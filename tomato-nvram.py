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

def write_script(values, outfile):
    '''
    Write values to script_file in the form:

        nvram set name1=value1
        nvram set name2=value2
        nvram set name3='multi
        line
        value3'
    '''
    # Sections
    for section, items in groupby_sections(values):
        outfile.write('\n# {}\n'.format(section))
        for name, value in sorted(items.items(), key=lambda item: (bool(multiline_chars.search(item[1])), item[0])):
            if '>' in value or '\n' in value:
                value = re.sub(r'^|(?<=>)(?!$)', '\\\n', value)
            outfile.write('nvram set {}={}\n'.format(name, shlex.quote(value)))

    # Certificate
    if 'https_crt_file' in values:
        crt_file = tarfile.open(fileobj=io.BytesIO(base64.b64decode(values['https_crt_file'])))
        cert = crt_file.extractfile('etc/cert.pem').read().decode()
        key = crt_file.extractfile('etc/key.pem').read().decode()
        outfile.write(crt_template.format(cert=cert.strip(), key=key.strip()))

    # Commit
    outfile.write('\n# Save\nnvram commit\n')

def groupby_sections(values, other='Other'):
    parser = configparser.ConfigParser()
    parser.read('config.ini')
    patterns = {section: re.compile(items['pattern']) for section, items in parser.items() if 'pattern' in items}
    for section, pattern in patterns.items():
        section_values = {name: values[name] for name in values if pattern.match(name) and not ignore_names.match(name)}
        if section_values:
            yield section, section_values
            values = {name: values[name] for name in values if name not in section_values}
    values = {name: values[name] for name in values if not ignore_names.match(name)}
    if values and other:
        yield other, values

crt_template = '''
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
            write_script(dict(diff), outfile)

if __name__ == '__main__':
    import sys
    main(sys.argv[1:])