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
(?P<name>[a-z0-9_.:/]+) # Name
=                       # Equals
(?P<value>.*?)          # Value
\n(?=                   # Followed by
[a-z0-9_.:/]+=          # Next stanza
|---\n                  # Or prelude on MIPS
|$)                     # Or end of file
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
    for section, section_items in groupby_sections(items):
        outfile.write('# {}\n'.format(section))
        for name, value in sorted(section_items, key=lambda item: (bool(multiline_chars.search(item[1])), item[0])):
            if '>' in value or '\n' in value:
                value = re.sub(r'^|(?<=>)(?!$)', '\\\n', value)
            outfile.write('nvram set {}={}\n'.format(name, shlex.quote(value)))
        outfile.write('\n')

    # Certificate
    crt_file = dict(items).get('https_crt_file')
    if crt_file:
        crt_file = tarfile.open(fileobj=io.BytesIO(base64.b64decode(crt_file)))
        cert = crt_file.extractfile('etc/cert.pem').read().decode()
        key = crt_file.extractfile('etc/key.pem').read().decode()
        outfile.write(crt_template.format(cert=cert.strip(), key=key.strip()))

    # Commit
    outfile.write('# Save\nnvram commit\n')

def groupby_sections(items):

    # Load section patterns.
    parser = configparser.ConfigParser()
    parser.read('config.ini')
    section_names, patterns = zip(*((name, section['pattern']) for name, section in parser.items() if 'pattern' in section))

    # Group items from values into sections based on pattern matched.
    lookup = re.compile('|'.join('({})'.format(pattern) for pattern in patterns))
    sections = {name: [] for name in section_names}
    for item in items:
        name, value = item
        match = lookup.match(name)
        ignore = ignore_names.match(name)
        if match and not ignore:
            sections[section_names[match.lastindex - 1]].append(item)

    return ((name, items) for name, items in sections.items() if items)

crt_template = '''\
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