from glob import glob
from os import chdir, listdir, remove, mkdir
from os.path import exists
from shutil import rmtree
from subprocess import check_output, STDOUT, run, PIPE
from time import strftime
import json
import logging
import re

from bs4 import UnicodeDammit
from rq.decorators import job

import qmk_redis
from qmk_commands import checkout_qmk, memoize, git_hash

debug = False
default_key_entry = {'x':-1, 'y':-1, 'w':1}
error_log = []

# Regexes
enum_re = re.compile(r'enum[^{]*[^}]*')
keymap_re = re.compile(r'constuint[0-9]*_t[PROGMEM]*keymaps[^;]*')
layers_re = re.compile(r'\[[^\]]*]=[0-9A-Z_]*\([^[]*\)')
layout_macro_re = re.compile(r']=(LAYOUT[0-9a-z_]*)\(')
keymap_macro_re = re.compile(r']=(KEYMAP[0-9a-z_]*)\(')

# Processors
ARM_PROCESSORS = 'cortex-m0', 'cortex-m0plus', 'cortex-m3', 'cortex-m4', 'STM32F042', 'STM32F072', 'STM32F303'
AVR_PROCESSORS = 'at90usb1286', 'at90usb646', 'atmega16u2', 'atmega32a', 'atmega32u2', 'atmega32u4'


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
        error_log.append({'severity': 'warning', 'message': 'Warning: ' + error_msg})
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
            error_log.append({'severity': 'warning', 'message': 'Warning: ' + error_msg})
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

            if '//' in line:
                line = line[:line.index('//')].strip()

            if not line:
                continue

            line = line.split()

            if line[0] == '#define':
                if len(line) == 1:
                    error_msg = '%s: Incomplete #define! On or around line %s' % (file, linenum)
                    error_log.append({'severity': 'error', 'message': 'Error: ' + error_msg})
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
                    error_log.append({'severity': 'error', 'message': 'Error: ' + error_msg})
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

            if '#' in line:
                line = line[:line.index('#')].strip()

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


def populate_enums(keymap_text, keymap):
    """Pull the enums from the file and assign them (hopefully) correct numbers.
    """
    replacements = {}
    for enum in enum_re.findall(keymap_text):
        if '{' not in enum:
            logging.error('Matched enum without a curlybrace? %s', enum)
            continue
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
    layers = {}  # Use a dict because we can't predict what order layers will be defined in
    keymap_text = preprocess_source(keymap_file)
    keymap = extract_layouts(keymap_text, keymap_file)

    if not keymap:
        return '', []

    # Extract layer definitions from the keymap
    layout_macro = layout_macro_re.findall(keymap_text)
    if not layout_macro:
        layout_macro = keymap_macro_re.findall(keymap_text)
    layout_macro = layout_macro[0] if layout_macro else ''

    keymap = populate_enums(keymap_text, keymap)

    # Parse layers into a dict keyed by layer number.
    for layer in layers_re.findall(keymap):
        layer_num, _, layer = layer.partition('=')
        layer = layer.split('(', 1)[1].rsplit(')', 1)[0]
        layer_num = layer_num.replace('[', '').replace(']', '')

        if not layer_num or not layer_num.isdigit():
            layer_num = layer_index
            layer_index += 1
        layers[int(layer_num)] = layer.split(',')


    # Turn the layers dict into a properly ordered list.
    layer_list = []
    if layers:
        max_int = sorted(layers.keys())[-1]
        for i in range(max_int+1):
            layer_list.append(layers.get(i))

    return layout_macro, layer_list


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

    for alias, text in aliases.items():
        if text in parsed_keymaps:
            parsed_keymaps[alias] = parsed_keymaps[text]

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
                # Skip any layout macro that ends with '_kc', they will not compile
                if not layout_macro.startswith('LAYOUT_kc'):
                    yield (keymap, keymap_folder, layout_macro, layers)


def merge_info_json(info_fd, keyboard_info):
    """Merge the parsed keyboard_info data with the parsed info.json and return the full JSON that will ultimately be stored in redis.
    """
    try:
        info_json = json.load(info_fd)
    except Exception as e:
        error_msg = "%s is invalid JSON: %s" % (info_fd.name, e)
        error_log.append({'severity': 'error', 'message': 'Error: ' + error_msg})
        logging.error(error_msg)
        logging.exception(e)
        return keyboard_info

    if not isinstance(info_json, dict):
        error_msg = "%s is invalid! Should be a JSON dict object."% (info_fd.name)
        error_log.append({'severity': 'error', 'message': 'Error: ' + error_msg})
        logging.error(error_msg)
        return keyboard_info

    for key in ('keyboard_name', 'manufacturer', 'identifier', 'url', 'maintainer', 'processor', 'bootloader', 'width', 'height'):
        if key in info_json:
            keyboard_info[key] = info_json[key]

    if 'layouts' in info_json:
        for layout_name, json_layout in info_json['layouts'].items():
            # Only pull in layouts we have a macro for
            if layout_name in keyboard_info['layouts']:
                if len(keyboard_info['layouts'][layout_name]['layout']) != len(json_layout['layout']):
                    error_msg = '%s: %s: Number of elements in info.json does not match! info.json:%s != %s:%s' % (keyboard_info['keyboard_folder'], layout_name, len(json_layout['layout']), layout_name, len(keyboard_info['layouts'][layout_name]['layout']))
                    error_log.append({'severity': 'error', 'message': 'Error: ' + error_msg})
                    logging.error(error_msg)
                else:
                    keyboard_info['layouts'][layout_name]['layout'] = json_layout['layout']

    return keyboard_info


def find_readme(directory):
    """Find the readme.md file in a case insensitive way.
    """
    for file in listdir(directory):
        if file.lower() == 'readme.md':
            return '/'.join((directory, file))
    return ''


@job('default', connection=qmk_redis.redis)
def update_needed(**update_info):
    """Called when updates happen to QMK Firmware.
    """
    qmk_redis.set('qmk_needs_update', True)


@job('default', connection=qmk_redis.redis)
def update_kb_redis():
    """Called to update qmk_firmware.
    """
    # Clean up the environment and fetch the latest source
    del(error_log[:])
    if not debug:
        if exists('update_kb_redis'):
            rmtree('update_kb_redis')
        mkdir('update_kb_redis')
    chdir('update_kb_redis')
    qmk_redis.set('qmk_needs_update', False)

    if not debug:
        checkout_qmk(skip_cache=True)

    # Update redis with the latest data
    kb_list = []
    usb_list = {}  # Structure: VENDOR_ID: {PRODUCT_ID: {KEYBOARD_FOLDER: {'vendor_id': VENDOR_ID, 'product_id': PRODUCT_ID, 'device_ver': DEVICE_VER, 'manufacturer': MANUFACTURER, 'product': PRODUCT, 'keyboard': KEYBOARD_FOLDER}

    cached_json = {'last_updated': strftime('%Y-%m-%d %H:%M:%S %Z'), 'keyboards': {}}
    for keyboard in list_keyboards():
        keyboard_info = {
            'keyboard_name': keyboard,
            'keyboard_folder': keyboard,
            'keymaps': [],
            'layouts': {},
            'maintainer': 'qmk',
        }
        for layout_name, layout_json in find_all_layouts(keyboard).items():
            if not layout_name.startswith('LAYOUT_kc'):
                keyboard_info['layouts'][layout_name] = layout_json

        for info_json_filename in find_info_json(keyboard):
            # Iterate through all the possible info.json files to build the final keyboard JSON.
            try:
                with open(info_json_filename) as info_file:
                    keyboard_info = merge_info_json(info_file, keyboard_info)
            except Exception as e:
                error_msg = 'Error encountered processing %s! %s: %s' % (keyboard, e.__class__.__name__, e)
                error_log.append({'severity': 'error', 'message': 'Error: ' + error_msg})
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
                with open(readme, 'rb') as readme_fd:
                    readme_text = readme_fd.read()
                readme_text = UnicodeDammit(readme_text)
                readme_text = readme_text.unicode_markup
            else:
                readme_text = '%s does not exist.' % readme
            qmk_redis.set('qmk_api_kb_%s_keymap_%s_readme' % (keyboard, keymap_name), readme_text)

        # Pull some keyboard information from existing rules.mk and config.h files
        config_h = parse_config_h(keyboard)
        rules_mk = parse_rules_mk(keyboard)

        usb_entry = {'keyboard': keyboard}
        for key in ('VENDOR_ID', 'PRODUCT_ID', 'DEVICE_VER', 'MANUFACTURER', 'DESCRIPTION'):
            if key in config_h:
                if key in ('VENDOR_ID', 'PRODUCT_ID', 'DEVICE_VER'):
                    config_h[key] = config_h[key].upper().replace('0X', '')
                    config_h[key] = '0x' + config_h[key]
                keyboard_info[key.lower()] = config_h[key]
                usb_entry[key.lower()] = config_h[key]

        # Populate the usb_list entry for this keyboard
        vendor_id = usb_entry.get('vendor_id', '0xFEED')
        product_id = usb_entry.get('product_id', '0x0000')

        if vendor_id not in usb_list:
            usb_list[vendor_id] = {}

        if product_id not in usb_list[vendor_id]:
            usb_list[vendor_id][product_id] = {}

        usb_list[vendor_id][product_id][keyboard] = usb_entry

        # Setup platform specific keys
        if rules_mk.get('MCU') in ARM_PROCESSORS:
            keyboard_info['processor_type'] = 'arm'
            keyboard_info['bootloader'] = rules_mk['BOOTLOADER'] if 'BOOTLOADER' in rules_mk else 'unknown'
            keyboard_info['processor'] = rules_mk['MCU'] if 'MCU' in rules_mk else 'unknown'
            if keyboard_info['bootloader'] == 'unknown':
                if 'STM32' in keyboard_info['processor']:
                    keyboard_info['bootloader'] = 'stm32-dfu'
                elif keyboard_info.get('manufacturer') == 'Input Club':
                    keyboard_info['bootloader'] = 'kiibohd-dfu'
            if 'STM32' in keyboard_info['processor']:
                keyboard_info['platform'] = 'STM32'
            elif 'MCU_SERIES' in rules_mk:
                keyboard_info['platform'] = rules_mk['MCU_SERIES']
            elif 'ARM_ATSAM' in rules_mk:
                keyboard_info['platform'] = 'ARM_ATSAM'
        elif rules_mk.get('MCU') in AVR_PROCESSORS:
            keyboard_info['processor_type'] = 'avr'
            keyboard_info['bootloader'] = rules_mk['BOOTLOADER'] if 'BOOTLOADER' in rules_mk else 'atmel-dfu'
            keyboard_info['platform'] = rules_mk['ARCH'] if 'ARCH' in rules_mk else 'unknown'
            keyboard_info['processor'] = rules_mk['MCU'] if 'MCU' in rules_mk else 'unknown'
        else:
            keyboard_info['bootloader'] = 'unknown'
            keyboard_info['platform'] = 'unknown'
            keyboard_info['processor'] = 'unknown'
            keyboard_info['processor_type'] = 'unknown'

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
            error_log.append({'severity': 'warning', 'message': 'Warning: ' + error_msg})
            logging.warning(error_msg)
            keyboard_info['readme'] = False

        # Write the keyboard to redis and add it to the master list.
        qmk_redis.set('qmk_api_kb_%s' % (keyboard), keyboard_info)
        kb_list.append(keyboard)
        cached_json['keyboards'][keyboard] = keyboard_info

    # Update the global redis information
    qmk_redis.set('qmk_api_keyboards', kb_list)
    qmk_redis.set('qmk_api_kb_all', cached_json)
    qmk_redis.set('qmk_api_usb_list', usb_list)
    qmk_redis.set('qmk_api_last_updated', {'git_hash': git_hash(), 'last_updated': strftime('%Y-%m-%d %H:%M:%S %Z')})
    qmk_redis.set('qmk_api_update_error_log', error_log)

    chdir('..')

    return True


if __name__ == '__main__':
    debug = True

    update_kb_redis()
