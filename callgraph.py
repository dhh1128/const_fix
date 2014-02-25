import os, re, sys

doxy_output_folder = '/tmp/html'
doxy_log = '/tmp/doxy-stdout'

proto_pat = re.compile(r'<td class="memname">(.*?)</td>(.*?)</div>\s*</div>', re.DOTALL)
references_pat = re.compile('<p>References(.*?)</p>', re.DOTALL)
referenced_by_pat = re.compile('<p>Referenced by(.*?)</p>', re.DOTALL)
link_pat = re.compile('">([^<]+\(\))</a>')
valid_sections = ['Function Documentation', 'tructor Documentation']
param_pat = re.compile('<td class="paramtype">(.*?)</td>')

def _split_method_name(func):
    i = func.find('::')
    if i > -1:
        return func[0:i], func[i+2:]
    return None, func

def _remove_name_with_typedef(func, names):
    '''
    Testing shows that in our codebase, we sometimes do something like this:
    
        typedef msnl_t MSNL;
    
    In some places in code, we do stuff like this:
    
        void MSNL::SetCount() { ... }
    
    And in others, we do this:
    
        msnl_t::SetCount(25);
        
    Doxygen doesn't resolve the typedefs consistently, so we have to attempt to compensate.
    This func looks through a list of functions for possible fuzzy matches and removes one
    if found. We only call it if exact matching hasn't helped us.
    '''
    if names:
        #print('trying to remove %s from %s' % (func, names))
        cls, method = _split_method_name(func)
        #print('split = %s, %s' % (cls, method))
        if cls:
            possible_names = [cls.upper(), cls.lower()]
            if cls.endswith('_t'):
                alt = cls[:-2]
            else:
                alt = cls + '_t'
            possible_names.append(alt.upper())
            possible_names.append(alt.lower())
            possible_names = [n + '::' + method for n in possible_names]
            for item in names:
                if item in possible_names:
                    names.remove(item)
                    return True
                
def _normalize_param(param):
    param = param.replace('&#160;', '').replace('&amp;', '&').strip()
    while True:
        i = param.find('<a ')
        if i == -1:
            break
        j = param.find('>', i + 3)
        if j == -1:
            break
        param = param[0:i] + param[j + 1:]
    param = param.replace('</a>', '')
    return param    

def _analyze(fname, by_caller, by_callee, params_by_caller):
    #print(fname)
    f = open(fname, 'r')
    try:
        all_txt = f.read()        
    finally:
        f.close()
    for section in valid_sections:
        section_top = '%s</h2>' % section
        i = all_txt.find(section_top)
        if i > -1:
            txt = all_txt[i:]
            # Find the next section of the doc (typically this is the Variable Documentation part) --
            # and trim it off so it doesn't confuse us.
            i = txt.find('<h2 class="groupheader">', 10)
            if i > -1:
                txt = txt[0:i]
            for proto_match in proto_pat.finditer(txt):
                funcname = proto_match.group(1).strip()
                # Ignore template classes for now
                if '&gt;::' in funcname:
                    continue
                i = funcname.rfind(' ')
                if i == -1:
                    # This is a corner case that happens occasionally with macros.
                    # We can ignore, mostly--but not entirely--without repercussions.
                    funcname = funcname + '()'
                else:
                    funcname = funcname[i + 1:] + '()'
                caller_count = 0
                called_count = 0
                chunk = proto_match.group(2)
                params = []
                for match in param_pat.finditer(chunk):
                    param = _normalize_param(match.group(1))
                    params.append(param)
                params_by_caller[funcname] = params
                refs_match = references_pat.search(chunk)
                refby_match = referenced_by_pat.search(chunk)
                if funcname not in by_caller:
                    by_caller[funcname] = []
                if funcname not in by_callee:
                    by_callee[funcname] = []
                if refs_match:
                    x = by_caller[funcname]
                    for match in link_pat.finditer(refs_match.group(1)):
                        called = match.group(1)
                        if called not in x:
                            x.append(called)
                            called_count += 1
                if refby_match:
                    x = by_callee[funcname]
                    for match in link_pat.finditer(refby_match.group(1)):
                        caller = match.group(1)
                        if caller not in x:
                            x.append(caller)
                            caller_count += 1
                #print('  %s: calls %d, called by %d' % (funcname, called_count, caller_count))
    return True 
        
def _call_doxygen(folder):
    oldcwd = os.getcwd()
    try:
        os.chdir(folder)
        exitcode = os.system('doxygen docs/Doxyfile >%s 2>&1' % doxy_log)
        return exitcode
    finally:
        os.chdir(oldcwd)
        
def _get_doxy_date(folder):
    try:
        info = os.stat(os.path.join(folder, 'index.html'))
        if info:
            return info.st_mtime
    except:
        pass
    return 0

def _get_vcs_date(folder):
    info = os.stat(os.path.join(folder, '.git', 'FETCH_HEAD'))
    if info:
        return info.st_mtime
    return 0

class Callgraph:
    def __init__(self, root):
        self.root = os.path.normpath(os.path.abspath(root))
        self.load()
    def load(self):
        doxy_lastmod = _get_doxy_date(doxy_output_folder)
        vcs_lastmod = _get_vcs_date(self.root)
        if doxy_lastmod < vcs_lastmod:
            print('  doxy_lastmod = %s; vcs_lastmod = %s' % (doxy_lastmod, vcs_lastmod))
            print('  Re-running doxygen. Tail %s to monitor...' % doxy_log)
            error = _call_doxygen(self.root)
            if error:
                sys.stderr.write('Doxygen failed with error code %d.\n', error)
                sys.exit(1)
        else:
            print('  Doxygen output is up-to-date.')
        self._build_call_graphs()
    def _build_call_graphs(self):
        self.by_caller = {}
        self.by_callee = {}
        self.params_by_caller = {}
        files = [f for f in os.listdir(doxy_output_folder) if f.endswith('.html')]
        sys.stdout.write('  Analyzing')
        for item in files:
            sys.stdout.write('.')
            sys.stdout.flush()
            _analyze(os.path.join(doxy_output_folder, item), self.by_caller, self.by_callee, self.params_by_caller)
        print('\n  Sorting...')
        for caller in self.by_caller:
            self.by_caller[caller].sort()
        for callee in self.by_callee:
            self.by_callee[callee].sort()
        print('  Breaking recursion...')
        self._break_simple_recursion()
    def get_params(self, func):
        if func in self.params_by_caller:
            return self.params_by_caller[func]
    def get_orphans(self):
        orphans = []
        for func in self.by_callee:
            if not self.by_callee[func]:
                orphans.append(func)
    def get_leaves(self):
        leaves = []
        for func in self.by_caller:
            # If this function doesn't call anything, then it's a leaf.
            if not self.by_caller[func]:
                leaves.append(func)
        return leaves
    def remove(self, func):
        if func in self.params_by_caller:
            #print('deleting %s from params_by_caller; before len = %d' % (func, len(self.params_by_caller)))
            del self.params_by_caller[func]
            #print('deleting %s from params_by_caller; after len = %d' % (func, len(self.params_by_caller)))            
        if func in self.by_callee:
            #print('deleting %s from self.by_callee; before len = %d' % (func, len(self.by_callee)))
            callers = self.by_callee[func]
            for caller in callers:
                try:
                    called_by_this_caller = self.by_caller[caller]
                    #print('deleting %s from self.by_caller["%s"]; before len = %d' % (func, caller, len(called_by_this_caller)))
                    called_by_this_caller.remove(func)     
                    #print('deleting %s from self.by_caller["%s"]; after len = %d' % (func, caller, len(called_by_this_caller)))
                except:
                    # Some functions are overloaded or defined more than one way in the codebase.
                    # Don't get hung up on these...
                    if caller == 'main()':
                        pass
                    else:
                        if caller not in self.by_caller or not _remove_name_with_typedef(func, self.by_caller[caller]):
                            # Special case; moab codebase uses typedefs in an unfortunate way with MSNL, which causes
                            # inconsistency in doxygen output. Ignore...
                            if 'MSNL' in func or 'MSNL' in caller:
                                pass
                            else:
                                x = []
                                if caller in self.by_caller:
                                    x = self.by_caller[caller]
                                if x:
                                    print("Couldn't remove %s from the called list for %s." % (func, caller))
                                    print('Here is what the called list for %s looked like: %s' % (caller, x))
            del self.by_callee[func]
            #print('deleting %s from self.by_callee; after len = %d' % (func, len(self.by_callee)))
        else:
            print('Unable to delete %s.' % func)
        if func in self.by_caller:
            #print('deleting %s from self.by_caller; before len = %d' % (func, len(self.by_caller)))
            del self.by_caller[func]
            #print('deleting %s from self.by_caller; after len = %d' % (func, len(self.by_caller)))            
        #x = raw_input()
    def is_empty(self):
        return not (self.by_callee or self.by_caller)
    def _break_simple_recursion(self):
        '''
        This only breaks recursion where a function calls itself directly;
        indirect recursion is not detected.
        '''
        recursive = []
        for func in self.by_caller:
            called = self.by_caller[func]
            # Does function call itself?
            if func in called:
                recursive.append(func)
                called.remove(func)
                x = self.by_callee[func]
                try:
                    x.remove(func)
                except:
                    pass
        print('  Found %d recursive functions.' % len(recursive))
        self.recursive = recursive
