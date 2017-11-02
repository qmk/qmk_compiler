import json
import logging
import qmk_storage
from io import BytesIO
from os import chdir, mkdir, remove
from os.path import exists, normpath
from qmk_commands import checkout_qmk, find_firmware_file
from qmk_errors import NoSuchKeyboardError
from qmk_redis import redis
from rq import get_current_job
from rq.decorators import job
from subprocess import check_output, CalledProcessError, STDOUT

# The `keymap.c` template to use when a keyboard doesn't have its own
DEFAULT_KEYMAP_C = """#include QMK_KEYBOARD_H

// Helpful defines
#define _______ KC_TRNS

const uint16_t PROGMEM keymaps[][MATRIX_ROWS][MATRIX_COLS] = {
__KEYMAP_GOES_HERE__
};
"""


# Local Helper Functions
def generate_keymap_c(result, layers):
    if exists('qmk_firmware/keyboards/%s/templates/keymap.c'):
        keymap_c = open('keyboards/%s/keymap.c' % result['keyboard']).read()
    else:
        keymap_c = DEFAULT_KEYMAP_C

    layer_txt = []
    for layer_num, layer in enumerate(layers):
        if layer_num != 0:
            layer_txt[-1] = layer_txt[-1] + ','
        layer_keys = ', '.join(layer)
        layer_txt.append('\t[%s] = %s(%s)' % (layer_num, result['layout'], layer_keys))

    keymap = '\n'.join(layer_txt)
    keymap_c = keymap_c.replace('__KEYMAP_GOES_HERE__', keymap)

    return keymap_c


def store_firmware_metadata(job, result):
    """Save `result` as a JSON file along side the firmware.
    """
    json_data = json.dumps({
        'created_at': job.created_at.strftime('%Y-%m-%d %H:%M:%S %Z'),
        'enqueued_at': job.enqueued_at.strftime('%Y-%m-%d %H:%M:%S %Z'),
        'id': job.id,
        'is_failed': result['returncode'] != 0,
        'is_finished': True,
        'is_queued': False,
        'is_started': False,
        'result': result
    })
    json_obj = BytesIO(json_data.encode('utf-8'))
    filename = '%s.json' % result['id']

    qmk_storage.save_fd(json_obj, filename, len(json_data))


def store_firmware_binary(result):
    """Called while PWD is qmk_firmware to store the firmware hex.
    """
    firmware_file = 'qmk_firmware/%s' % result['firmware_filename']
    if not exists(firmware_file):
        return False

    result['firmware'] = open(firmware_file, 'r').read()
    qmk_storage.save_file(firmware_file, '%(id)s/%(firmware_filename)s' % result, 'text/plain')


def store_firmware_source(result):
    """Called while PWD is the top-level directory to store the firmware source.
    """
    result['source_archive'] = 'qmk_firmware-%(keyboard)s-%(keymap)s.zip' % (result)
    result['source_archive'] = result['source_archive'].replace('/', '-')
    zip_command = ['zip', '-x', 'qmk_firmware/.build/*', '-x', 'qmk_firmware/.git/*', '-r', result['source_archive'], 'qmk_firmware']
    try:
        logging.debug('Zipping Source: %s', zip_command)
        check_output(zip_command)
    except CalledProcessError as build_error:
        logging.error('Could not zip source, Return Code %s, Command %s', build_error.returncode, build_error.cmd)
        logging.error(build_error.output)

    qmk_storage.save_file(result['source_archive'], '%(id)s/%(source_archive)s' % result, 'text/plain')
    remove(result['source_archive'])


def create_keymap(result, layers):
    keymap_c = generate_keymap_c(result, layers)
    keymap_path = find_keymap_path(result)
    mkdir(keymap_path)
    with open('%s/keymap.c' % keymap_path, 'w') as keymap_file:
        keymap_file.write(keymap_c)
    with open('%s/layers.json' % keymap_path, 'w') as layers_file:
        json.dump(layers, layers_file)


def compile_keymap(job, result):
    logging.debug('Executing build: %s', result['command'])
    chdir('qmk_firmware/')
    try:
        hash = check_output(['git', 'rev-parse', 'HEAD'])
        open('version.txt', 'w').write(hash.decode('cp437') + '\n')
        result['output'] = check_output(result['command'], stderr=STDOUT, universal_newlines=True)
        result['returncode'] = 0
        result['firmware_filename'] = find_firmware_file()
        result['firmware'] = open(result['firmware_filename'], 'r').read()

    except CalledProcessError as build_error:
        logging.error('Could not build firmware (%s): %s', build_error.cmd, build_error.output)
        result['returncode'] = build_error.returncode
        result['cmd'] = build_error.cmd
        result['output'] = build_error.output

    finally:
        store_firmware_metadata(job, result)
        chdir('..')


def find_keymap_path(result):
    for directory in ['.', '..', '../..', '../../..', '../../../..', '../../../../..']:
        basepath = normpath('qmk_firmware/keyboards/%s/%s/keymaps' % (result['keyboard'], directory))
        if exists(basepath):
            return '/'.join((basepath, result['keymap']))

    logging.error('Could not find keymaps directory!')
    raise NoSuchKeyboardError('Could not find keymaps directory for: %s' % result['keyboard'])


# Public functions
@job('default', connection=redis)
def compile_firmware(keyboard, keymap, layout, layers):
    """Compile a firmware.
    """
    checkout_qmk()
    job = get_current_job()
    result = {
        'id': job.id,
        'keyboard': keyboard,
        'layout': layout,
        'keymap': keymap,
        'command': ['make', ':'.join((keyboard, keymap))],
        'returncode': -2,
        'output': '',
        'firmware': None,
        'firmware_filename': ''
    }

    # Sanity checks
    if not exists('qmk_firmware/keyboards/%s' % keyboard):
        logging.error('Unknown keyboard: %s', keyboard)
        return {'returncode': -1, 'command': '', 'output': 'Unknown keyboard!', 'firmware': None}

    if exists('qmk_firmware/keyboards/%s/keymaps/%s' % (keyboard, keymap)) or exists('qmk_firmware/keyboards/%s/../keymaps/%s' % (keyboard, keymap)):
        logging.error('Name collision! This should not happen!')
        return {'returncode': -1, 'command': '', 'output': 'Keymap name collision!', 'firmware': None}

    # Build the keyboard firmware
    create_keymap(result, layers)
    compile_keymap(job, result)

    # Store the results
    store_firmware_binary(result)
    store_firmware_source(result)

    return result
