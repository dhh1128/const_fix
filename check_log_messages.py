import os, re, sys

my_folder, my_name = os.path.split(os.path.abspath(__file__))
help_pat = re.compile(r'(--?)?(\?|h(elp)?)', re.IGNORECASE)
id_param = r'\s*([a-zA-Z_]+)\s*,'
num_param = r'\s*([0-9]+)\s*,'
msg_param = r'"(.*?)"\s*,'
comment_param = r'"(.*?)"\s*\)'
decl_pat = re.compile(r'^\s*EVENT\s*\(' + id_param + id_param + id_param + id_param + num_param + msg_param + comment_param, re.MULTILINE | re.DOTALL)
multiline_pat = re.compile('"[ \t]*\r?\n[ \t]*"')

bad_pats = [
    (re.compile(r'\Wand/or\W'), 'Use either "and" or "or", not "and/or".'),
    (re.compile(r'\Winternet\w'), 'Capitalize "Internet".'),
]

def find_codepath(folder):
    while True:
        if os.path.isdir(os.path.join(folder, 'include')):
            codepath = os.path.join(folder, 'include', 'MMessageTuples.h')
            if os.path.isfile(codepath):
                return codepath
        if folder.rfind('/') > 0:
            folder, subdir = os.path.split(folder)
        else:
            return

def fix_multiline(quoted):
    return multiline_pat.sub('', quoted)
        
class msg:
    def __init__(self, match):
        self.component = match.group(1)
        self.name = match.group(2)
        self.level = match.group(3)
        self.escalation = match.group(4)
        self.number = match.group(5)
        self.msg = fix_multiline(match.group(6))
        self.msg = fix_multiline(match.group(7))

def check_log_messages(folder):
    codepath = find_codepath(folder)
    if not codepath:
        print('Unable to find include/MMessageTuples.h from the codebase identified by %s.' % folder)
        sys.exit(1)
    msgs = {}
    
    

if __name__ == '__main__':
    folder = None
    if len(sys.argv) == 2:
        arg = sys.argv[1]
        if help_pat.match(arg):
            print('python %s [folder]\n  Check log messages in codebase. Any folder in codebase is equivalent.' % my_name)
            sys.exit(0)
        else:
            folder = arg
    check_log_messages(folder)