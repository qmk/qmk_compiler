from glob import glob
from os import chdir, listdir, mkdir
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
default_key_entry = {'x': -1, 'y': -1, 'w': 1}
error_log = []

# Regexes
enum_re = re.compile(r'enum[^{]*{[^}]*')
keymap_re = re.compile(r'constuint[0-9]*_t[PROGMEM]*keymaps[^;]*')
layers_re = re.compile(r'\[[^\]]*]=[0-9A-Z_]*\([^[]*\)')
layout_macro_re = re.compile(r']=(LAYOUT[0-9a-z_]*)\(')
keymap_macro_re = re.compile(r']=(KEYMAP[0-9a-z_]*)\(')

# Processors
ARM_PROCESSORS = 'cortex-m0', 'cortex-m0plus', 'cortex-m3', 'cortex-m4', 'MKL26Z64', 'MK20DX128', 'MK20DX256', 'STM32F042', 'STM32F072', 'STM32F103', 'STM32F303'
AVR_PROCESSORS = 'at90usb1286', 'at90usb646', 'atmega16u2', 'atmega328p', 'atmega32a', 'atmega32u2', 'atmega32u4', None


def log_error(message):
    """Writes a log message to both the std logging module and the JSON error_log.
    """
    error_log.append({'severity': 'error', 'message': 'Error: ' + message})
    logging.error(message)


def log_warning(message):
    """Writes a log message to both the std logging module and the JSON error_log.
    """
    error_log.append({'severity': 'warning', 'message': 'Warning: ' + message})
    logging.warning(message)


def unicode_text(filename):
    """Returns the contents of filename as a UTF-8 string. Tries to DTRT when it comes to encoding.
    """
    with open(filename, 'rb') as fd:
        text = UnicodeDammit(fd.read())

    if text.contains_replacement_characters:
        log_warning('%s: Could not determine file encoding, some characters were replaced.' % (filename,))

    return text.unicode_markup or ''


def unicode_lines(filename):
    """Returns the contents of filename as a UTF-8 string. Tries to DTRT when it comes to encoding.
    """
    return unicode_text(filename).split('\n')


def list_keyboards():
    """Extract the list of keyboards from qmk_firmware.
    """
    chdir('qmk_firmware')
    try:
        keyboards = check_output(('qmk', 'list-keyboards'), stderr=STDOUT, universal_newlines=True)
        keyboards = keyboards.strip()
        keyboards = keyboards.split('\n')[-1]
    finally:
        chdir('..')
    return keyboards.split()


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
        log_warning('%s: Falling back to searching for KEYMAP/LAYOUT macros.' % (keyboard))
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
            log_error('%s: Missing layout pp macro for %s' % (keyboard, supported_layouts))

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
        config_h_lines = unicode_lines(file)

        for linenum, line in enumerate(config_h_lines):
            line = line.strip()

            if '//' in line:
                line = line[:line.index('//')].strip()

            if not line:
                continue

            line = line.split()

            if line[0] == '#define':
                if len(line) == 1:
                    log_error('%s: Incomplete #define! On or around line %s' % (file, linenum))
                elif len(line) == 2:
                    config_h[line[1]] = True
                else:
                    config_h[line[1]] = ' '.join(line[2:])

            elif line[0] == '#undef':
                if len(line) == 2:
                    if line[1] in config_h:
                        if config_h[line[1]] is True:
                            del config_h[line[1]]
                        else:
                            config_h[line[1]] = False
                else:
                    log_error('%s: Incomplete #undef! On or around line %s' % (file, linenum))

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
        rules_mk_lines = unicode_lines(file)

        for line in rules_mk_lines:
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

                else:
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

    Returns a copy of keymap with the enums replaced with numbers.
    """
    replacements = {}
    for enum in enum_re.findall(keymap_text):
        enum = enum.split('{')[1]
        index = 0

        for define in enum.split(','):
            if '=' in define:
                define, new_index = define.split('=')

                if new_index.strip() == 'SAFE_RANGE':
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
        logging.warning('Could not find any layers in %s!', keymap_file)
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
        for i in range(max_int + 1):
            layer_list.append(layers.get(i))

    return layout_macro, layer_list


@memoize
def find_layouts(file):
    """Returns list of parsed layout macros found in the supplied file.
    """
    aliases = {}  # Populated with all `#define`s that aren't functions
    writing_keymap = False
    discovered_keymaps = []
    parsed_keymaps = {}
    current_keymap = []

    for line in unicode_lines(file):
        if not writing_keymap:
            if '#define' in line and '(' in line and ('LAYOUT' in line or 'KEYMAP' in line):
                writing_keymap = True
            elif '#define' in line:
                try:
                    _, pp_macro_name, pp_macro_text = line.strip().split(' ', 2)
                    aliases[pp_macro_name] = pp_macro_text
                except ValueError:
                    continue

        if writing_keymap:
            current_keymap.append(line.strip() + '\n')
            if ')' in line:
                writing_keymap = False
                discovered_keymaps.append(''.join(current_keymap))
                current_keymap = []

    for keymap in discovered_keymaps:
        # Clean-up the keymap text, extract the macro name, and end up with a list
        # of key entries.
        keymap = keymap.replace('\\', '').replace(' ', '').replace('\t', '').replace('#define', '')
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
            'layout': parsed_keymap,
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


def merge_info_json(info_json, keyboard_info):
    """Merge the parsed keyboard_info data with the parsed info.json and return the full JSON that will ultimately be stored in redis.
    """
    try:
        with open(info_json) as info_fd:
            info_json = json.load(info_fd)
    except Exception as e:
        log_error("Could not parse %s as JSON: %s" % (info_json, e))
        return keyboard_info

    if not isinstance(info_json, dict):
        log_error("%s is invalid! Should be a JSON dict object." % (info_fd.name))
        return keyboard_info

    for key in ('keyboard_name', 'manufacturer', 'identifier', 'url', 'maintainer', 'processor', 'bootloader', 'width', 'height'):
        if key in info_json:
            keyboard_info[key] = info_json[key]

    if 'layouts' in info_json:
        for layout_name, json_layout in info_json['layouts'].items():
            # Only pull in layouts we have a macro for
            if layout_name in keyboard_info['layouts']:
                if len(keyboard_info['layouts'][layout_name]['layout']) != len(json_layout['layout']):
                    log_args = {
                        'keyboard': keyboard_info['keyboard_folder'],
                        'layout': layout_name,
                        'info_len': len(json_layout['layout']),
                        'keymap_len': len(keyboard_info['layouts'][layout_name]['layout']),
                    }
                    log_error('%(keyboard)s: %(layout)s: Number of elements in info.json does not match! info.json:%(info_len)s != %(layout)s:%(keymap_len)s' % log_args)
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


def build_keyboard_info(keyboard):
    """Returns a dictionary describing a keyboard.
    """
    return {
        'keyboard_name': keyboard,
        'keyboard_folder': keyboard,
        'keymaps': [],
        'layouts': {},
        'maintainer': 'qmk',
        'readme': False,
    }


def arm_processor_rules(keyboard_info, rules_mk):
    """Setup the default keyboard info for an ARM board.
    """
    keyboard_info['processor_type'] = 'arm'
    keyboard_info['bootloader'] = rules_mk['BOOTLOADER'] if 'BOOTLOADER' in rules_mk else 'unknown'
    keyboard_info['processor'] = rules_mk['MCU'] if 'MCU' in rules_mk else 'unknown'
    if keyboard_info['bootloader'] == 'unknown':
        if 'STM32' in keyboard_info['processor']:
            keyboard_info['bootloader'] = 'stm32-dfu'
        elif keyboard_info.get('manufacturer') == 'Input Club':
            keyboard_info['bootloader'] = 'kiibohd-dfu'
    keyboard_info['protocol'] = 'ChibiOS'
    if 'STM32' in keyboard_info['processor']:
        keyboard_info['platform'] = 'STM32'
    elif 'MCU_SERIES' in rules_mk:
        keyboard_info['platform'] = rules_mk['MCU_SERIES']
    elif 'ARM_ATSAM' in rules_mk:
        keyboard_info['platform'] = 'ARM_ATSAM'
        keyboard_info['protocol'] = 'ATSAM'


def avr_processor_rules(keyboard_info, rules_mk):
    """Setup the default keyboard info for an AVR board.
    """
    keyboard_info['processor_type'] = 'avr'
    keyboard_info['bootloader'] = rules_mk['BOOTLOADER'] if 'BOOTLOADER' in rules_mk else 'atmel-dfu'
    keyboard_info['platform'] = rules_mk['ARCH'] if 'ARCH' in rules_mk else 'unknown'
    keyboard_info['processor'] = rules_mk['MCU'] if 'MCU' in rules_mk else 'unknown'

    # These are the only two MCUs which need V-USB at the moment.
    # Eventually we should detect the protocol by looking at PROTOCOL inherited from mcu_selection.mk:
    #if rules_mk['PROTOCOL'] == 'VUSB':
    if rules_mk.get('MCU') in ['atmega32a', 'atmega328p']:
        keyboard_info['protocol'] = 'V-USB'
    else:
        keyboard_info['protocol'] = 'LUFA'


def unknown_processor_rules(keyboard_info, rules_mk):
    """Setup the default keyboard info for unknown boards.
    """
    keyboard_info['bootloader'] = 'unknown'
    keyboard_info['platform'] = 'unknown'
    keyboard_info['processor'] = 'unknown'
    keyboard_info['processor_type'] = 'unknown'
    keyboard_info['protocol'] = 'unknown'


def store_keyboard_readme(keyboard_info):
    """Write a keyboard's readme file to redis.
    """
    keyboard = keyboard_info['keyboard_folder']
    readme_filename = None
    readme_path = ''
    for dir in keyboard.split('/'):
        readme_path = '/'.join((readme_path, dir))
        new_name = find_readme('qmk_firmware/keyboards%s' % (readme_path))
        if new_name:
            readme_filename = new_name  # Last one wins

    if readme_filename:
        qmk_redis.set('qmk_api_kb_%s_readme' % (keyboard), unicode_text(readme_filename))
        keyboard_info['readme'] = True
    else:
        log_warning('%s does not have a readme.md.' % keyboard)


def build_usb_entry(keyboard_info, config_h, usb_list):
    """Returns the default USB entry for a keyboard based on its config.h contents.
    """
    usb_entry = {'keyboard': keyboard_info['keyboard_folder']}
    for key in ('VENDOR_ID', 'PRODUCT_ID', 'DEVICE_VER', 'MANUFACTURER', 'DESCRIPTION'):
        if key in config_h:
            if key in ('VENDOR_ID', 'PRODUCT_ID', 'DEVICE_VER'):
                config_h[key] = config_h[key].upper().replace('0X', '')
                config_h[key] = '0x' + config_h[key]
            keyboard_info[key.lower()] = config_h[key]
            usb_entry[key.lower()] = config_h[key]

    vendor_id = usb_entry['vendor_id'] = usb_entry.get('vendor_id', '0xFEED')
    product_id = usb_entry['product_id'] = usb_entry.get('product_id', '0x0000')

    if vendor_id not in usb_list:
        usb_list[vendor_id] = {}

    if product_id not in usb_list[vendor_id]:
        usb_list[vendor_id][product_id] = {}

    return usb_entry


def process_keyboard(keyboard, usb_list, kb_list, kb_entries):
    """Parse all the files associated with a specific keyboard to build an API object for it.
    """
    keyboard_info = build_keyboard_info(keyboard)

    for layout_name, layout_json in find_all_layouts(keyboard).items():
        if not layout_name.startswith('LAYOUT_kc'):
            keyboard_info['layouts'][layout_name] = layout_json

    for info_json_filename in find_info_json(keyboard):
        # Merge info.json files into one.
        keyboard_info = merge_info_json(info_json_filename, keyboard_info)

    # Pull some keyboard information from existing rules.mk and config.h files
    config_h = parse_config_h(keyboard)
    rules_mk = parse_rules_mk(keyboard)
    usb_entry = build_usb_entry(keyboard_info, config_h, usb_list)
    usb_list[usb_entry['vendor_id']][usb_entry['product_id']][keyboard] = usb_entry

    # Setup platform specific keys
    mcu = rules_mk.get('MCU')
    if mcu in ARM_PROCESSORS:
        arm_processor_rules(keyboard_info, rules_mk)
    elif mcu in AVR_PROCESSORS:
        avr_processor_rules(keyboard_info, rules_mk)
    else:
        log_warning("%s: Unknown MCU: %s" % (keyboard, mcu))
        unknown_processor_rules(keyboard_info, rules_mk)

    # Used to identify keyboards in the redis key qmk_api_usb_list.
    keyboard_info['identifier'] = ':'.join((
        keyboard_info.get('vendor_id', 'unknown'),
        keyboard_info.get('product_id', 'unknown'),
        keyboard_info.get('device_ver', 'unknown'),
    ))

    # Store the keyboard's readme in redis
    store_keyboard_readme(keyboard_info)

    # Write the keyboard to redis and add it to the master list.
    qmk_redis.set('qmk_api_kb_%s' % (keyboard), keyboard_info)
    kb_list.append(keyboard)
    kb_entries['keyboards'][keyboard] = keyboard_info


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
    del error_log[:]
    if not debug:
        if exists('update_kb_redis'):
            rmtree('update_kb_redis')
        mkdir('update_kb_redis')
        chdir('update_kb_redis')
    qmk_redis.set('qmk_needs_update', False)

    if not debug or not exists('qmk_firmware'):
        checkout_qmk(skip_cache=True)

    # Update redis with the latest data
    kb_list = []
    usb_list = {}  # Structure: VENDOR_ID: {PRODUCT_ID: {KEYBOARD_FOLDER: {'vendor_id': VENDOR_ID, 'product_id': PRODUCT_ID, 'device_ver': DEVICE_VER, 'manufacturer': MANUFACTURER, 'product': PRODUCT, 'keyboard': KEYBOARD_FOLDER}

    kb_all = {'last_updated': strftime('%Y-%m-%d %H:%M:%S %Z'), 'keyboards': {}}
    for keyboard in list_keyboards():
        try:
            process_keyboard(keyboard, usb_list, kb_list, kb_all)

        except Exception as e:
            # Uncaught exception handler. Ideally this is never hit.
            log_error('Uncaught exception while processing keyboard %s! %s: %s' % (keyboard, e.__class__.__name__, str(e)))
            logging.exception(e)

    # Update the global redis information
    qmk_redis.set('qmk_api_keyboards', kb_list)
    qmk_redis.set('qmk_api_kb_all', kb_all)
    qmk_redis.set('qmk_api_usb_list', usb_list)
    qmk_redis.set('qmk_api_last_updated', {'git_hash': git_hash(), 'last_updated': strftime('%Y-%m-%d %H:%M:%S %Z')})
    qmk_redis.set('qmk_api_update_error_log', error_log)
    logging.info('*** All keys successfully written to redis! Total size:', len(json.dumps(kb_all)))

    chdir('..')

    return True


if __name__ == '__main__':
    debug = True

    import sys
    if len(sys.argv) > 1:
        keyboard = sys.argv[1]
        cached_json = {'last_updated': strftime('%Y-%m-%d %H:%M:%S %Z'), 'keyboards': {}}
        usb_list = {}
        kb_list = []
        process_keyboard(keyboard, usb_list, kb_list, cached_json)
    else:
        update_kb_redis()
