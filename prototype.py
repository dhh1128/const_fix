import os, sys, re

from param import Param

_label_not_proto_pat = re.compile(r':[ \t\r]*\n')
_prototype_pat_template = r'^[ \t]*((?:[_a-zA-Z][_a-zA-Z0-9:]*)[^-;{}()=+!<>/|^]*?(?:\s+|\*|\?))(%s)\s*\(([^()]*?)\)(\s*const)?\s*([{;])'
# Matches lines like this: mock((void *)0, void *, __MRMQueryThread,(void *Args))
_old_mock_proto_pat_template = r'^\s*mock\s*\((.*?),\s*(.*?),\s*(%s)\s*,\s*\((.*?)\)\)\s*$'

# Matches lines like this: MOCK_CMETHOD4(int, MGEventItemIterate, mgevent_list_t *, char **, mgevent_obj_t **, mgevent_iter_t *);
_new_mock_cproto_pat_template = r'^\s*MOCK_CMETHOD\d\s*\(\s*(.*?)\s*,\s*(%s)\s*,\s*(.*?)\)\s*;\s*$'

# Matches lines like this: MOCK_METHOD4(MGEventItemIterate, int(mgevent_list_t *, char **, mgevent_obj_t **, mgevent_iter_t *));
_new_mock_cppproto_pat_template = r'^\s*MOCK_METHOD\d\s*\(\s*(%s)\s*,\s*([^(]+)\((.*?)\)\s*\)\s*;\s*$'

test_proto_pats = [_old_mock_proto_pat_template, _new_mock_cppproto_pat_template, _new_mock_cproto_pat_template]

def _pluralize(noun, count):
    if count == 1:
        return noun
    return noun + 's'

def _split_params(txt, i, end):
    '''
    Given a block of text that contains the parameter list for a func,
    make a list of params out of it. This is much harder than it sounds,
    because the param list might have comments scattered throughout.
    This is a common pattern in moab code; we use it to document IN
    and OUT parameters.
    '''
    params = []
    paren_count = 0
    param = None
    while i < end:
        # Each time through the loop, i is pointing at the first
        # char that might begin the next param. This char could prove
        # to be whitespace, a comment, or a meaningful token beginner.
        while txt[i].isspace() and i < end:
            i += 1
        if i == end:
            param.end = suffix = txt[begin:i]
            break
        else:
            begin = None
            cut_position = None
            while True:
                c = txt[i]
                if c == '/':
                    # If we haven't seen the actual param def yet, just skip comment.
                    # Otherwise, end our param def with this comment. This strategy
                    # is not guaranteed to be perfect -- we could have a comment right
                    # in the middle of a definition, such as:
                    #
                    #   int do_something(char /*comment*/ * xyz);
                    #
                    # However, it's close enough. If we misinterpret something, we'll
                    # just fail the compile and back out a change. Not the end of the
                    # world.
                    if begin is None:                            
                        two = txt[i:i+2]
                        if two == '/*':
                            i = txt.find('*/', i + 2)
                            assert i > -1
                        elif two == '//':
                            i = txt.find('\n', i + 2)
                            assert i > -1
                        else:
                            assert False
                    else:
                        if cut_position is None:
                            cut_position = i
                elif c == '(': # can happen if func ptr is a parameter type
                    if begin is None:
                        begin = i
                    paren_count += 1
                elif c == ')':
                    paren_count -= 1
                elif c == ',':
                    if paren_count == 0:
                        assert begin is not None
                        if cut_position:
                            i = cut_position
                        fragment = txt[begin:i].rstrip()
                        params.append(Param(begin, fragment))
                        begin = None
                        cut_position = None
                else:
                    if begin is None and (c.isalpha() or c == '_'):
                        begin = i
                i += 1
                if i == end:
                    if begin:
                        if cut_position:
                            i = cut_position
                        fragment = txt[begin:i].rstrip()
                        params.append(Param(begin, fragment))
                        break
    return params
    
def _find_end_of_body(txt, first_body_idx):
    '''find the curly brace that ends the body of a function'''
    in_quote = False
    curly_count = 1
    end = len(txt)
    i = first_body_idx
    while i < end:
        c = txt[i]
        if in_quote:
            if c == '\\':
                i += 1
            elif c == '"':
                in_quote = False
        else:
            if c == '{':
                curly_count += 1
            elif c == '}':
                curly_count -= 1
                if curly_count == 0:
                    return i
            elif c == '/':
                if txt[i + 1] == '*':
                    i = txt.find('*/', i + 2) + 1
                elif txt[i + 1] == '/':
                    i = txt.find('\n')
            elif c == '"':
                in_quote = True
        i += 1
    assert(False)
    
class Prototype:
    def __init__(self, fpath, txt, match):
        self.fpath = fpath
        self.txt = txt
        self.match = match
        x = self.txt[self.match.start():self.match.end() - 1]
        for i in xrange(len(x)):
            if not x[i].isspace():
                break
        self.indent = x[0:i]
        self.original = x[i:]
        self.start_of_body = None
        self.end_of_body = None
        self.return_type = match.group(1).strip()
        if match.group(5) == '{':
            self.start_of_body = match.end(5)
            self.end_of_body = _find_end_of_body(txt, self.start_of_body)
            self.original = self.original.rstrip()
        self.params = _split_params(txt, match.start(3), match.end(3))
        self.dirty = False
        
    @property
    def name(self):
        return self.match.group(2)
    
    def get_ideal(self):
        return '%s %s(%s)' % (self.return_type, self.match.group(2), ', '.join([str(p) for p in self.params]))
    
    def is_in_tests(self):
        return 'test/' in self.fpath
    
    def is_in_header(self):
        return self.fpath.endswith('.h')
    
    def is_in_impl(self):
        return self.fpath.endswith('.c')
    
    def is_const_candidate(self):
        if self.params:
            for p in self.params:
                if p.is_const_candidate():
                    return True
        return False
    
    def matches(self, other):
        if len(self.params) == len(other.params):
            if len(self.params) != len(other.params):
                return False
            for i in xrange(len(self.params)):
                type_a = self.params[i].data_type
                type_b = other.params[i].data_type
                if type_a != type_b:
                    return False
        return False
    
    def prove_param_cant_be_const(self, param_idx):
        if self.start_of_body:
            if len(self.params) > param_idx:
                param = self.params[param_idx]
                name = param.new_name
                if not name:
                    name = param.name
                if name:
                    i = param.get_pivot_point()
                    if i > -1:
                        pivot = param.data_type[i]
                        if pivot == '*':
                            operator = '->'
                        else:
                            assert pivot == '&'
                            operator = r'\.'
                        pat = re.compile(r'[^a-zA-Z0-9_]%s%s[a-zA-Z0-9_]+\s*(\+[+=]|-[-=]|=(?!=))' % (name, operator))
                        if pat.search(self.txt, self.start_of_body, self.end_of_body):
                            return True
                        if pivot == '*':
                            pat = re.compile(r'\*%s\s*(\+[+=]|-[-=]|=(?!=))' % name)
                            if pat.search(self.txt, self.start_of_body, self.end_of_body):
                                return True
                        else:
                            pat = re.compile(r'[^a-zA-Z0-9_]%s\s*(\+[+=]|-[-=]|=(?!=))' % name)
                            if pat.search(self.txt, self.start_of_body, self.end_of_body):
                                return True
        return False
    
def find_prototypes_in_file(func, fpath):
    with open(fpath, 'r') as f:
        txt = f.read()
    i = func.find('(')
    if i > -1:
        func = func[:i]
    # Do quick sanity check first.
    i = txt.find(func)
    if i == -1:
        return
    expr = re.compile(_prototype_pat_template % func, re.MULTILINE)
    protos = []
    for m in expr.finditer(txt):
        if not _label_not_proto_pat.search(m.group(1)):
            protos.append(Prototype(fpath, txt, m))
    if 'test/' in fpath:
        test_pats = [re.compile(pat % func, re.DOTALL | re.MULTILINE) for pat in test_proto_pats]
        for pat in test_pats:
            for m in pat.finditer(txt):
                protos.append(Prototype(fpath, txt, m))
    return protos

def find_prototypes_in_codebase(func, root, files=None):
    prototypes = PrototypeMap()
    if files:
        for f in files:
            prototypes[fpath] = find_prototypes_in_file(f)
    else:
        for root, dirs, files in os.walk(root):
            skip = [d for d in dirs if d.startswith('.')]
            for d in skip:
                dirs.remove(d)
            for f in files:
                if (not f.startswith('.')) and (f.endswith('.h') or f.endswith('.c')):
                    fpath = os.path.join(root, f)
                    in_this_file = find_prototypes_in_file(func, fpath)
                    if in_this_file:
                        prototypes[fpath] = in_this_file
    if prototypes:
        count = len(prototypes)
        print('  Found %d %s.' % (count, _pluralize('prototype', count)))
    return prototypes

class PrototypeMap(dict):
    @property
    def function_name(self):
        for fpath in self:
            for proto in self[fpath]:
                return proto.name
    
    def non_test_fpaths(self):
        for fpath in self:
            if 'test/' not in fpath:
                yield fpath
                
    def non_test_prototypes(self):
        for fpath in self.non_test_fpaths():
            for proto in self[fpath]:
                yield proto
                
    def dirty_fpaths(self):
        for fpath in self:
            is_dirty = False
            for proto in self[fpath]:
                if proto.dirty:
                    is_dirty = True
                    break
            if is_dirty:
                yield fpath

    def find_best(self):
        '''
        Given a dict of file path --> list of self in that file, find the best
        prototype to use as a starting point for modification.
        '''
        # By preference, choose a prototype that's in a .c, has a body (instead of being
        # a declaration only), and that isn't in our tests. This gives us the best chance
        # of starting from a prototype that has named parameters. A second best alternative
        # would be a declaration in a non-test .c file.
        second_best = None
        for fpath, protos in self.iteritems():
            if fpath.endswith('*.c') and 'test/' not in fpath:
                for p in protos:
                    if p.start_of_body:
                        return p
                    else:
                        second_best = p
        if second_best:
            return second_best
        # Failing that, look for a prototype in a header, but not in tests. In case the
        # function is declared multiple times in headers, pick the version that has an
        # inline impl by preference.
        for fpath, protos in self.iteritems():
            if fpath.endswith('*.h') and 'test/' not in fpath:
                for p in protos:
                    if p.start_of_body:
                        return p
                    else:
                        second_best = p
        if second_best:
            return second_best
        # Failing that, pick the first prototype.
        for protos in self.values():
            for p in protos:
                return p
