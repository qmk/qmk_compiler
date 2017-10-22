import logging
import qmk_redis
from glob import glob
from os import chdir
from os.path import exists
from qmk_commands import checkout_qmk, memoize
from rq.decorators import job
from subprocess import check_output, STDOUT
from time import strftime

default_key_entry = {'x':-1, 'y':-1, 'w':1}


@memoize
def list_keyboards():
    """Extract the list of keyboards from qmk_firmware.
    """
    chdir('qmk_firmware')
    try:
        keyboards = check_output(('make', 'list-keyboards'), stderr=STDOUT, universal_newlines=True)
        keyboards = keyboards.strip()
        keyboards = keyboards.split('\n')[-1]
    finally:
        chdir('..')
    return keyboards.split()


@memoize
def find_all_layouts(keyboard):
    """Looks for layout macros associated with this keyboard.
    """
    layouts = {}
    rules_mk = parse_rules_mk('qmk_firmware/keyboards/'+keyboard+'/rules.mk')

    # First look for `<keyboard>/<keyboard>.h` or `<keyboard>/[folder.../]<folder>/<folder.h>` and prefer those files if they exist.
    if 'DEFAULT_FOLDER' in rules_mk:
        include_filename = rules_mk['DEFAULT_FOLDER'].split('/')[-1] + '.h'
        keyboard_include = '/'.join(('qmk_firmware/keyboards', rules_mk['DEFAULT_FOLDER'], include_filename))
    else:
        include_filename = keyboard.split('/')[-1] + '.h'
        keyboard_include = '/'.join(('qmk_firmware/keyboards/', keyboard, include_filename))

    if exists(keyboard_include):
        layouts.update(find_layouts(keyboard_include))
    else:
        # If we can't guess the correct file we have to search for it. This is error
        # prone which is why we want to encourage people to follow the standard above.
        logging.warning('Falling back to searching for KEYMAP/LAYOUT macros.')

        if 'DEFAULT_FOLDER' in rules_mk:
            layout_dir = 'qmk_firmware/keyboards/' + rules_mk['DEFAULT_FOLDER']
        else:
            layout_dir = 'qmk_firmware/keyboards/' + keyboard

        for file in glob(layout_dir + '/*.h'):
            if file.endswith('.h'):
                these_layouts = find_layouts(file)
                if these_layouts:
                    keyboard_include = file
                    layouts.update(these_layouts)
                    break

    if 'LAYOUTS' in rules_mk:
        # Match these up against the supplied layouts
        supported_layouts = rules_mk['LAYOUTS'].strip().split()
        for layout_name in sorted(layouts):
            if not layout_name.startswith('LAYOUT_'):
                continue
            layout_name = layout_name[7:]
            if layout_name in supported_layouts:
                supported_layouts.remove(layout_name)

        if supported_layouts:
            logging.warning('*** Missing layout pp macro for %s', supported_layouts)

    return layouts


def parse_rules_mk(file, rules_mk=None):
    """Turn a rules.mk file into a dictionary.
    """
    if not rules_mk:
        rules_mk = {}

    if not exists(file):
        return {}

    for line in open(file).readlines():
        line = line.strip().split('#')[0]
        if not line:
            continue

        if '=' in line:
            if '+=' in line:
                key, value = line.split('+=')
                if key.strip() not in rules_mk:
                    rules_mk[key.strip()] = value.strip()
                else:
                    rules_mk[key.strip()] += ' ' + value.strip()
            elif '=' in line:
                key, value = line.split('=', 1)
                rules_mk[key.strip()] = value.strip()

    return rules_mk


def default_key(entry=None):
    """Increment x and return a copy of the default_key_entry.
    """
    default_key_entry['x'] += 1
    return default_key_entry.copy()


@memoize
def find_layouts(file):
    """Returns list of parsed layout macros found in the supplied file.
    """
    aliases = {}  # Populated with all `#define`s that aren't functions
    source_code=open(file).readlines()
    writing_keymap=False
    discovered_keymaps=[]
    parsed_keymaps={}
    current_keymap=[]
    for line in source_code:
        if not writing_keymap:
            if '#define' in line and '(' in line and ('LAYOUT' in line or 'KEYMAP' in line):
                writing_keymap=True
            elif '#define' in line:
                try:
                    _, pp_macro_name, pp_macro_text = line.strip().split(' ', 2)
                    aliases[pp_macro_name] = pp_macro_text
                except ValueError:
                    continue
        if writing_keymap:
            current_keymap.append(line.strip()+'\n')
            if ')' in line:
                writing_keymap=False
                discovered_keymaps.append(''.join(current_keymap))
                current_keymap=[]

    for keymap in discovered_keymaps:
        # Clean-up the keymap text, extract the macro name, and end up with a list
        # of key entries.
        keymap = keymap.replace('\\', '').replace(' ', '').replace('#define', '')
        macro_name, keymap = keymap.split('(', 1)
        keymap = keymap.split(')', 1)[0]

        # Reject any macros that don't start with `KEYMAP` or `LAYOUT`
        if not (macro_name.startswith('KEYMAP') or macro_name.startswith('LAYOUT')):
            continue

        # Parse the keymap entries into naive x/y data
        parsed_keymap = []
        default_key_entry['y'] = -1
        for row in keymap.strip().split('\n'):
            default_key_entry['x'] = -1
            default_key_entry['y'] += 1
            parsed_keymap.extend([default_key() for key in row.split(',')])
        parsed_keymaps[macro_name] = parsed_keymap

    to_remove = set()
    for alias, text in aliases.items():
        if text in parsed_keymaps:
            parsed_keymaps[alias] = parsed_keymaps[text]
            to_remove.add(text)
    for macro in to_remove:
        del(parsed_keymaps[macro])

    return parsed_keymaps


@job('default', connection=qmk_redis.redis)
def update_kb_redis():
    checkout_qmk()
    kb_list = []
    cached_json = {'generated_at': strftime('%Y-%m-%d %H:%M:%S %Z'), 'keyboards': {}}
    for keyboard in list_keyboards():
        keyboard_info = {
            'name': keyboard,
            'maintainer': 'TBD',
            'layouts': {}
        }
        for layout_name, layout_json in find_all_layouts(keyboard).items():
            keyboard_info['layouts'][layout_name] = layout_json
        qmk_redis.set('qmk_api_kb_'+keyboard, keyboard_info)
        kb_list.append(keyboard)
        cached_json['keyboards'][keyboard] = keyboard_info

    qmk_redis.set('qmk_api_keyboards', kb_list)
    qmk_redis.set('qmk_api_kb_all', cached_json)

    return True


if __name__ == '__main__':
    update_kb_redis()
