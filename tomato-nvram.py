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

    # Dedup repeated settings.
    groups.dedup()

    # Write groups.
    outfile.write(groups.formatted())

    # Certificate
    if crt_file:
        outfile.write(crt_file.formatted())

    # Commit
    outfile.write('\n# Save\nnvram commit\n')

from collections import defaultdict
class Groups(defaultdict):
    '''
    Container for groups/sections.
    '''
    def __init__(self, items, config):
        super().__init__()
        self.config = config
        for item in items:
            item = Item(*item)
            self[config.groupname(item)].append(item)

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

    def dedup(self, minsize=3):
        '''
        Factor out common settings.
        '''
        Deduper(self, self.config).dedup(minsize)

    def formatted(self):
        groups = sorted(self.values(), key=lambda group: group.sort_key)
        return '\n'.join(group.formatted() for group in groups)

    def scan(self, pattern):
        '''
        Yield (item, group, match) for each item whose name matches pattern.
        '''
        for group in self.values():
            for item in group:
                match = pattern.match(item.name)
                if match:
                    yield item, group, match

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

    @classmethod
    def loop(cls, name, rank, prefixes, keys):
        prefix = commonprefix(prefixes)
        items = (Item('${{{}}}{}'.format(prefix, suffix), value) for suffix, value in keys)
        prefixes = ' '.join(sorted(prefixes))
        return cls(name, rank, items,
                   prefix='for {} in {}\ndo'.format(prefix, prefixes),
                   suffix='done')

import shlex
class Item:
    '''
    Format a single item.
    '''
    def __init__(self, name, value):
        self.name = name
        self.value = value
        self.__key = name, value

        parts = self.name_break.split(name)
        self.prefix = parts[0] if len(parts) > 1 else re.match('[a-z]*', name).group()
        self.suffix = parts[-1]

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

    def groupname(self):
        if len(self.prefix) <= 4:
            return self.prefix.upper()
        else:
            return self.prefix.capitalize()

    @classmethod
    def quoted(cls, value):
        # Use double quotes with an initial newline if value contains newlines.
        if '\n' in value and not cls.special_chars.search(value):
            return '"\\\n{}"'.format(value)
            
        # Format tomato lists in double quotes, one item per line.
        list, found = cls.list_break.subn('\\\n', value)
        if found and not cls.special_chars.search(value):
            return '"\\\n{}"'.format(list)

        # Use double quotes if value contains a single quote.
        if "'" in value:
            return '"{}"'.format(cls.special_chars.sub(r'\\\g<0>', value))

        # Otherwise let shlex.quote handle it with single quotes.
        return shlex.quote(value) if value else value

    special_chars = re.compile(r'["\\`]|\$(?=\S)')  # Require escaping in double quotes
    list_break = re.compile(r'(?<=>)(?!$)')         # Where to break tomato lists
    name_break = re.compile(r'_|:')                 # Where to break names

from functools import partial
import itertools
class Deduper:
    '''
    Factor out common settings into loops.
    '''
    def __init__(self, groups, config):
        self.groups = groups
        self.config = config

        self.prefix_to_keys = defaultdict(set)
        self.group_names = dict()
        self.cleanup = dict()
        for item, group, match in groups.scan(self.prefix_pattern):
            prefix = match.group()
            key = item.name[match.end():], item.value
            self.prefix_to_keys[prefix].add(key)
            self.group_names[prefix, key] = group.name
            self.cleanup[prefix, key] = partial(self.remove_item, item, group, groups)

    def commonkeys(self, prefixes):
        return set.intersection(*(self.prefix_to_keys[prefix] for prefix in prefixes))

    def dedup(self, minsize=3):
        for prefixes, keys in self.to_factor(minsize):
            name = commonprefix(self.group_names[prefix, key] for prefix in prefixes for key in keys)
            group = Group.loop(name, self.config.rank[name], prefixes, keys)
            self.groups[id(group)] = group
            self.remove(prefixes, keys)

    def lines_saved(self, prefixes, keys=None):
        if len(prefixes) > 1:
            keys = keys or self.commonkeys(prefixes)
            saved = (len(prefixes) - 1) * len(keys) - 5
            if saved > 0:
                return saved
        return 0

    def prefix_groups(self, minsize=2):
        grouped = itertools.groupby(sorted(self.prefix_to_keys), key=lambda item: item[0:minsize])
        powerset = itertools.chain.from_iterable(self.powerset(prefixes, 2) for _, prefixes in grouped)
        return filter(self.lines_saved, powerset)

    def remove(self, prefixes, keys):
        for prefix in prefixes:
            self.prefix_to_keys[prefix].difference_update(keys)
            for key in keys:
                self.cleanup[prefix, key]()

    def to_factor(self, minsize=3):
        for prefixes in sorted(self.prefix_groups(), key=self.lines_saved, reverse=True):
            keys = self.commonkeys(prefixes)
            if len(keys) >= minsize and self.lines_saved(prefixes, keys) > 0:
                yield prefixes, keys

    @staticmethod
    def powerset(iterable, start=0):
        "powerset([1,2,3]) --> () (1,) (2,) (3,) (1,2) (1,3) (2,3) (1,2,3)"
        s = list(iterable)
        return itertools.chain.from_iterable(itertools.combinations(s, r) for r in range(start, len(s)+1))

    @staticmethod
    def remove_item(item, group, groups):
        group.remove(item)
        if not group:
            del groups[group.name]

    prefix_pattern = re.compile(r'([a-z]+_[a-z]+\d+)|[a-z]+\d*(?=_)')

import os.path
import string
def commonprefix(names):
    return os.path.commonprefix(list(names)).strip(string.punctuation + string.whitespace)

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
        self.rank = defaultdict(lambda: len(self.names), ((name, i) for i, name in enumerate(self.names)))
        self.rank['Other'] = len(self.rank) + 1

    def groupname(self, item):
        match = self.lookup.match(item.name)
        return self.names[match.lastindex - 1] if match else item.groupname()

    def collapsible(self, group):
        return group.rank == len(self.names)

    def getrank(self, itemname):
        match = self.lookup.match(itemname)
        return self.rank[self.names[match.lastindex - 1]] if match else len(self.names)

import argparse
parser = argparse.ArgumentParser(description='Generate NVRAM setting shell script.',
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument('-i', '--input',  default='nvram.txt',    help='input filename')
parser.add_argument('-b', '--base',   default='defaults.txt', help='base filename')
parser.add_argument('-o', '--output', default='set-nvram.sh', help='output filename')
parser.add_argument('-c', '--config', default='config.ini',   help='config filename')

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