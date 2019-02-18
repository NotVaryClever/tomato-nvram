#!/usr/bin/env python3

import argparse
import base64
import configparser
import io
import re
import shlex
import tarfile

# Names to ignore
ignore_names = re.compile(r'''
http_id         # HTTP ID
|https_crt_file # HTTP Certificate
|\w+_cache      # Cache
''', re.VERBOSE)

# Format of items in nvram.txt
nvram_txt_pattern = re.compile(r'''
(?P<name>[\w.:/]+)  # Name
=                   # Equals
(?P<value>.*?)      # Value
\n(?=               # Followed by
[\w.:/]+=           # Next stanza
|---\n              # Or prelude on MIPS
|$)                 # Or end of file
''', re.DOTALL | re.VERBOSE)

# Multiline characters
multiline_chars = re.compile(r'[>\n]')

def parse_nvram_txt(nvram_txt):
    '''
    Parse nvram.txt of the form:

        name1=value1
        name2=value2
        name3=multi
        line
        value3

    Return a set of name-value tuples.
    '''
    return set(match.groups() for match in nvram_txt_pattern.finditer(nvram_txt))

def diff_files(input_name, base_name):
    '''
    Return a set of items in input_name but not base_name.
    '''
    with open(input_name) as infile:
        input = parse_nvram_txt(infile.read())

    if base_name:
        with open(base_name) as infile:
            base = parse_nvram_txt(infile.read())
    else:
        return input

    return input - base;

def write_script(items, outfile):
    '''
    Write items to outfile in the form:

        nvram set name1=value1
        nvram set name2=value2
        nvram set name3='multi
        line
        value3'
    '''
    # Sections
    outfile.writelines(SectionFormatter(items).formatted())

    # Certificate
    crt_file = dict(items).get('https_crt_file')
    if crt_file:
        outfile.write(HttpsCrtFile(crt_file).formatted())

    # Commit
    outfile.write('# Save\nnvram commit\n')

class SectionFormatter:
    '''
    Format items for each section in the config file.
    '''
    def __init__(self, items, filename='config.ini'):
        # Load section patterns.
        parser = configparser.ConfigParser()
        parser.read(filename)
        names, patterns = zip(*((name, section['pattern']) for name, section in parser.items() if 'pattern' in section))

        # Group items into sections based on pattern matched.
        lookup = re.compile('|'.join('({})'.format(pattern) for pattern in patterns))
        self.sections = {name: [] for name in names}
        for item in items:
            item = self.Item(*item)
            match = lookup.match(item.name)
            if match and not ignore_names.match(item.name):
                self.sections[names[match.lastindex - 1]].append(item)

    def formatted(self):
        for name, items in self.sections.items():
            if items:
                formatted = ''.join(item.formatted() for item in sorted(items))
                yield '# {}\n{}\n'.format(name, formatted)

    class Item:
        '''
        Format a single item.
        '''
        def __init__(self, name, value):
            self.name = name
            self.value = value
            self.multiline = bool(multiline_chars.search(value))
            self.sort_key = self.multiline, name

            if self.multiline:
                value = '"{}"'.format(re.sub(r'^|(?<=>)(?!$)', '\\\n', value))
            else:
                value = shlex.quote(value)
            self.command = 'nvram set {}={}'.format(name, value)

        def __lt__(self, other):
            return self.sort_key < other.sort_key

        def formatted(self):
            return '{}\n'.format(self.command)

class HttpsCrtFile:
    '''
    Certificate and private key for HTTPS access.
    '''
    def __init__(self, https_crt_file, *args, **kwargs):
        self.tarfile = tarfile.open(fileobj=io.BytesIO(base64.b64decode(https_crt_file)))
        return super().__init__(*args, **kwargs)

    def getpem(self, name):
        return self.tarfile.extractfile('etc/{}.pem'.format(name)).read().decode().strip()

    def formatted(self):
        return self.template.format(**{name: self.getpem(name) for name in ('cert', 'key')})

    template = '''\
# Web GUI Certificate
echo '{cert}' > /etc/cert.pem

# Web GUI Private Key
echo '{key}' > /etc/key.pem

# Tar Certificate & Key
nvram set https_crt_file="$(cd / && tar -czf - etc/*.pem | openssl enc -A -base64)"

'''

parser = argparse.ArgumentParser(description='Generate NVRAM setting shell script.',
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument('-i', '--input', default='nvram.txt', help='input filename')
parser.add_argument('-b', '--base', default='defaults.txt', help='base filename')
parser.add_argument('-o', '--output', default='set-nvram.sh', help='output filename')

def main(args):
    # Parse arguments.
    args = parser.parse_args(args)

    try:
        # Diff files.
        diff = diff_files(args.input, args.base)
    
    except FileNotFoundError as error:
        print(error)
        parser.print_help()

    else:
        # Write output script.
        with open(args.output, 'w') as outfile:
            write_script(diff, outfile)

if __name__ == '__main__':
    import sys
    main(sys.argv[1:])