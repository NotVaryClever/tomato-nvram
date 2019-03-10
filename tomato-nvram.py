#!/usr/bin/env python3

import argparse
import base64
import configparser
import collections
import itertools
import io
import re
import shlex
import tarfile

# Names to ignore
ignore_names = re.compile(r'''
http_id         # HTTP ID
|https_crt_file # HTTP Certificate
|os_\w+         # OS Values
|\w+_cache      # Cache
''', re.VERBOSE)

# Splits nvram.txt on names
nvram_txt_split = re.compile(r'''
(?:\n|^)            # Newline (or start of string)
(?P<name>[\w.:/]+)  # Name
=                   # Equals
(?!=|\s*\n[^\w.:/]) # Values can't start wtih an equals or a newline
''', re.VERBOSE)

# nvram.txt epilogue
nvram_txt_epilogue = re.compile(r'\n(---\n[\w\s,.]+)?$')

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
    nvram_txt = nvram_txt_epilogue.sub('', nvram_txt)
    _, *namevalues = nvram_txt_split.split(nvram_txt)
    return set(zip(*[iter(namevalues)] * 2))

def diff_files(input_name, base_name):
    '''
    Return a set of items in input_name but not base_name.
    '''
    with open(input_name) as infile:
        input = parse_nvram_txt(infile.read())

    if base_name:
        with open(base_name) as infile:
            base = parse_nvram_txt(infile.read())
    
        return input - base;

    else:
        return input

def write_script(items, outfile, config):
    '''
    Write items to outfile in the form:

        nvram set name1=value1
        nvram set name2=value2
        nvram set name3='multi
        line
        value3'
    '''
    # Collapse small groups.
    def collapse(group):
        return config.rank[group.name] == len(config.names) and len(group) < 3

    # Group items based on pattern matched.
    groups = Groups(items, config).collapse(collapse)

    # Sections
    outfile.write(groups.formatted())

    # Certificate
    crt_file = dict(items).get('https_crt_file')
    if crt_file:
        outfile.write(HttpsCrtFile(crt_file).formatted())

    # Commit
    outfile.write('# Save\nnvram commit\n')

class Config:
    '''
    Group configuration from config.ini.
    '''
    def __init__(self, filename):
        parser = configparser.ConfigParser()
        parser.read(filename)
        self.names, self.patterns = zip(*((name, section['pattern']) for name, section in parser.items() if 'pattern' in section))
        self.rank = collections.defaultdict(lambda: len(self.names), ((name, i) for i, name in enumerate(self.names)))
        self.rank['Other'] = len(self.rank) + 1

class Groups(collections.defaultdict):
    '''
    Container for groups/sections.
    '''
    def __init__(self, items, config):
        super().__init__()
        self.rank = config.rank
        lookup = re.compile('|'.join('({})'.format(pattern) for pattern in config.patterns))
        for item in items:
            item = self.Item(*item)
            if not ignore_names.match(item.name):
                match = lookup.match(item.name)
                group = config.names[match.lastindex - 1] if match else item.group
                self[group].append(item)

    def __missing__(self, key):
        return self.setdefault(key, self.Group(key, self.rank[key]))

    def collapse(self, func, dst='Other'):
        for key in {key for key, group in self.items() if func(group) and key != dst}:
            if dst:
                self[dst].extend(self[key])
            del self[key]
        return self

    def formatted(self):
        return '\n'.join(group.formatted() for group in sorted(self.values()))

    class Group(list):
        '''
        Format a named group of items.
        '''
        def __init__(self, name, rank, *args, **kwargs):
            self.name = name
            self.rank = rank
            return super().__init__(*args, **kwargs)

        def __lt__(self, other):
            return self.sort_key < other.sort_key

        @property
        def large(self):
            return any(item.large for item in self)

        @property
        def sort_key(self):
            return self.large, self.rank, self.name

        def formatted(self):
            # Format and divide into single and multi line items.
            width = max(item.width for item in self)
            newlines = collections.defaultdict(list)
            for item in sorted(self):
                newlines[bool(item.newlines)].append(item)
            single, multi = ((item.formatted(width) for item in newlines[key]) for key in (False, True))

            formatted = ''.join(single) + '\n'.join(multi)
            return '# {}\n{}'.format(self.name, formatted)

    class Item:
        '''
        Format a single item.
        '''
        def __init__(self, name, value):
            self.name = name
            self.value = value

            parts = tuple(self.capitalize(part) for part in name.split('_'))
            self.group = parts[0] if len(parts) > 1 else 'Other'
            self.comment = parts[-1]

            self.command = 'nvram set {}={}'.format(name, self.quoted(value))
            self.newlines = self.command.count('\n')
            self.sort_key = self.newlines, name.lower(), name
            self.width = len(self.command) if not self.newlines else 0
            self.large = self.newlines > 24 or self.width > 128

        def __lt__(self, other):
            return self.sort_key < other.sort_key

        def formatted(self, width=0):
            comment = None
            if comment:
                if self.newlines:
                    return '\n# {}\n{}\n'.format(comment, self.command)
                else:
                    return '{:<{}} # {}\n'.format(self.command, width, comment)
            else:
                return '{}\n'.format(self.command)

        @staticmethod
        def capitalize(part):
            return part.capitalize() if len(part) > 4 else part.upper()

        @classmethod
        def quoted(cls, value):
            if "'" in value:
                return '"{}"'.format(cls.special_chars.sub(r'\\\g<0>', value))
            if not cls.special_chars.search(value):
                if cls.list_break.search(value) and '\n' not in value:
                    return '"\\\n{}"'.format(cls.list_break.sub('\\\n', value))
                if '\n' in value:
                    return '"\\\n{}"'.format(value)
            return shlex.quote(value) if value else value

        special_chars = re.compile(r'["\\`]|\$(?=\S)')  # Require escaping in double quotes
        list_break = re.compile(r'(?<=>)(?!$)')         # Where to break tomato lists

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

    template = '''
# Web GUI Certificate
echo '{cert}' > /etc/cert.pem

# Web GUI Private Key
echo '{key}' > /etc/key.pem

# Tar Certificate & Key
nvram set https_crt_file="$(cd / && tar -czf - etc/*.pem | openssl enc -A -base64)"

'''

def bisect(items, predicate):
    matches = collections.defaultdict(list)
    for item in items:
        matches[predicate(item)].append(item)
    return matches[True], matches[False]

parser = argparse.ArgumentParser(description='Generate NVRAM setting shell script.',
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument('-i', '--input', default='nvram.txt', help='input filename')
parser.add_argument('-b', '--base', default='defaults.txt', help='base filename')
parser.add_argument('-o', '--output', default='set-nvram.sh', help='output filename')
parser.add_argument('-c', '--config', default='config.ini', help='config filename')

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
        # Load conifg.
        config = Config(args.config)

        # Write output script.
        with open(args.output, 'w') as outfile:
            write_script(diff, outfile, config)

        print('{:,} values written to {}'.format(len(diff), args.output))

if __name__ == '__main__':
    import sys
    main(sys.argv[1:])