import json
import logging
from io import BytesIO
from os import chdir, mkdir, environ, path, getcwd
from subprocess import check_output, CalledProcessError, STDOUT
from time import strftime
from traceback import format_exc

from rq import get_current_job
from rq.decorators import job

import qmk_redis
import qmk_storage
from qmk_commands import checkout_qmk, find_firmware_file, store_source, checkout_chibios
from qmk_errors import NoSuchKeyboardError
from qmk_redis import redis


API_URL = environ.get('API_URL', 'https://api.qmk.fm/')
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
    template_name = 'keyboards/%(keyboard)s/templates/keymap.c' % result
    if path.exists(template_name):
        keymap_c = open(template_name).read()
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
    filename = '%s/%s.json' % (result['id'], result['id'])

    qmk_storage.save_fd(json_obj, filename, len(json_data))


def store_firmware_binary(result):
    """Called while PWD is qmk_firmware to store the firmware hex.
    """
    firmware_file = 'qmk_firmware/%s' % result['firmware_filename']
    firmware_storage_path = '%(id)s/%(firmware_filename)s' % result
    mime_type = 'text/plain' if result['firmware_filename'].endswith('.hex') else 'application/octet-stream'

    if not path.exists(firmware_file):
        return False

    qmk_storage.save_file(firmware_file, firmware_storage_path, mime_type)
    result['firmware_binary_url'] = [path.join(API_URL, 'v1', 'compile', result['id'], 'download')]


def store_firmware_source(result):
    """Called while PWD is the top-level directory to store the firmware source.
    """
    result['source_archive'] = 'qmk_firmware-%(keyboard)s-%(keymap)s.zip' % (result)
    result['source_archive'] = result['source_archive'].replace('/', '-')
    store_source()
    result['firmware_source_url'] = [path.join(API_URL, 'v1', 'compile', result['id'], 'source')]


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
        result['firmware'] = 'binary file'
        if result['firmware_filename'].endswith('.hex'):
            result['firmware'] = open(result['firmware_filename'], 'r').read()  # FIXME: Remove this for v2

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
        basepath = path.normpath('qmk_firmware/keyboards/%s/%s/keymaps' % (result['keyboard'], directory))
        if path.exists(basepath):
            return '/'.join((basepath, result['keymap']))

    logging.error('Could not find keymaps directory!')
    raise NoSuchKeyboardError('Could not find keymaps directory for: %s' % result['keyboard'])


# Public functions
@job('default', connection=redis)
def compile_firmware(keyboard, keymap, layout, layers):
    """Compile a firmware.
    """
    result = {
        'keyboard': keyboard,
        'layout': layout,
        'keymap': keymap,
        'command': ['make', 'COLOR=false', ':'.join((keyboard, keymap))],
        'returncode': -2,
        'output': '',
        'firmware': None,
        'firmware_filename': ''
    }

    try:
        job = get_current_job()
        result['id'] = job.id
        checkout_qmk()

        # Sanity checks
        if not path.exists('qmk_firmware/keyboards/' + keyboard):
            logging.error('Unknown keyboard: %s', keyboard)
            return {'returncode': -1, 'command': '', 'output': 'Unknown keyboard!', 'firmware': None}

        for pathname in ('qmk_firmware/keyboards/%s/keymaps/%s' % (keyboard, keymap),
                         'qmk_firmware/keyboards/%s/../keymaps/%s' % (keyboard, keymap)):
            if path.exists(pathname):
                logging.error('Name collision! %s already exists! This should not happen!', pathname)
                return {'returncode': -1, 'command': '', 'output': 'Keymap name collision! %s already exists!' % (pathname), 'firmware': None}

        # If this keyboard needs chibios check it out
        kb_data = qmk_redis.get('qmk_api_kb_' + keyboard)

        if kb_data['processor_type'] == 'arm':
            checkout_chibios()

        # Build the keyboard firmware
        create_keymap(result, layers)
        store_firmware_source(result)
        compile_keymap(job, result)
        store_firmware_binary(result)

    except Exception as e:
        result['returncode'] = -3
        result['exception'] = e.__class__.__name__
        result['stacktrace'] = format_exc()

        if not result['output']:
            result['output'] = result['stacktrace']

    return result


@job('default', connection=redis)
def ping():
    """Write a timestamp to redis to make sure at least one worker is running ok.
    """
    return redis.set('qmk_api_last_ping', strftime('"%Y-%m-%d %H:%M:%SZ"'))
