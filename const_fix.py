# -*- coding: utf-8 -*-
import os, sys, re, traceback

import callgraph
from prototype import *
from safechange import *

outcomes_log = 'const-outcomes.txt'
compile_log = '/tmp/make.log'
compile_cmd = 'make -j8 >%s 2>&1' % compile_log
compile_tests_cmd = 'scons -f sconstruct.buildonly -j8 >%s 2>&1' % compile_log
make_clean_cmd = 'make clean >/dev/null 2>&1'
clean_tests_cmd = 'scons -c >/dev/null 2>&1'
test_log = '/tmp/test.log'
test_cmd = 'scons -j8 >%s 2>&1' % test_log

const_error_pat_template = r'In function [^(]+ %s\s*\(.*?error: (' + \
    'passing ‘const[^\n]+discards qualifiers|' + \
    'assignment of member ‘[^‘]+’ in read-only object|' + \
    'invalid conversion from ‘const[^‘]+’ to ‘(?!const))'

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
    for fpath in prototypes.non_test_fpaths():
        prototypes_in_this_file = prototypes[fpath]
        if not best:
            best = ['' for i in xrange(len(prototypes_in_this_file[0].params))]
        for proto in prototypes_in_this_file:
            i = 0
            for param in proto.params:
                if param.name:
                    current = param.name
                    # Moab has a nagging problem where params go by single-letter
                    # names. For jobs that are named J, this isn't that terrible--but
                    # for reservations, RMs, resources, and reqs, this is really
                    # unfortunate. Same for partitions and policies. Therefore,
                    # propose better names if no existing ones are useful.
                    if len(current) < 2 and len(best[i]) < 2:
                        current = param.propose_name()
                    if best[i]:
                        if len(best[i]) < len(current):
                            best[i] = current
                    else:
                        best[i] = current
                i += 1
    
    # On off chance that we have a func that's only in the test folder...
    if not best:
        return False

    # Now see which prototypes need to be updated to reflect the best names.
    change_count = 0
    for fpath in prototypes.non_test_fpaths():
        for proto in prototypes[fpath]:
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
        if prove_safe_change(root, prototypes, param_name_rollback()):
            for proto in prototypes.non_test_prototypes():
                for param in proto.params:
                    if param.new_name:
                        param.name = param.new_name
                    param.decl = str(param)
            return True
        
    return False

def tests_pass(root):
    print('Testing...')
    oldcwd = os.getcwd()
    try:
        os.chdir(os.path.join(root, 'test'))
        exitcode = run(test_cmd)
        if exitcode:
            print('  Tests failed. See %s for details.' % test_log)
        else:
            print('  Tests pass.')
        return exitcode == 0
    finally:
        os.chdir(oldcwd)
        
def get_compile_log_tail():
    with open(compile_log, 'r') as f:
        txt = f.read()
    if len(txt) > 2000:
        txt = txt[-2000:]
    return txt

def compile_is_clean(root, changed_func=None):
    print('Compiling...')
    oldcwd = os.getcwd()
    try:
        os.chdir(root)
        exitcode = run(compile_cmd)
        test_clean = False
        if exitcode:
            dont_bother_with_clean = False
            if changed_func:
                tail = get_compile_log_tail()
                pat = re.compile(const_error_pat_template % changed_func, re.DOTALL | re.MULTILINE)
                if pat.search(tail):
                    dont_bother_with_clean = True
            if dont_bother_with_clean:
                print('  Compile failed due to const error.')
                return False
            else:
                print('  Incremental compile failed. Trying to clean.')
                run(make_clean_cmd)
                test_clean = True
                exitcode = run(compile_cmd)
        if not exitcode:
            os.chdir(os.path.join(root, 'test'))
            if not test_clean:
                run(compile_tests_cmd)
                if exitcode:
                    print('  Incremental compile of tests failed.')
                    test_clean = True
            if test_clean:
                print('  Trying to clean and then compile tests.')
                run(clean_tests_cmd)
                exitcode = run(compile_tests_cmd)
        if exitcode:
            print('  Clean compile failed. See %s for details.' % compile_log)
        else:
            print('  Compile succeeded.')
        return exitcode == 0
    finally:
        os.chdir(oldcwd) 

def rewrite_prototypes(prototypes):
    for fpath in prototypes.dirty_fpaths():
        backup_file(fpath)
        with open(fpath, 'r') as f:
            txt = f.read()
        i = 0
        new_txt = ''
        new_names = []
        for proto in prototypes[fpath]:
            for param in proto.params:
                if proto.start_of_body and param.new_name and param.new_name != param.name:
                    new_names.append((re.compile(r'(\W)(?<![.>])%s(\W)' % param.name), r'\1%s\2' % param.new_name))
                new_txt += txt[i:param.begin]
                new_txt += str(param)
                i = param.begin + len(param.decl)
        if new_names:
            new_txt += txt[i:proto.start_of_body]
            body = txt[proto.start_of_body:proto.end_of_body]
            for pair in new_names:
                body = pair[0].sub(pair[1], body)
            new_txt += body
            i = proto.end_of_body
        new_txt += txt[i:]
        with open(fpath, 'w') as f:
            f.write(new_txt)
            
def _pluralize(noun, count):
    if count == 1:
        return noun
    return noun + 's'

def prove_safe_change(root, prototypes, undo_func):
    if not compile_is_clean(root, prototypes.function_name) or not tests_pass(root):
        print("  Change doesn't work. Backing it out.")
        for fpath in prototypes.dirty_fpaths():
            restore_file(fpath)
        undo_func(prototypes)
        if not compile_is_clean(root, prototypes.function_name) or not tests_pass(root):
            print('Unable to get back to a clean state; exiting prematurely.')
            sys.exit(1)
        return False
    else:
        print("  It works. Keeping change.")
        return True
        
def fix_func(func, root, cg, tags):
    if func.lower().endswith("printf"):
        tags += "SKIPPED"
        return tags
    # Locate every place where this function's prototype appears.
    # In some cases, the prototype might be followed by a body; in most cases, not.
    prototypes = find_prototypes_in_codebase(func, root)
    
    # Find the version of the prototype that's associated with the main implementation
    # of the function (not the one in test scaffolding). Use it as the standard against
    # which other prototypes are compared.
    ok = True
    impl = prototypes.find_best()
    for fpath in prototypes:
        for proto in prototypes[fpath]:
            if proto is not impl:
                if not proto.matches(impl):
                    print("  Prototypes don't match:\n    %s\n      vs\n    %s" % (impl.get_ideal(), proto.get_ideal()))
                    ok = False
    if not ok:
        tags += "INCONSISTENT_PROTOTYPES "
        return tags
    
    # We may be able to improve the code by copying param names into places that
    # don't have them.
    if False and improve_param_names(root, prototypes):
        tags += "PARAM_NAMES_IMPROVED "
        # Rather than trying to update every offset and every param name for every
        # prototype, in RAM, it's safer to just reload from disk after we make
        # changes.
        prototypes = find_prototypes_in_codebase(func, root)
        # Re-fetch impl,
        impl = prototypes.find_best()
            
    if not impl.start_of_body:
        print('  Unable to find an implementation of %s. Skipping.' % impl.name)
        tags += 'NO_IMPL '
        return tags
    
    if impl.is_const_candidate():
        change_count = 0
        param_idx = 0
        while param_idx < len(impl.params):
            param = impl.params[param_idx]
            original_state = None
            if param.is_const_candidate():
                if not param.is_const():
                    if impl.prove_param_cant_be_const(param_idx):
                        print("Proved that param %d can't be const." % (param_idx + 1))
                        pass
                    else:
                        original_state = False
                        param.set_const(True)
            elif param.is_const():
                # Currently we'll skip remediation of obnoxious const to speed up analysis
                # and limit diffs.
                param.set_const(False)
                if True:
                    tags += 'OBNOXIOUS_CONST: %s ' % param
                    print('  Skipping fix of unnecessary const -> %s.' % impl.get_ideal())
                    param.set_const(True)
                else:
                    original_state = True
            if original_state is not None:
                print('Trying %s...' % impl.get_ideal())
                for fpath in prototypes:
                    for proto in prototypes[fpath]:
                        proto.params[param_idx].data_type = param.data_type
                        proto.dirty = True
                rewrite_prototypes(prototypes)
                if prove_safe_change(root, prototypes, const_rollback(param, param_idx, original_state)):
                    # Rather than trying to update every offset and every param name for every
                    # prototype, in RAM, it's safer to just reload from disk after we make
                    # changes.
                    prototypes = find_prototypes_in_codebase(func, root)
                    # Re-fetch impl,
                    impl = prototypes.find_best()
                    change_count += 1
            param_idx += 1
        print('%d %s made.' % (change_count, _pluralize('change', change_count)))
        tags += str(change_count)
        if change_count:
            tags += ' --> ' + impl.get_ideal()
    else:
        tags += 'CANT_MODIFY'
    return tags
                
CONST_IRRELEVANT = 0
CONST_MATTERS = 1
OBNOXIOUS_CONST = 2
classify_labels = ['CONST_IRRELEVANT', 'CONST_MATTERS', 'OBNOXIOUS_CONST']

def _classify_func(params):
    cls = CONST_IRRELEVANT
    if params:
        for p in params:
            if ('*' in p or '&' in p) and ('const' not in p) and ('void' not in p):
                return CONST_MATTERS
            elif 'const' in p:
                cls = OBNOXIOUS_CONST
    return cls

def verify_clean(root):
    ok = True
    print('Verifying that codebase is clean before we start...')
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
    num_removed = 0
    if previously_analyzed:
        print('Eliminating previously analyzed functions...')
        for func in previously_analyzed:
            cg.remove(func)
            num_removed += 1
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
                num_removed += 1
        print('Reduced function count from %d to %d.' % (len(cg.by_caller) + len(cuttable), len(cg.by_caller)))
    return num_removed
    
def prune(cg):
    num_pruned = 0
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
        num_pruned += 1
    return num_pruned
        
def tabulate(func, tags):
    if not func.endswith('()'):
        func += '()'
    if not hasattr(tags, 'upper'):
        tags = str(tags)[1:-1].replace(',', '')
        tags = tags.strip()
    with open(outcomes_log, 'a') as f:
        f.write('%s\t%s\n' % (func, tags))
        
def load_previous_results():
    previously_analyzed = []
    if os.path.isfile(outcomes_log):
        with open(outcomes_log, 'r') as f:
            lines = f.readlines()
        previously_analyzed = [x[:x.find('\t')] for x in lines if x.find('\t') > -1]
    print('Found %d previously analyzed %s.' % (len(previously_analyzed), _pluralize('function', len(previously_analyzed))))
    return previously_analyzed

def fix_prototypes(root, start_count=0, end_count=0):
    print('')    
    root = os.path.normpath(os.path.abspath(root))
    
    global outcomes_log
    outcomes_log = os.path.join(root, outcomes_log)
    
    verify_makefile(root)
    verify_clean(root)
    
    print('Loading call graph...')
    cg = callgraph.Callgraph(root)
    func_count = len(cg.by_callee.keys())
    
    previously_analyzed = load_previous_results()
    func_count -= cut_noise(cg, previously_analyzed)
        
    tried_to_prune = False
    pass_number = 0
    while not cg.is_empty():
        pass_number += 1
        leaves = cg.get_leaves()
        print('\nPass %d: %d leaves out of %d functions ----------------' % (pass_number, len(leaves), len(cg.by_callee)))
        if len(leaves) == 0:
            # See if we can prune some stuff away by finding functions where const doesn't matter.
            if tried_to_prune:
                print('No functions are leaves; fixes after this point may be hit or miss because they are hampered by function interdependencies.')
                leaves = cg.by_caller.keys()
            else:
                tried_to_prune = True
                func_count -= prune(cg)
                continue
        else:
            tried_to_prune = False
            
        i = 1
        for func in leaves:
            tags = ''
            callers = None
            if func in cg.by_callee:
                callers = cg.by_callee[func]
            if not callers:
                print('\n%d.%d. %s appears to be an orphan, never called.' % (pass_number, i, func))
                tags += 'ORPHAN '
            params = cg.get_params(func)
            cls = _classify_func(params)
            if cls == CONST_MATTERS:
                print('\n%d.%d. Experimenting with changes to %s...' % (pass_number, i, func))
                try:
                    if (start_count > 0 and func_count > start_count):
                        tags += 'SKIPPED '
                    else:
                        tags = fix_func(func, root, cg, tags)
                except SystemExit:
                    raise
                except KeyboardInterrupt:
                    raise
                except:
                    traceback.print_exc()
                    tags += 'EXCEPTION '
            elif cls == OBNOXIOUS_CONST:
                tags += 'OBNOXIOUS_CONST '
                print('%d.%d. %s should not use const, but does.' % (pass_number, i, func))
            else:
                tags += 'CONST_IRRELEVANT '
                print("%d.%d. Constness is not relevant to %s." % (pass_number, i, func))
            tabulate(func, tags)
            cg.remove(func)
            func_count -= 1
            if (end_count > 0 and func_count <= end_count):
                break
            i += 1

def report_crash():
    import smtplib
    from email.mime.text import MIMEText
    msg = MIMEText(traceback.format_exc())
    msg['Subject'] = 'const_fix crashed'
    frm = 'daniel@springville.ac'
    to = 'dhardman@adaptivecomputing.com'
    msg['From'] = frm
    msg['To'] = to
    s = smtplib.SMTP('localhost')
    s.sendmail(frm, [to], msg.as_string())
    s.quit()

if __name__ == '__main__':
    start_count = 0
    end_count = 0
    try:
        if len(sys.argv) > 1:
            folder = sys.argv[1]
            if len(sys.argv) > 2:
                start_count = int(sys.argv[2])
                if len(sys.argv) > 3:
                    end_count = int(sys.argv[3])
        else:
            folder = '.'
        sys.exit(fix_prototypes(folder, start_count, end_count))
    except:
        report_crash()
        raise
