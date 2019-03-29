#!/usr/bin/env python3

import re

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
    crt_file = HttpsCrtFile.extract(items)

    # Group items based on pattern matched.
    groups = Groups(items.items(), config)

    # Collapse small groups.
    groups.collapse()

    # Dedup wireless and WAN.
    groups.dedup('wl')
    groups.dedup('wan')

    # Write groups.
    outfile.write(groups.formatted())

    # Certificate
    if crt_file:
        outfile.write(crt_file.formatted())

    # Commit
    outfile.write('\n# Save\nnvram commit\n')

import collections
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

    def dedup(self, prefix, dst=None, minsize=3):
        '''
        Factor out common settings.
        '''
        Deduper(prefix, self, self.config).dedup(dst, minsize)

    def formatted(self):
        groups = sorted(self.values(), key=lambda group: group.sort_key)
        return '\n'.join(group.formatted() for group in groups)

class Group(list):
    '''
    Format a named group of items.
    '''
    def __init__(self, name, rank, *args, prefix=None, suffix=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = name
        self.rank = rank
        self.prefix = prefix
        self.suffix = suffix

    @property
    def large(self):
        return any(item.large for item in self)

    @property
    def sort_key(self):
        return self.large, self.rank, self.name

    def formatted(self):
        width = max(item.width for item in self)
        items = sorted(self)
        single = (item.formatted(width) for item in items if not item.newlines)
        multi  = (item.formatted(width) for item in items if     item.newlines)
        prefix = self.prefix + '\n' if self.prefix else ''
        suffix = self.suffix + '\n' if self.suffix else ''
        return '# {}\n{}{}{}{}'.format(self.name, prefix, ''.join(single), '\n'.join(multi), suffix)

import shlex
class Item:
    '''
    Format a single item.
    '''
    def __init__(self, name, value):
        self.name = name
        self.value = value
        self.__key = name, value

        parts = name.split('_')
        self.prefix = parts[0] if len(parts) > 1 else ''
        self.suffix = parts[-1]
        self.group = self.capitalize(self.prefix)

        self.command = 'nvram set {}={}'.format(name, self.quoted(value))
        self.newlines = self.command.count('\n')
        self.sort_key = self.newlines, name.lower().replace('_', ' ')
        self.width = len(self.command) if not self.newlines else 0
        self.large = self.newlines > 24 or self.width > 128

    def __eq__(self, other):
        return self.__key == other.__key

    def __hash__(self):
        return hash(self.__key)

    def __lt__(self, other):
        return self.sort_key < other.sort_key

    def __repr__(self):
        return '{}={}'.format(self.name, self.value)

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

import itertools
import os.path
import string
class Deduper:
    def __init__(self, prefix, groups, config):
        self.groups = groups
        self.pattern = re.compile(r'{}\d*'.format(prefix))
        self.config = config

    def dedup(self, dst=None, minsize=3):
        while True:
            key_to_prefixes = collections.defaultdict(set)
            key_to_group_and_item_tuples = collections.defaultdict(list)
            for match, item, group in self.matching():
                key = item.name[match.end():], item.value
                key_to_prefixes[key].add(item.prefix)
                key_to_group_and_item_tuples[key].append((group, item))

            prefixes_to_keys = collections.defaultdict(set)
            for key, prefixes in key_to_prefixes.items():
                for combo in self.powerset(prefixes, 2):
                    prefixes_to_keys[combo].add(key)

            if prefixes_to_keys:
                prefixes, keys = max(prefixes_to_keys.items(), key=self.lines_saved)
                if len(keys) >= minsize and self.lines_saved((prefixes,keys)) > 0:
                    names = list(group.name for key in keys for group, _ in key_to_group_and_item_tuples[key])
                    dst = dst or os.path.commonprefix(names).strip(string.punctuation + string.whitespace)
                    group = self.group(dst, prefixes, keys)
                    self.groups[id(group)] = group
                    for key in keys:
                        for group, item in key_to_group_and_item_tuples[key]:
                            group.remove(item)
                            if not group:
                                del self.groups[group.name]
                    continue
            return

    def group(self, name, prefixes, keys):
        prefix = os.path.commonprefix(list(prefixes))
        items = (Item('${{{}}}{}'.format(prefix, suffix), value) for suffix, value in keys)
        return Group(name, self.config.rank[name], items,
                     prefix='for {} in {}\ndo'.format(prefix, ' '.join(sorted(prefixes))),
                     suffix='done')

    def matching(self):
        for group in self.groups.values():
            for item in group:
                match = self.pattern.match(item.name)
                if match:
                    yield match, item, group

    @staticmethod
    def lines_saved(item):
        prefixes, items = item
        return (len(prefixes) - 1) * len(items) - 5

    @staticmethod
    def powerset(iterable, start=0):
        "powerset([1,2,3]) --> () (1,) (2,) (3,) (1,2) (1,3) (2,3) (1,2,3)"
        s = list(iterable)
        return itertools.chain.from_iterable(itertools.combinations(s, r) for r in range(start, len(s)+1))

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
        return crt_file and cls(crt_file)

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

        print('{:,} settings written to {}'.format(len(diff), args.output))

    else:
        print('No differences found.')

if __name__ == '__main__':
    import sys
    main(sys.argv[1:])