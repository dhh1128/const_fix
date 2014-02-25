import os, sys, re, weakref
import callgraph

outcomes_log = 'const-outcomes.txt'
compile_log = '/tmp/make.log'
compile_cmd = 'make -j6 >%s 2>&1' % compile_log
compile_tests_cmd = 'scons -n -j6 >%s 2>&1' % compile_log
make_clean_cmd = 'make clean >/dev/null 2>&1'
clean_tests_cmd = 'scons -c'
test_log = '/tmp/test.log'
test_cmd = 'scons -j6 >%s 2>&1' % test_log
comment_pat = re.compile(r'/\*.*?\*/')
datatype_names = 'int|short|long|double|float|char|bool'.split('|')
moab_type_pat = re.compile('.*\Wm[a-z_0-9]+_t$')
const_prefix_pat = re.compile('^const ([a-zA-Z0-9_]+)(.*)$')
const_suffix_pat = re.compile('([a-zA-Z0-9_]+)\s+const.*')
array_spec_pat = re.compile('.*(\[^]]\])$')
prototype_pat_template = r'^\s*((?:[_a-zA-Z][_a-zA-Z0-9:]*)[^-()=+!<>/|^]*?(?:\s+|\*|\?))(%s)\s*\(([^()]*?)\)(\s*const)?\s*([{;])'

# Matches lines like this: mock((void *)0, void *, __MRMQueryThread,(void *Args))
old_mock_proto_pat_template = r'^\s*mock\s*\((.*?),\s*(.*?),\s*(%s)\s*,\s*\((.*?)\)\)\s*$'

# Matches lines like this: MOCK_CMETHOD4(int, MGEventItemIterate, mgevent_list_t *, char **, mgevent_obj_t **, mgevent_iter_t *);
new_mock_cproto_pat_template = r'^\s*MOCK_CMETHOD\d\s*\(\s*(.*?)\s*,\s*(%s)\s*,\s*(.*?)\)\s*;\s*$'

# Matches lines like this: MOCK_METHOD4(MGEventItemIterate, int(mgevent_list_t *, char **, mgevent_obj_t **, mgevent_iter_t *));
new_mock_cppproto_pat_template = r'^\s*MOCK_METHOD\d\s*\(\s*(%s)\s*,\s*([^(]+)\((.*?)\)\s*\)\s*;\s*$'

test_proto_pats = [old_mock_proto_pat_template, new_mock_cppproto_pat_template, new_mock_cproto_pat_template]

def run(cmd):
    print('  ' + cmd)
    return os.system(cmd)

def squeeze(txt):
    '''Replace all runs of whitepace with a single space, and trim front and back.'''
    txt = txt.strip().replace('\r', ' ').replace('\n', ' ').replace('\t', ' ')
    while txt.find('  ') > -1:
        txt = txt.replace('  ', ' ')
    return txt
    
def normalize_type(typ):
    '''
    Put the type portion of a parameter declaration into normalized
    form so it can be compared.
      const char* --> char const *
      mjob_t  & --> mjob_t &
    '''
    typ = squeeze(typ.replace('*', ' * ').replace('&', ' & '))
    m = const_prefix_pat.match(typ)
    if m:
        typ = '%s const%s' % (m.group(1), m.group(2))
    return typ

def split_params(param_list):
    '''
    Given a block of text that contains the parameter list for a func,
    make a list of params out of it.'''
    params = []
    paren_count = 0
    start = 0
    param_list = param_list.strip()
    if param_list:
        for i in xrange(len(param_list)):
            c = param_list[i]
            if c == ',' and paren_count == 0:
                param = squeeze(param_list[start:i])
                assert(param)
                params.append(param)
                start = i + 1
            elif c == '(':
                paren_count += 1
            elif c == ')':
                paren_count -= 1
        param = squeeze(param_list[start:])
        assert(param)
        params.append(param)
        params = [Param(p) for p in params]
    return params
    
def cut_cpp_comments(txt):
    '''remove c++-style comments from a block of text'''
    args = txt.split('\n')
    for n in xrange(len(args)):
        arg = args[n]
        k = arg.find('//')
        if k > -1:
            arg = arg[0:k]
            args[n] = arg
    return '\n'.join(args)

def find_end_of_body(txt, first_body_idx):
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
    
class Param:
    def __init__(self, decl):
        self.decl = decl
        self.array_spec = None
        self.data_type = None
        self.name = None
        self._parse()
    def is_const_candidate(self):
        dt = self.data_type
        i = dt.find('*')
        j = dt.find('&')
        # Params that are not pointers or references are passed by value,
        # so their constness is irrelevant.
        if i == -1 and j == -1:
            return False
        # Params that are *& are virtually guaranteed to be OUT params,
        # so their constness should not be adjusted.
        if i > -1 and j > -1:
            return False
        # Same for **.
        if i > -1 and i < dt.rfind('*'): 
            return False
        return True
    def is_const(self):
        return 'const' in self.decl
    def get_pivot_point(self):
        i = self.data_type.find('*')
        j = self.data_type.find('&')
        if i > -1:
            if j > -1:
                return min(i, j)
            return i
        elif j > -1:
            return j
    def set_const(self, value):
        i = self.get_pivot_point()
        if value:
            if not self.is_const():
                self.data_type = self.data_type[:i].rstrip() + ' const ' + self.data_type[i:]
        elif self.is_const():
            self.data_type = re.sub(r'\s{2,}', ' ', self.data_type.replace('const', ''))            
    def _parse(self):
        decl = squeeze(self.decl)
        m = array_spec_pat.match(decl)
        if m:
            self.array_spec = m.group(1).replace(' ', '')
            decl = decl[0:m.start(1)]
        name_idx = -1
        if not (decl.endswith('*') or decl.endswith('&') or moab_type_pat.match(decl)):
            i = decl.rfind(' ')
            if i > -1:
                name_idx = i + 1
                while decl[name_idx] == '*' or decl[name_idx] == '&':
                    name_idx += 1
                if name in datatype_names:
                    name_idx = -1
        if name_idx > -1:
            self.data_type = decl[0:name_idx].rstrip()
            self.name = decl[name_idx:]
        else:
            self.data_type = decl
        self.data_type = normalize_type(self.data_type)
    def __str__(self):
        if self.name:
            return self.data_type + ' ' + self.name + self.array_spec
        elif self.array_spec:
            return self.data_type + self.array_spec
        else:
            return self.data_type

class Prototype:
    def __init__(self, fpath, txt, match):
        self.fpath = fpath
        self.txt = txt
        self.match = match
        self.original = self.txt[self.match.start():self.match.end() - 1]
        self.body = None
        self.return_type = match.group(1).strip()
        if match.group(5) == '{':
            self.body = find_end_of_body(txt, match.end(5))
            self.original = self.original.rtrim()
        # Find boundaries of param list
        param_list = match.group(3)
        # Remove comments that might confuse us.
        param_list = comment_pat.sub('', param_list)
        param_list = cut_cpp_comments(param_list)
        # Now split what's left. We can't just split on commas in
        # a simplistic way, since some args might be
        # function prototypes themselves, containing embedded commas
        # of their own...
        self.params = split_params(param_list)
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

def update_param_names(prototypes):
    return False
    if len(prototypes) < 2:
        return False
    best = []
    for i in xrange(len(prototypes)):
        best[i] = ''
    # Figure out which version of this parameter, across all prototypes,
    # has the longest name. We're going to assume that the longest name
    # is the best one. Not 100% true, I know, but a good approximation.
    for proto in prototypes:
        i = 0
        for p in proto.params:
            if p.name:
                if best[i]:
                    if len(best[i]) < len(p.name):
                        best[i] = p.name
                else:
                    best[i] = p.name
            i += 1
    # Now see which prototypes need to be updated with better names.
    change_count = 0
    for proto in prototypes:
        proto.dirty = False
        i = 0
        for p in proto.params:
            if p.name != best[i]:
                p.new_name = best[i]
                proto.dirty = True
            i += 1
        if proto.dirty:
            change_count += 1
    # Now tweak all prototypes to use the best param names.
    # We do it this way, instead of just generating a perfect
    # prototype and inserting it where the old prototype used
    # to be, so we can preserve unique comments, line spacing, and
    # indents in each prototype.
    if changed_count:
        for proto in prototypes:
            if proto.dirty:
                bkpath, txt = backup_file(proto.fpath)
                for param in proto.params:
                    # TODO: this needs to be fixed. Won't work as-is, because our decls have
                    # been squeezed and idealized already. Besides, this searches entire file
                    # instead of the block of text where the parameters live.
                    txt = re.sub(r'([^a-zA-Z0-9_])%s([^a-zA-Z0-9_])' % param.decl, param.ideal_version(), txt)
                    # If we have a named param and a function body, update all usage of the name
                    # inside the function.
                    if param.name and proto.body:
                        txt = re.sub(r'([^a-zA-Z0-9_])%s([^a-zA-Z0-9_])' % param.name, param.new_name, txt)
                overwrite_file(proto.fpath, txt)
    return changed_count > 0
                
def _name_to_backup_name(fname):
    assert not fname.startswith('.')
    return '.' + fname + '.bak'

def _backup_name_to_name(fname):
    assert fname.startswith('.')
    return fname[1:].replace('.bak', '')

def _backup_or_restore_file(fpath, name_func):
    folder, fname = os.path.split(fpath)
    new_name = os.path.join(folder, name_func(fname))
    with open(fpath, 'r') as f:
        txt = f.read()
    if os.path.isfile(new_name):
        os.remove(new_name)
    with open(new_name, 'w') as f:
        f.write(txt)
        
def backup_file(fpath):
    _backup_or_restore_file(fpath, _name_to_backup_name)
    
def restore_file(fpath):
    folder, fname = os.path.split(fpath)
    fpath = os.path.join(folder, _name_to_backup_name(fname))
    _backup_or_restore_file(fpath, _backup_name_to_name)

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
    expr = re.compile(prototype_pat_template % func, re.MULTILINE)
    protos = []
    for m in expr.finditer(txt):
        protos.append(Prototype(fpath, txt, m))
    if '/test/' in fpath:
        test_pats = [re.compile(pat % func, re.DOTALL | re.MULTILINE) for pat in test_proto_pats]
        for pat in test_pats:
            for m in pat.finditer(txt):
                protos.append(Prototype(fpath, txt, m))
    return protos

def find_prototypes_in_codebase(func, root, files=None):
    prototypes = {}
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
        print('found %d prototypes for %s' % (len(prototypes), func))
    return prototypes

def tests_pass(root):
    print('Testing...')
    oldcwd = os.getcwd()
    try:
        os.chdir(os.path.join(root, 'test'))
        exitcode = run(test_cmd)
        if exitcode:
            print('Tests failed. See %s for details.\n' % test_log)
        else:
            print('Tests pass.\n')
        return exitcode == 0
    finally:
        os.chdir(oldcwd) 

def compile_is_clean(root):
    print('Compiling...')
    oldcwd = os.getcwd()
    try:
        os.chdir(root)
        exitcode = run(compile_cmd)
        if exitcode:
            print('  Incremental compile failed. Trying to clean.')
            run(make_clean_cmd)
            exitcode = run(compile_cmd)
        if not exitcode:
            os.chdir(os.path.join(root, 'test'))
            run(compile_tests_cmd)
            if exitcode:
                print('  Incremental compile of tests failed. Trying to clean.')
                run(clean_tests_cmd)
                exitcode = run(compile_tests_cmd)
        if exitcode:
            print('Clean compile failed. See %s for details.\n' % compile_log)
        else:
            print('Compile succeeded.\n')
        return exitcode == 0
    finally:
        os.chdir(oldcwd) 

def find_best_prototype(prototypes):
    '''
    Given a dict of file path --> list of prototypes in that file, find the best
    prototype to use as a starting point for modification.
    '''
    # By preference, choose a prototype that's in a .c, has a body (instead of being
    # a declaration only), and that isn't in our tests. This gives us the best chance
    # of starting from a prototype that has named parameters. A second best alternative
    # would be a declaration in a non-test .c file.
    second_best = None
    for fpath, protos in prototypes.iteritems():
        if fpath.endswith('*.c') and 'test/' not in fpath:
            for p in protos:
                if p.body:
                    return p
                else:
                    second_best = p
    if second_best:
        return second_best
    # Failing that, look for a prototype in a header, but not in tests. In case the
    # function is declared multiple times in headers, pick the version that has an
    # inline impl by preference.
    for fpath, protos in prototypes.iteritems():
        if fpath.endswith('*.h') and 'test/' not in fpath:
            for p in protos:
                if p.body:
                    return p
                else:
                    second_best = p
    if second_best:
        return second_best
    # Failing that, pick the first prototype.
    for protos in prototypes.values():
        for p in protos:
            return p
        
def rewrite_prototypes(prototypes, param_idx, param):
    for fpath in prototypes:
        backup_file(fpath)
        with open(fpath, 'r') as f:
            txt = f.read()
        offsets = [p.match.start() for p in prototypes[fpath]]
        i = 0
        for p in prototypes[fpath]:
            p.params[param_idx].data_type = param.data_type
            old_len = len(p.original)
            new_prototype = p.get_ideal()
            new_len = len(new_prototype)
            txt = txt[0:offsets[i]] + new_prototype + txt[offsets[i] + old_len:]
            delta_len = new_len - old_len
            if delta_len:
                for j in xrange(i + 1, len(offsets)):
                    offsets[j] += delta_len
            i += 1
        with open(fpath, 'w') as f:
            f.write(txt)
            
def _pluralize(noun, count):
    if count == 1:
        return nount
    return noun + 's'

def fix_func(func, root, cg):
    # Locate every place where this function's prototype appears.
    # In some cases, the prototype might be followed by a body; in most cases, not.
    prototypes = find_prototypes_in_codebase(func, root)
    # Change our code so function declarations display names for parameters.
    # What we have in the code today, in headers, is often something like this:
    #
    #   int do_something(mjob_t *, char *, int, int, void *);
    #
    # This is an antipattern. You're supposed to be able to read a prototype and
    # know how to use the function. So we're going to change the prototypes to
    # look like this:
    #
    #   int do_something(mjob_t * job, char * buf, int size, int level, void * state);
    if len(prototypes) > 1 and update_param_names(prototypes):
        if not compile_is_clean():
            revert()
        else:
            # Reload prototypes from just the files that we modified.
            x = {}
            for p in prototypes:
                x[p.fpath] = 1
            prototypes = find_prototypes_in_codebase(func, root, x.keys())
            
    # Find the version of the prototype that's associated with the main implementation
    # of the function (not the one in scaffolding.c).
    impl = find_best_prototype(prototypes)
    
    if impl.is_const_candidate():
        change_count = 0
        print('Checking %s.' % impl.get_ideal())
        param_idx = 0
        for param in impl.params:
            original_state = None
            if param.is_const_candidate():
                if not param.is_const():
                    original_state = False
                    param.set_const(True)
            elif param.is_const():
                original_state = True
                param.set_const(False)
            if original_state is not None:
                rewrite_prototypes(prototypes, param_idx, param)
                if not compile_is_clean(root) or not tests_pass(root):
                    print("%s doesn't work; backing out change." % impl.get_ideal())
                    param.set_const(False)
                    for fpath in prototypes:
                        restore_file(fpath)
                    if not compile_is_clean(root) or not tests_pass(root):
                        print('Unable to get back to a clean state; exiting prematurely.')
                        sys.exit(1)
                else:
                    print("%s works; keeping change." % impl.get_ideal())
                    change_count += 1
            param_idx += 1
        print('%d %s made.' % (change_count, _pluralize('change', change_count)))
        tags = str(change_count)
        if change_count:
            tags += ' --> ' + impl.get_ideal()
        tabulate(impl.name, tags)
                
CONST_IRRELEVANT = 0
CONST_MATTERS = 1
OBNOXIOUS_CONST = 2
classify_labels = ['CONST_IRRELEVANT', 'CONST_MATTERS', 'OBNOXIOUS_CONST']

def _classify_func(params):
    cls = CONST_IRRELEVANT
    if params:
        for p in params:
            if ('*' in p or '&' in p) and ('const' not in p):
                return CONST_MATTERS
            elif 'const' in p:
                cls = OBNOXIOUS_CONST
    return cls

def verify_clean():
    ok = True
    if False:
        print('Verifying that codebase is clean before we start...\n')
        if not compile_is_clean(root):
            ok = False
        if not tests_pass(root):
            ok = False
        if not ok:
            sys.stderr.write('Prototype fixup in %s aborted.\n' % root)
            sys.exit(1)
    else:
        print('Skipping initial verification; please re-enable later in script.')
        
def verify_makefile(root):
    if not os.path.isfile(os.path.join(root, 'Makefile')):
        sys.stderr.write('Did not find Makefile at root of codebase %s.\n' % root)
        sys.exit(1)
        
def cut_noise(cg):
    print('Eliminating functions where const issues are irrelevant...')
    cuttable = []
    for func in cg.by_caller:
        params = cg.get_params(func)
        cls = _classify_func(params)
        if cls == CONST_IRRELEVANT:
            cuttable.append(func)
    with open(outcomes_log, 'a') as f:
        lbl = classify_labels[CONST_IRRELEVANT]
        for func in cuttable:
            f.write('%s\t%s\n' % (func, lbl))
            cg.remove(func)
    print('Reduced function count from %d to %d.' % (len(cg.by_caller) + len(cuttable), len(cg.by_caller)))
    
def prune(cg):
    to_prune = []
    for func in cg.by_caller:
        params = cg.get_params(func)
        cls = _classify_func(params)
        if cls != CONST_MATTERS:
            tabulate(func, classify_labels[cls])
            to_prune.append(func)
    print('pruning %d items: %s' % (len(to_prune), to_prune))
    for item in to_prune:
        cg.remove(item)
        
def tabulate(func, tags):
    if not hasattr(tags, 'upper'):
        tags = str(tags)[1:-1].replace(',', '')
    with open(outcomes_log, 'a') as f:
        f.write('%s\t%s\n' % (func, tags))

def fix_prototypes(root):
    print('')    
    root = os.path.normpath(os.path.abspath(root))
    verify_makefile(root)
    verify_clean()
    
    print('Loading call graph...')
    cg = callgraph.Callgraph(root)    
    cut_noise(cg)
        
    tried_to_prune = False
    pass_number = 0
    while not cg.is_empty():
        pass_number += 1
        leaves = cg.get_leaves()
        print('\nPass %d: %d leaves out of %d functions ----------------\n' % (pass_number, len(leaves), len(cg.by_callee)))
        if len(leaves) == 0:
            # See if we can prune some stuff away by finding functions where const doesn't matter.
            if tried_to_prune:
                print('Stuck; no functions are leaves.')
                sys.exit(1)
            else:
                tried_to_prune = True
                prune(cg)
                continue
        else:
            tried_to_prune = False
        for func in leaves:
            callers = None
            if func in cg.by_callee:
                callers = cg.by_callee[func]
            if not callers:
                print('%s appears to be an orphan, never called.' % func)
            else:
                pass #print('%s called by %s' % (func, callers))
            params = cg.get_params(func)
            cls = _classify_func(params)
            if cls == CONST_MATTERS:
                print('Experimenting with changes to %s...' % func)
                fix_func(func, root, cg)
                sys.exit(0)
            elif cls == OBNOXIOUS_CONST:
                print('%s should not use const, but does.' % func)
            else:
                print("Constness is not relevant to %s." % func)
            cg.remove(func)

if __name__ == '__main__':
    if len(sys.argv) > 1:
        folder = sys.argv[1]
    else:
        folder = '.'
    sys.exit(fix_prototypes(folder))