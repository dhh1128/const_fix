import os, sys, re, weakref
import callgraph

comment_pat = re.compile(r'/\*.*?\*/')
datatype_names = 'int|short|long|double|float|char|bool'.split('|')
moab_type_pat = re.compile('.*\Wm[a-z_0-9]+_t$')
const_prefix_pat = re.compile('^const ([a-zA-Z0-9_]+)(.*)$')
const_suffix_pat = re.compile('([a-zA-Z0-9_]+)\s+const.*')

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

def split_args(arglist):
    '''
    Given a block of text that contains the parameter list for a func,
    make a list of args out of it.'''
    args = []
    paren_count = 0
    start = 0
    arglist = arglist.strip()
    if arglist:
        for i in xrange(len(arglist)):
            c = arglist[i]
            if c == ',' and paren_count == 0:
                arg = squeeze(arglist[start:i])
                assert(arg)
                args.append(arg)
                start = i + 1
            elif c == '(':
                paren_count += 1
            elif c == ')':
                paren_count -= 1
        arg = squeeze(arglist[start:])
        assert(arg)
        args.append(arg)
    return args
    
def cut_cpp_comments(txt):
    '''remove c++-style comments from a block of text'''
    args = arglist.split('\n')
    for n in xrange(len(args)):
        arg = args[n]
        k = arg.find('//')
        if k > -1:
            arg = arg[0:k]
            args[n] = arg
    return args.join('\n')

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

class Prototype:
    def __init__(self, fpath, txt, match):
        self.fpath = fpath
        self.txt = txt
        self.match = match
        self.original = self.txt[self.match.start():self.match.end()]
        self.body = None
        if match.group(2) == '{':
            self.body = find_end_of_body(txt, match.end(2))
        # Find boundaries of arg list
        i = txt.find('(', match.end(1)) + 1
        j = txt.rfind(')', match.end())
        arglist = txt[i:j]
        # Remove comments that might confuse us
        arglist = comment_pat.sub('', arglist)
        arglist = cut_cpp_comments(arglist)
        # Now split what's left. We can't just split on commas in
        # a simplistic way, since some args might be
        # function prototypes themselves, containing embedded commas
        # of their own...
        self.args = split_args(arglist)
    def matches(self, other):
        if len(self.args) == len(other.args):
            for i in xrange(len(self.args)):
                type_a = self.get_arg_type(i)
                type_b = self.
    def arg_is_const_candidate(self, i):
        arg = self.args[i]
        return arg.find['*'] > -1 or arg.find['&'] > -1
    def get_arg_type(self, i):
        name = self.get_arg_name(i)
        typ = self.args[i]
        if name:
            j = typ.rfind(name)
            typ = typ[0:j]
        return typ.strip()
    def get_arg_name(self, i):
        arg = self.args[i]
        if arg.endswith('*') or arg.endswith('&') or moab_type_pat.match(arg):
            return
        j = arg.rfind(' ')
        if j == -1:
            return
        name = arg[j+1:]
        if name.startswith('*') or name.startswith('&'):
            name = name[1:]
        if name in datatype_names:
            return
        if name.endwith(']'):
            name = name[0:name.rfind('[')]
        return name

def update_param_names(prototypes):
    find_best_names_for_each_param(prototypes)

def find_prototypes_in_file(func, fpath):
    with open(fpath, 'r') as f:
        txt = f.read()
    # Do quick sanity check first.
    i = txt.find(func)
    if i == -1:
        return
    expr = re.compile(START_LINE + RETURN_TYPE + '(' + func + ')' + ARGS + '(;|{)')
    protos = []
    for m in expr.finditer(txt):
        protos.append(Prototype(fpath, txt, m))
    return protos

def find_prototypes_in_codebase(func, root):
    prototypes = {}
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
    return prototypes

def fix_func(func):
    prototypes = get_prototypes(func)
    if update_param_names(prototypes):
        if not compile_is_clean():
            revert()
    impl = find_impl(prototypes)
    if const_matters_to_func(impl):
        for param in impl.params:
            recompile = False
            if const_matters_to_param(param):
                if is_const(param):
                    recompile = True
                    
            elif is_const(param):
                recompile = True
                remove_const(param)
            else:
                recompile = False
                
class CallGraphNode:
    def __init__(self, parent=None):
        self.children = None
        if parent:
            self.parent = weakref.ref(parent)
        else:
            self.parent = None
    def is_leaf(self):
        return not self.children
    def get_leaves(self):
        leaves = []
        if self.children:
            for child in self.children:
                if child.is_leaf():
                    leaves.append(child)
                else:
                    leaves.extend(child.get_leaves())
        return leaves
    def remove_leaves(self):
        if self.children:
            to_remove = []
            for child in self.children:
                if child.is_leaf():
                    to_remove.append(child)
                else:
                    child.remove_leaves()
            for child in to_remove:
                self.children.remove(child)
            
def fix_prototypes(root):
    root = os.path.normpath(os.path.abspath(root))
    if not os.path.isfile(os.path.join(root, 'Makefile')):
        sys.stderr.write('Folder %s is not the root of a codebase.\n' % root)
        return 1
    if not code_compiles(root):
        sys.stderr.write('Code in %s does not compile cleanly. Prototype fixup aborted.\n' % root)
        return 1
    if not tests_pass(root):
        sys.stderr.write('Code in %s does not compile cleanly. Prototype fixup aborted.\n' % root)
        return 1
    funcs = load_call_graph(root)
    while call_graph:
        for func in call_graph.leaves():
            fix_func(func)
        call_graph.remove_leaves()

if __name__ == '__main__':
    if len(sys.argv) > 1:
        folder = sys.argv[1]
    else:
        folder = '.'
    sys.exit(fix_prototypes(folder))