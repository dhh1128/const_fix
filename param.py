import re

array_spec_pat = re.compile('.*(\[^]]\])$')
const_prefix_pat = re.compile('^const ([a-zA-Z0-9_]+)(.*)$')
moab_type_pat = re.compile('.*\Wm[a-z_0-9]+_t$')
datatype_names = 'int|short|long|double|float|char|bool'.split('|')
moab_struct_naming_pat = re.compile(r'm([a-z_]+)_t(?=$|\W)')
moab_common_out_pat = re.compile(r'(m?u?(long|int)|mbool_t)\s*\*\s*$')

splittable = [
    'table',
    'info',
    'mdata',
    'data',
    'grp',
    'req',
    'vm',
    'grid',
    'stats',
    'list',
    'array',
    'node'
]   

abbreviatable = {
    'request': 'req',
    'response': 'resp',
    'constraint': 'cons',
    'policy': 'pol',
    'partition': 'par',
    'group': 'grp',
    'threadpool': 'thpool',
    'trigger': 'trig',
    'resource': 'res',
    'reservation': 'rsv',
}
    
def _squeeze(txt):
    '''Replace all runs of whitepace with a single space, and trim front and back.'''
    return re.sub(r'\s{2,}', ' ', txt).strip()
    
def normalize_type(typ):
    '''
    Put the type portion of a parameter declaration into normalized
    form so it can be compared reliably:
    
      const char* --> char const *
      mjob_t  & --> mjob_t &
    '''
    typ = _squeeze(typ.replace('*', ' * ').replace('&', ' & ')).replace('* *', '**')
    m = const_prefix_pat.match(typ)
    if m:
        typ = '%s const%s' % (m.group(1), m.group(2))
    return typ

class Param:
    def __init__(self, begin, decl):
        self.begin = begin
        self.decl = decl
        self.array_spec = ''
        self.data_type = None
        self.name = None
        self.new_name = None
        self._parse()
        
    def propose_name(self):
        m = moab_struct_naming_pat.match(self.data_type)
        if m:
            base = m.group(1)
            '''
            r -> rm, resource, rsv, req
            p -> policy, partition
            '''
            for key in abbreviatable:
                if base.endswith(key):
                    base = base[0:len(base) - len(key)] + '_' + abbreviatable[key]
                    break
            for x in splittable:
                if base.endswith(x):
                    i = len(base) - len(x)
                    if base[i - 1] != '_':
                        base = base[0:i] + '_' + base[i:]
            cap_next = False
            proposed = ''
            for c in base:
                if c == '_':
                    cap_next = True
                else:
                    if cap_next:
                        proposed += c.upper()
                        cap_next = False
                    else:
                        proposed += c
            return proposed

    def is_const_candidate(self):
        dt = self.data_type
        if 'void' in dt:
            return False
        i = dt.find('*')
        j = dt.find('&')
        # Params that are not pointers or references are passed by value,
        # so their constness is irrelevant.
        if i == -1 and j == -1:
            return False
        # Just some optimizations that applies to moab specifically.
        # 1. EMsg bufs are passed around to accumulate error messages; we
        #    know they are modified.
        # 2. It's common in the codebase to see pointers to numeric types
        #    passed for OUT params: int * size, long * count, etc. These
        #    are also not worth checking.
        if i > -1 and self.data_type == 'char *' and self.name == 'EMsg':
            return False
        if i > -1 and moab_common_out_pat.match(self.data_type):
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
                self.data_type = _squeeze(self.data_type[:i].rstrip() + ' const ' + self.data_type[i:])
        elif self.is_const():
            self.data_type = _squeeze(self.data_type.replace('const', ''))

    def _parse(self):
        decl = _squeeze(self.decl)
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
                name = decl[name_idx:]
                if name in datatype_names:
                    name_idx = -1
        if name_idx > -1:
            self.data_type = decl[0:name_idx].rstrip()
            self.name = decl[name_idx:]
        else:
            self.data_type = decl
        self.data_type = normalize_type(self.data_type)

    def __str__(self):
        name = self.new_name
        if not name:
            name = self.name
        if name:
            return self.data_type + ' ' + name + self.array_spec
        return self.data_type + self.array_spec

