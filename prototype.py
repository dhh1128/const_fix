import sys, re

from param import Param

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
                    if begin is None:
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
    curly_count = 1
    for i in xrange(first_body_idx, len(txt)):
        c = txt[i]
        if c == '{':
            curly_count += 1
        elif c == '}':
            curly_count -= 1
            if curly_count == 0:
                return i
    sys.stderr.write('Could not find end of function.')
    
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
        self.body = None
        self.return_type = match.group(1).strip()
        if match.group(5) == '{':
            self.body = _find_end_of_body(txt, match.end(5))
            self.original = self.original.rstrip()
        self.params = _split_params(txt, match.start(3), match.end(3))
        
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
        if len(self.args) == len(other.args):
            for i in xrange(len(self.args)):
                type_a = self.get_arg_type(i)
                type_b = self.get_arg_type(i)
                if type_a != type_b:
                    return False
        else:
            return False
        return True

class AllPrototypes(dict):
    @property
    def function_name(self):
        for fpath in self:
            for proto in self[fpath]:
                return proto.name
    
    def non_tests(self):
        for fpath in self:
            if 'test/' not in fpath:
                yield fpath
        