import os, re, sys

proto_pat = re.compile('<td class="memname">(.*?)</td>(.*?)Here is the call(er)? graph', re.DOTALL)
references_pat = re.compile('<p>References(.*?)</p>', re.DOTALL)
referenced_by_pat = re.compile('<p>Referenced by(.*?)</p>', re.DOTALL)
link_pat = re.compile('">([^<]+\(\))</a>')

def analyze(fname, by_caller, by_callee):
    f = open(fname, 'r')
    try:
        txt = f.read()        
    finally:
        f.close()
    i = txt.find('<h2 class="groupheader">Function Documentation</h2>')
    if i > -1:
        print(fname)
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
            print('  %s: calls %d, called by %d' % (funcname, called_count, caller_count))
        return True 
        
def build_call_graphs(folder):
    by_caller = {}
    by_callee = {}
    files = [f for f in os.listdir(folder) if f.endswith('.html')]
    for item in files:
        analyze(os.path.join(folder, item), by_caller, by_callee)
    for caller in by_caller:
        by_caller[caller].sort()
    for callee in by_callee:
        by_callee[callee].sort()
    
    orphans = []
    for func in by_callee:
        if not by_callee[func]:
            orphans.append(func)
            
    leaves = []
    for func in by_caller:
        if not by_caller[func]:
            leaves.append(func)
            
    print('\norphans\n-------')
    for o in orphans:
        print ('  %s' % o)
        
    print('\nleaves\n-------')
    for l in leaves:
        print ('  %s' % l)
    
if __name__ == '__main__':
    build_call_graphs(sys.argv[1])
