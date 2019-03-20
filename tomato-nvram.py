#!/usr/bin/env python3

import collections
import re
import shlex

# Names to ignore
ignore_names = re.compile(r'''
http_id         # HTTP ID
|os_\w+         # OS Values
|\w+_cache      # Cache
''', re.VERBOSE)

def keep_item(item):
    name, value = item
    return not ignore_names.match(name)

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

    Return an iterable of name-value tuples.
    '''
    nvram_txt = nvram_txt_epilogue.sub('', nvram_txt)
    _, *namevalues = nvram_txt_split.split(nvram_txt)
    return filter(keep_item, zip(*[iter(namevalues)] * 2))

def diff_files(input_name, base_name):
    '''
    Return a mapping of items in input_name but not base_name.
    '''
    with open(input_name) as infile:
        input = parse_nvram_txt(infile.read())

    if base_name:
        with open(base_name) as infile:
            base = parse_nvram_txt(infile.read())
    
        return dict(set(input).difference(base))

    else:
        return dict(input)

def write_script(items, outfile, config):
    '''
    Write items to outfile in the form:

        nvram set name1=value1
        nvram set name2=value2
        nvram set name3='multi
        line
        value3'
    '''
    # Bypass special items.
    wireless = dedup_wireless(items, config)
    crt_file = HttpsCrtFile.extract(items)

    # Group items based on pattern matched.
    groups = Groups(items.items(), config)

    # Collapse small groups.
    groups.collapse()

    # Dedup wireless.
    if wireless:
        groups[wireless.name] = wireless

    # Write groups.
    outfile.write(groups.formatted())

    # Certificate
    if crt_file:
        outfile.write(crt_file.formatted())

    # Commit
    outfile.write('\n# Save\nnvram commit\n')

class Groups(collections.defaultdict):
    '''
    Container for groups/sections.
    '''
    def __init__(self, items, config):
        super().__init__()
        self.config = config
        for item in items:
            item = Item(*item)
            self[config.group(item)].append(item)

    def __missing__(self, key):
        return self.setdefault(key, Group(key, self.config.rank[key]))

    def collapse(self, minsize=3, dst='Other'):
        '''
        Collapse groups smaller than minsize into a group named dst.
        '''
        def collapsible(group):
            return self.config.collapsible(group) and len(group) < minsize
        for key in {key for key, group in self.items() if collapsible(group) and key != dst}:
            if dst:
                self[dst].extend(self[key])
            del self[key]
        return self

    def formatted(self):
        groups = sorted(self.values(), key=lambda group: group.sort_key)
        return '\n'.join(group.formatted() for group in groups)

class Group(list):
    '''
    Format a named group of items.
    '''
    def __init__(self, name, rank, prefix=None, suffix=None):
        super().__init__()
        self.name = name
        self.rank = rank
        self.prefix = prefix + '\n' if prefix else ''
        self.suffix = suffix + '\n' if suffix else ''

    @property
    def large(self):
        return any(item.large for item in self)

    @property
    def sort_key(self):
        return self.large, self.rank, self.name

    def formatted(self):
        width = max(item.width for item in self)
        items = sorted(self, key=lambda item: item.sort_key)
        single = (item.formatted(width) for item in items if not item.newlines)
        multi  = (item.formatted(width) for item in items if     item.newlines)
        return '# {}\n{}{}{}{}'.format(self.name, self.prefix, ''.join(single), '\n'.join(multi), self.suffix)

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

def dedup_wireless(items, config):
    groups = collections.defaultdict(set)
    originals = collections.defaultdict(set)
    for name, value in items.items():
        match = re.match(r'wl\d+', name)
        if match:
            wl_name = '${wl}' + name[match.end():]
            item = wl_name, value
            groups[match.group()].add(item)
            originals[wl_name].add(name)
    if len(groups) > 1:
        wireless=set.intersection(*groups.values())
        if wireless:
            prefix='for wl in {}\ndo'.format(' '.join(sorted(groups)))
            group = Group('Wireless', config.getrank(''), prefix=prefix, suffix='done')
            for wl_name, value in wireless:
                group.append(Item(wl_name, value))
                for original in originals[wl_name]:
                    group.rank = min(group.rank, config.getrank(original))
                    del items[original]
            return group

import base64
import io
import tarfile
class HttpsCrtFile:
    '''
    Certificate and private key for HTTPS access.
    ''' 
    def __init__(self, https_crt_file):
        self.tarfile = tarfile.open(fileobj=io.BytesIO(base64.b64decode(https_crt_file)))

    @classmethod
    def extract(cls, items):
        crt_file = items.pop('https_crt_file', None)
        if crt_file:
            return cls(crt_file)

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

import configparser
class Config:
    '''
    Group configuration from config.ini.
    '''
    def __init__(self, filename):
        parser = configparser.ConfigParser()
        parser.read(filename)
        self.names, patterns = zip(*((name, section['pattern']) for name, section in parser.items() if 'pattern' in section))
        self.lookup = re.compile('|'.join('({})'.format(pattern) for pattern in patterns))
        self.rank = collections.defaultdict(lambda: len(self.names), ((name, i) for i, name in enumerate(self.names)))
        self.rank['Other'] = len(self.rank) + 1

    def group(self, item):
        match = self.lookup.match(item.name)
        return self.names[match.lastindex - 1] if match else item.group

    def collapsible(self, group):
        return group.rank == len(self.names)

    def getrank(self, itemname):
        match = self.lookup.match(itemname)
        return self.rank[self.names[match.lastindex - 1]] if match else len(self.names)

import argparse
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
        return

    if diff:
        # Load conifg.
        config = Config(args.config)

        # Write output script.
        with open(args.output, 'w') as outfile:
            write_script(diff, outfile, config)

        print('{:,} values written to {}'.format(len(diff), args.output))

    else:
        print('No differences found.')

if __name__ == '__main__':
    import sys
    main(sys.argv[1:])