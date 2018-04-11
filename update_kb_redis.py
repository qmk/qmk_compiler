from glob import glob
from os import chdir, listdir
from os.path import exists
from subprocess import check_output, STDOUT, run, PIPE
from time import strftime
import json
import logging
import re

from rq.decorators import job

from qmk_commands import checkout_qmk, memoize, git_hash
from sparse_list import SparseList
import qmk_redis

debug = False
default_key_entry = {'x':-1, 'y':-1, 'w':1}
error_log = []

# Regexes
enum_re = re.compile(r'enum[^{]*[^}]*')
keymap_re = re.compile(r'constuint[0-9]*_t[PROGMEM]*keymaps[^;]*')
layers_re = re.compile(r'\[[^\]]*]=[0-9A-Z_]*\([^[]*\)')
layout_macro_re = re.compile(r']=(LAYOUT[0-9a-z_]*)\(')
keymap_macro_re = re.compile(r']=(KEYMAP[0-9a-z_]*)\(')


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
    rules_mk = parse_rules_mk(keyboard)
    keyboard_path = rules_mk.get('DEFAULT_FOLDER', keyboard)

    # Pull in all keymaps defined in the standard files
    current_path = 'qmk_firmware/keyboards/'
    for directory in keyboard_path.split('/'):
        current_path += directory + '/'
        if exists('%s/%s.h' % (current_path, directory)):
            layouts.update(find_layouts('%s/%s.h' % (current_path, directory)))

    if not layouts:
        # If we didn't find any layouts above we widen our search. This is error
        # prone which is why we want to encourage people to follow the standard above.
        error_msg = '%s: Falling back to searching for KEYMAP/LAYOUT macros.' % (keyboard)
        error_log.append('Warning: ' + error_msg)
        logging.warning(error_msg)
        for file in glob('qmk_firmware/%s/*.h' % keyboard):
            if file.endswith('.h'):
                these_layouts = find_layouts(file)
                if these_layouts:
                    layouts.update(these_layouts)

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
            error_msg = '%s: Missing layout pp macro for %s' % (keyboard, supported_layouts)
            error_log.append('Warning: ' + error_msg)
            logging.warning(error_msg)

    return layouts


def parse_config_h(keyboard):
    """Parses all the config_h.mk files for a keyboard.
    """
    rules_mk = parse_rules_mk(keyboard)
    config_h = parse_config_h_file('qmk_firmware/keyboards/%s/config.h' % keyboard)

    if 'DEFAULT_FOLDER' in rules_mk:
        keyboard = rules_mk['DEFAULT_FOLDER']
        config_h = parse_config_h_file('qmk_firmware/keyboards/%s/%s/config.h' % (keyboard, rules_mk['DEFAULT_FOLDER']), config_h)

    return config_h


def parse_config_h_file(file, config_h=None):
    """Extract defines from a config.h file.
    """
    if not config_h:
        config_h = {}

    if exists(file):
        for linenum, line in enumerate(open(file).readlines()):
            line = line.strip()
            if not line:
                continue

            line = line.split()

            if line[0] == '#define':
                if len(line) == 1:
                    error_msg = '%s: Incomplete #define! On or around line %s' % (file, linenum)
                    error_log.append(error_msg)
                    logging.error(error_msg)
                elif len(line) == 2:
                    config_h[line[1]] = True
                else:
                    config_h[line[1]] = ' '.join(line[2:])

            elif line[0] == '#undef':
                if len(line) == 2:
                    if line[1] in config_h:
                        if config_h[line[1]] is True:
                            del(config_h[line[1]])
                        else:
                            config_h[line[1]] = False
                else:
                    error_msg = '%s: Incomplete #undef! On or around line %s' % (file, linenum)
                    error_log.append(error_msg)
                    logging.error(error_msg)

    return config_h


def parse_rules_mk(keyboard):
    """Parses all the rules.mk files for a keyboard.
    """
    rules_mk = parse_rules_mk_file('qmk_firmware/keyboards/%s/rules.mk' % keyboard)

    if 'DEFAULT_FOLDER' in rules_mk:
        keyboard = rules_mk['DEFAULT_FOLDER']
        rules_mk = parse_rules_mk_file('qmk_firmware/keyboards/%s/%s/rules.mk' % (keyboard, rules_mk['DEFAULT_FOLDER']), rules_mk)

    return rules_mk


def parse_rules_mk_file(file, rules_mk=None):
    """Turn a rules.mk file into a dictionary.
    """
    if not rules_mk:
        rules_mk = {}

    if exists(file):
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


def default_key(label=None):
    """Increment x and return a copy of the default_key_entry.
    """
    default_key_entry['x'] += 1
    new_key = default_key_entry.copy()

    if label:
        new_key['label'] = label

    return new_key


def preprocess_source(file):
    """Run the keymap through `clang -E` to strip comments and populate #defines
    """
    results = run(['clang', '-E', file], stdout=PIPE, stderr=PIPE, universal_newlines=True)
    return results.stdout.replace(' ', '').replace('\n', '')


def popluate_enums(keymap_text, keymap):
    """Pull the enums from the file and assign them (hopefully) correct numbers.
    """
    replacements = {}
    for enum in enum_re.findall(keymap_text):
        enum = enum.split('{')[1]
        index = 0

        for define in enum.split(','):
            if '=' in define:
                define, new_index = define.split('=')

                if new_index == 'SAFE_RANGE':
                    # We should skip keycode enums
                    break

                if not new_index.isdigit():
                    if new_index in replacements:
                        index = replacements[new_index]
                else:
                    index = new_index

                index = int(index)

            replacements[define] = index  # Last one wins in case of conflict
            index += 1

    # Replace enums with their values
    for replacement in replacements:
        keymap = keymap.replace(replacement, str(replacements[replacement]))

    return keymap


def extract_layouts(keymap_text, keymap_file):
    """Returns a list of layouts from keymap_text.
    """
    try:
        keymap = keymap_re.findall(keymap_text)[0]
    except IndexError:
        logging.error('Could not extract LAYOUT for %s!', keymap_file)
        return None

    return keymap


def extract_keymap(keymap_file):
    """Extract the keymap from a file.
    """
    layer_index = 0
    layers = SparseList()
    keymap_text = preprocess_source(keymap_file)
    keymap = extract_layouts(keymap_text, keymap_file)

    if not keymap:
        return '', layers

    layout_macro = layout_macro_re.findall(keymap_text)
    if not layout_macro:
        layout_macro = keymap_macro_re.findall(keymap_text)
    layout_macro = layout_macro[0] if layout_macro else ''

    keymap = popluate_enums(keymap_text, keymap)

    # Parse layers into a correctly ordered list
    for layer in layers_re.findall(keymap):
        layer_num, _, layer = layer.partition('=')
        layer = layer.split('(', 1)[1].rsplit(')', 1)[0]
        layer_num = layer_num.replace('[', '').replace(']', '')

        if not layer_num or not layer_num.isdigit():
            layer_num = layer_index
            layer_index += 1
        layers[int(layer_num)] = layer.split(',')

    return layout_macro, layers


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
        keymap = keymap.replace('\\', '').replace(' ', '').replace('\t','').replace('#define', '')
        macro_name, keymap = keymap.split('(', 1)
        keymap = keymap.split(')', 1)[0]

        # Reject any macros that don't start with `KEYMAP` or `LAYOUT`
        if not (macro_name.startswith('KEYMAP') or macro_name.startswith('LAYOUT')):
            continue

        # Parse the keymap entries into naive x/y data
        parsed_keymap = []
        default_key_entry['y'] = -1
        for row in keymap.strip().split(',\n'):
            default_key_entry['x'] = -1
            default_key_entry['y'] += 1
            parsed_keymap.extend([default_key(key) for key in row.split(',')])
        parsed_keymaps[macro_name] = {
            'key_count': len(parsed_keymap),
            'layout': parsed_keymap
        }

    to_remove = set()
    for alias, text in aliases.items():
        if text in parsed_keymaps:
            parsed_keymaps[alias] = parsed_keymaps[text]
            to_remove.add(text)
    for macro in to_remove:
        del(parsed_keymaps[macro])

    return parsed_keymaps


@memoize
def find_info_json(keyboard):
    """Finds all the info.json files associated with keyboard.
    """
    info_json_path = 'qmk_firmware/keyboards/%s%s/info.json'
    rules_mk_path = 'qmk_firmware/keyboards/%s/rules.mk' % keyboard
    files = []

    for path in ('/../../../..', '/../../..', '/../..', '/..', ''):
        if (exists(info_json_path % (keyboard, path))):
            files.append(info_json_path % (keyboard, path))

    if exists(rules_mk_path):
        rules_mk = parse_rules_mk_file(rules_mk_path)
        if 'DEFAULT_FOLDER' in rules_mk:
            if (exists(info_json_path % (rules_mk['DEFAULT_FOLDER'], path))):
                files.append(info_json_path % (rules_mk['DEFAULT_FOLDER'], path))

    return files


@memoize
def find_keymaps(keyboard):
    """Yields the keymaps for a particular keyboard.
    """
    keymaps_path = 'qmk_firmware/keyboards/%s%s/keymaps'
    keymaps = []

    for path in ('/../../../..', '/../../..', '/../..', '/..', ''):
        if (exists(keymaps_path % (keyboard, path))):
            keymaps.append(keymaps_path % (keyboard, path))

    for keymap_folder in keymaps:
        for keymap in listdir(keymap_folder):
            keymap_file = '%s/%s/keymap.c' % (keymap_folder, keymap)
            if exists(keymap_file):
                layout_macro, layers = extract_keymap(keymap_file)
                yield (keymap, keymap_folder, layout_macro, layers)


def merge_info_json(info_fd, keyboard_info):
    try:
        info_json = json.load(info_fd)
    except Exception as e:
        error_msg = "%s is invalid JSON: %s" % (info_fd.name, e)
        error_log.append('Error: ' + error_msg)
        logging.error(error_msg)
        logging.exception(e)
        return keyboard_info

    if not isinstance(info_json, dict):
        error_msg = "%s is invalid! Should be a JSON dict object."% (info_fd.name)
        error_log.append('Error: ' + error_msg)
        logging.error(error_msg)
        return keyboard_info

    for key in ('keyboard_name', 'manufacturer', 'identifier', 'url', 'maintainer', 'processor', 'bootloader', 'width', 'height'):
        if key in info_json:
            keyboard_info[key] = info_json[key]

    if 'layouts' in info_json:
        for layout_name, layout in info_json['layouts'].items():
            # Only pull in layouts we have a macro for
            if layout_name in keyboard_info['layouts']:
                if len(keyboard_info['layouts'][layout_name]['layout']) != len(layout['layout']):
                    error_msg = '%s: %s: Number of elements in info.json does not match! info.json:%s != %s:%s' % (keyboard_info['keyboard_folder'], layout_name, len(keyboard_info['layouts'][layout_name]['layout']), layout_name, len(layout['layout']))
                    error_log.append('Error: ' + error_msg)
                    logging.error(error_msg)
                else:
                    keyboard_info['layouts'][layout_name]['layout'] = layout['layout']

    return keyboard_info


def find_readme(directory):
    """Find the readme.md file in a case insensitive way.
    """
    for file in listdir(directory):
        if file.lower() == 'readme.md':
            return '/'.join((directory, file))
    return ''

@job('default', connection=qmk_redis.redis)
def update_kb_redis():
    del(error_log[:])  # Empty the error log

    if debug:
        #keyboards_iterator = ['planck']
        if not exists('qmk_firmware'):
            checkout_qmk()
        keyboards_iterator = list_keyboards()
    else:
        checkout_qmk()
        keyboards_iterator = list_keyboards()

    last_update = qmk_redis.get('qmk_api_last_updated')
    if not debug and isinstance(last_update, dict) and last_update['git_hash'] == git_hash():
        # We are already up to date
        logging.warning('update_kb_redis(): Already up to date, skipping...')
        return False

    kb_list = []
    cached_json = {'last_updated': strftime('%Y-%m-%d %H:%M:%S %Z'), 'keyboards': {}}
    for keyboard in keyboards_iterator:
        keyboard_info = {
            'keyboard_name': keyboard,
            'keyboard_folder': keyboard,
            'keymaps': [],
            'layouts': {},
            'maintainer': 'qmk',
        }
        for layout_name, layout_json in find_all_layouts(keyboard).items():
            keyboard_info['layouts'][layout_name] = layout_json

        for info_json_filename in find_info_json(keyboard):
            # Iterate through all the possible info.json files to build the final keyboard JSON.
            try:
                with open(info_json_filename) as info_file:
                    keyboard_info = merge_info_json(info_file, keyboard_info)
            except Exception as e:
                error_msg = 'Error encountered processing %s! %s: %s' % (keyboard, e.__class__.__name__, e)
                error_log.append('Error: ' + error_msg)
                logging.error(error_msg)
                logging.exception(e)

        # Iterate through all the possible keymaps to build keymap jsons.
        for keymap_name, keymap_folder, layout_macro, keymap in find_keymaps(keyboard):
            keyboard_info['keymaps'].append(keymap_name)
            keymap_blob = {
                'keyboard_name': keyboard,
                'keymap_name': keymap_name,
                'keymap_folder': keymap_folder,
                'layers': keymap,
                'layout_macro': layout_macro
            }

            # Write the keymap to redis
            qmk_redis.set('qmk_api_kb_%s_keymap_%s' % (keyboard, keymap_name), keymap_blob)
            readme = '%s/%s/readme.md' % (keymap_folder, keymap_name)
            if exists(readme):
                qmk_redis.set('qmk_api_kb_%s_keymap_%s_readme' % (keyboard, keymap_name), open(readme).read())
            else:
                qmk_redis.set('qmk_api_kb_%s_keymap_%s_readme' % (keyboard, keymap_name), '%s does not exist.' % readme)

        # Pull some keyboard information from existing rules.mk and config.h files
        config_h = parse_config_h(keyboard)
        rules_mk = parse_rules_mk(keyboard)

        for key in ('VENDOR_ID', 'PRODUCT_ID', 'DEVICE_VER', 'MANUFACTURER', 'DESCRIPTION'):
            if key in config_h:
                if key in ('VENDOR_ID', 'PRODUCT_ID', 'DEVICE_VER'):
                    config_h[key].replace('0x', '')
                    config_h[key] = config_h[key].upper()
                keyboard_info[key.lower()] = config_h[key]

        if 'ARMV' in rules_mk:
            # ARM processors
            if 'MCU' in rules_mk:
                keyboard_info['platform'] = rules_mk['MCU_LDSCRIPT']
            if 'MCU_LDSCRIPT' in rules_mk:
                keyboard_info['processor'] = rules_mk['MCU_LDSCRIPT']
            if 'BOOTLOADER' in rules_mk:
                keyboard_info['bootloader'] = rules_mk['BOOTLOADER']
            if 'bootloader' not in keyboard_info:
                if 'STM32' in keyboard_info['processor']:
                    keyboard_info['bootloader'] = 'stm32-dfu'
                elif keyboard_info.get('manufacturer') == 'Input Club':
                    keyboard_info['bootloader'] = 'kiibohd-dfu'
        else:
            # AVR processors
            if 'ARCH' in rules_mk:
                keyboard_info['platform'] = rules_mk['ARCH']
            if 'MCU' in rules_mk:
                keyboard_info['processor'] = rules_mk['MCU']
            if 'BOOTLOADER' in rules_mk:
                keyboard_info['bootloader'] = rules_mk['BOOTLOADER']
            if 'bootloader' not in keyboard_info:
                keyboard_info['bootloader'] = 'atmel-dfu'

        keyboard_info['identifier'] = ':'.join((keyboard_info.get('vendor_id', 'unknown'), keyboard_info.get('product_id', 'unknown'), keyboard_info.get('device_ver', 'unknown')))

        # Store the keyboard's readme in redis
        readme_filename = None
        readme_path = ''
        for dir in keyboard.split('/'):
            readme_path = '/'.join((readme_path, dir))
            new_name = find_readme('qmk_firmware/keyboards%s' % (readme_path))
            if new_name:
                readme_filename = new_name  # Last one wins

        if readme_filename:
            qmk_redis.set('qmk_api_kb_%s_readme' % (keyboard), open(readme_filename).read())
            keyboard_info['readme'] = True
        else:
            error_msg = '%s does not have a readme.md.' % keyboard
            qmk_redis.set('qmk_api_kb_%s_readme' % (keyboard), error_msg)
            error_log.append('Warning: ' + error_msg)
            logging.warning(error_msg)
            keyboard_info['readme'] = False

        # Write the keyboard to redis and add it to the master list.
        qmk_redis.set('qmk_api_kb_%s' % (keyboard), keyboard_info)
        kb_list.append(keyboard)
        cached_json['keyboards'][keyboard] = keyboard_info

    # Update the global redis information
    qmk_redis.set('qmk_api_keyboards', kb_list)
    qmk_redis.set('qmk_api_kb_all', cached_json)
    qmk_redis.set('qmk_api_last_updated', {'git_hash': git_hash(), 'last_updated': strftime('%Y-%m-%d %H:%M:%S %Z')})
    qmk_redis.set('qmk_api_update_error_log', error_log)

    return True


if __name__ == '__main__':
    debug = True
    update_kb_redis()
