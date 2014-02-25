import os, sys, re, weakref

import callgraph
from prototype import Prototype
from safechange import *

outcomes_log = 'const-outcomes.txt'
compile_log = '/tmp/make.log'
compile_cmd = 'make -j6 >%s 2>&1' % compile_log
compile_tests_cmd = 'scons -n -j6 >%s 2>&1' % compile_log
make_clean_cmd = 'make clean >/dev/null 2>&1'
clean_tests_cmd = 'scons -c'
test_log = '/tmp/test.log'
test_cmd = 'scons -j6 >%s 2>&1' % test_log
datatype_names = 'int|short|long|double|float|char|bool'.split('|')
moab_type_pat = re.compile('.*\Wm[a-z_0-9]+_t$')
const_suffix_pat = re.compile('([a-zA-Z0-9_]+)\s+const.*')
array_spec_pat = re.compile('.*(\[^]]\])$')
prototype_pat_template = r'^[ \t]*((?:[_a-zA-Z][_a-zA-Z0-9:]*)[^-;()=+!<>/|^]*?(?:\s+|\*|\?))(%s)\s*\(([^()]*?)\)(\s*const)?\s*([{;])'
const_error_pat_template = r'In function [^(]+ %s\s*\(.*?error: assignment of member ‘[^‘]+’ in read-only object'

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

def improve_param_names(root, prototypes):
    '''
    Moab's codebase has an antipattern where parameters are only named in the impl of a
    function, not its declaration. This makes it necessary to look up the impl to know
    how to call a function properly.
    
    Try to rectify this problem by scanning all prototypes for a given function, and
    giving each parameter the most meaningful name we can find.
    
    Return True if changes were made and the code compiled successfully afterward.
    '''
    if len(prototypes) < 2:
        return False
    
    best = None        
    # Figure out which version of this parameter, across all prototypes,
    # has the longest name. We're going to assume that the longest name
    # is the best one. Not 100% true, I know, but a good approximation.
    for fpath in prototypes:
        if 'test/' not in fpath:
            prototypes_in_this_file = prototypes[fpath]
            if not best:
                best = ['' for i in xrange(len(prototypes_in_this_file[0].params))]
            for proto in prototypes_in_this_file:
                i = 0
                for param in proto.params:
                    if param.name:
                        if best[i]:
                            if len(best[i]) < len(param.name):
                                best[i] = param.name
                        else:
                            best[i] = param.name
                    i += 1
    
    # On off chance that we have a func that's only in the test folder...
    if not best:
        return False

    # Now see which prototypes need to be updated to reflect the best names.
    func_name = None
    change_count = 0
    for fpath in prototypes:
        if 'test/' not in fpath:
            for proto in prototypes[fpath]:
                if func_name is None:
                    func_name = proto.name
                proto.dirty = False
                i = 0
                for param in proto.params:
                    if param.name != best[i]:
                        param.new_name = best[i]
                        proto.dirty = True
                    i += 1
                if proto.dirty:
                    change_count += 1
    
    if change_count:
        print("  Rewriting prototypes to include better param names.")
        # Make the changes and see if they are okay. One possible reason why these
        # changes could fail is that a parameter isn't used, so adding a name for
        # it causes a warning.
        rewrite_prototypes(prototypes)
        if prove_safe_change(root, param_name_rollback(prototypes), func_name):
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
        print('  Found %d %s.' % (len(prototypes), _pluralize('prototype', len(prototypes))))
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
        
def get_compile_log_tail():
    with open(compile_log, 'r') as f:
        txt = f.read()
    if len(txt) > 2000:
        txt = txt[:-2000]
    return txt

def compile_is_clean(root, changed_func=None):
    print('Compiling...')
    oldcwd = os.getcwd()
    try:
        os.chdir(root)
        exitcode = run(compile_cmd)
        if exitcode:
            dont_bother_with_clean = False
            if changed_func:
                tail = get_compile_log_tail()
                pat = const_error_pat_template % changed_func
                if pat.search(tail):
                    dont_bother_with_clean = True
            if dont_bother_with_clean:
                print('  Compile failed due to const error.')
                return False
            else:
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
        
def rewrite_prototypes(prototypes):
    for fpath in prototypes:
        file_is_dirty = False
        for proto in prototypes[fpath]:
            if proto.dirty:
                file_is_dirty = True
                break
        if file_is_dirty:
            backup_file(fpath)
            with open(fpath, 'r') as f:
                txt = f.read()
            offsets = [p.match.start() for p in prototypes[fpath]]
            i = 0
            for p in prototypes[fpath]:
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
        return noun
    return noun + 's'

def prove_safe_change(root, undo_func, changed_func):
    if not compile_is_clean(root) or not tests_pass(root):
        undo_func()
        print("  Change doesn't work; backing it out.")
        for fpath in prototypes:
            restore_file(fpath)
        if not compile_is_clean(root, changed_func) or not tests_pass(root):
            print('Unable to get back to a clean state; exiting prematurely.')
            sys.exit(1)
    else:
        print("  %s works; keeping change." % impl.get_ideal())
        
def fix_func(func, root, cg, tags):
    # Locate every place where this function's prototype appears.
    # In some cases, the prototype might be followed by a body; in most cases, not.
    prototypes = find_prototypes_in_codebase(func, root)
    
    # We may be able to improve the code by copying param names into places that
    # don't have them.
    improve_param_names(root, prototypes)
            
    # Find the version of the prototype that's associated with the main implementation
    # of the function (not the one in test scaffolding).
    impl = find_best_prototype(prototypes)
    
    if impl.is_const_candidate():
        change_count = 0
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
                for fpath in prototypes:
                    for proto in prototypes[fpath]:
                        proto.params[param_idx].data_type = param.data_type
                        proto.dirty = True
                rewrite_prototypes(prototypes)
                if prove_safe_change(root, const_rollback(param, prototypes, param_idx), impl.name):
                    change_count += 1
            param_idx += 1
        print('%d %s made.' % (change_count, _pluralize('change', change_count)))
        tags += str(change_count)
        if change_count:
            tags += ' --> ' + impl.get_ideal()
        tabulate(impl.name + '()', tags)
                
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

def verify_clean(root):
    ok = True
    print('Verifying that codebase is clean before we start...\n')
    if not compile_is_clean(root):
        ok = False
    elif not tests_pass(root):
        ok = False
    if not ok:
        sys.stderr.write('Prototype fixup in %s aborted.\n' % root)
        sys.exit(1)
        
def verify_makefile(root):
    if not os.path.isfile(os.path.join(root, 'Makefile')):
        sys.stderr.write('Did not find Makefile at root of codebase %s.\n' % root)
        sys.exit(1)
        
def cut_noise(cg, previously_analyzed):
    if previously_analyzed:
        print('Eliminating previously analyzed functions...')
        for func in previously_analyzed:
            cg.remove(func)
        print('Reduced function count from %d to %d.' % (len(cg.by_caller) + len(previously_analyzed), len(cg.by_caller)))            
    
    cuttable = []
    for func in cg.by_caller:
        params = cg.get_params(func)
        cls = _classify_func(params)
        if cls == CONST_IRRELEVANT:
            cuttable.append(func)
    if cuttable:
        print('Eliminating functions where const issues are irrelevant...')
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
        
def load_previous_results():
    previously_analyzed = []
    if os.path.isfile(outcomes_log):
        with open(outcomes_log, 'r') as f:
            lines = f.readlines()
        previously_analyzed = [x[:x.find('\t')] for x in lines if x.find('\t') > -1]
    print('\nFound %d previously analyzed %s.' % (len(previously_analyzed), _pluralize('function', len(previously_analyzed))))
    return previously_analyzed

def fix_prototypes(root):
    print('')    
    root = os.path.normpath(os.path.abspath(root))
    
    global outcomes_log
    outcomes_log = os.path.join(root, outcomes_log)
    
    verify_makefile(root)
    verify_clean(root)
    
    print('Loading call graph...')
    cg = callgraph.Callgraph(root)
    
    previously_analyzed = load_previous_results()
    cut_noise(cg, previously_analyzed)
        
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
            tags = ''
            callers = None
            if func in cg.by_callee:
                callers = cg.by_callee[func]
            if not callers:
                print('%s appears to be an orphan, never called.' % func)
                tags += 'ORPHAN '
            else:
                pass #print('%s called by %s' % (func, callers))
            params = cg.get_params(func)
            cls = _classify_func(params)
            if cls == CONST_MATTERS:
                print('Experimenting with changes to %s...' % func)
                fix_func(func, root, cg, tags)
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