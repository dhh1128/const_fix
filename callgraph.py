import os, re, sys

doxy_output_folder = '/tmp/html'
doxy_log = '/tmp/doxy-stdout'

proto_pat = re.compile('<td class="memname">(.*?)</td>(.*?)Here is the call(er)? graph', re.DOTALL)
references_pat = re.compile('<p>References(.*?)</p>', re.DOTALL)
referenced_by_pat = re.compile('<p>Referenced by(.*?)</p>', re.DOTALL)
link_pat = re.compile('">([^<]+\(\))</a>')

def _analyze(fname, by_caller, by_callee):
    f = open(fname, 'r')
    try:
        txt = f.read()        
    finally:
        f.close()
    i = txt.find('<h2 class="groupheader">Function Documentation</h2>')
    if i > -1:
        #print(fname)
        txt = txt[i:]
        for proto_match in proto_pat.finditer(txt):
            funcname = proto_match.group(1).strip()
            i = funcname.rfind(' ')
            assert(i > -1)
            funcname = funcname[i + 1:] + '()'
            caller_count = 0
            called_count = 0
            chunk = proto_match.group(2)
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
        exitcode = os.system('doxygen docs/Doxyfile >%s' % doxy_log)
        return exitcode
    finally:
        os.chdir(oldcwd)
        
def _get_doxy_date(folder):
    info = os.stat(os.path.join(folder, 'index.html'))
    if info:
        return info.st_mtime
    return 0

def _get_vcs_date(folder):
    info = os.stat(os.path.join(folder, '.hg', 'branch'))
    if info:
        return info.st_mtime
    return 0

class Callgraph:
    def __init__(self, root):
        self.root = os.path.normpath(os.path.abspath(root))
    def load(self):
        doxy_lastmod = _get_doxy_date(doxy_output_folder)
        vcs_lastmod = _get_vcs_date(self.root)
        if doxy_lastmod < vcs_lastmod:
            print('Re-running doxygen. Tail %s to monitor...' % doxy_log)
            error = _call_doxygen(self.root)
            if error:
                sys.stderr.write('Doxygen failed with error code %d.\n', error)
                sys.exit(1)
        self._build_call_graphs()
    def _build_call_graphs(self):
        self.by_caller = {}
        self.by_callee = {}
        files = [f for f in os.listdir(doxy_output_folder) if f.endswith('.html')]
        for item in files:
            analyze(os.path.join(folder, item), by_caller, by_callee)
        for caller in by_caller:
            by_caller[caller].sort()
        for callee in by_callee:
            by_callee[callee].sort()
        self._break_simple_recursion()
    def get_orphans(self):
        orphans = []
        for func in self.by_callee:
            if not self.by_callee[func]:
                orphans.append(func)
    def get_leaves(self):
        leaves = []
        for func in self.by_caller:
            if not self.by_caller[func]:
                leaves.append(func)
    def remove(self, func):
        callers = self.by_callee[func]
        del self.by_callee[func]
        for caller in callers:
            self.by_caller[caller].remove(func)     
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
            if func in called:
                recursive.append[func]
                called.remove(func)
                x = self.by_callee[func]
                x.remove(func)
        self.recursive = recursive
