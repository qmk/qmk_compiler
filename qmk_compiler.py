import json
import logging
from io import BytesIO
from os import chdir, environ, path, remove
from subprocess import check_output, CalledProcessError, STDOUT
from time import strftime, time
from traceback import format_exc

from rq import get_current_job
from rq.decorators import job

import qmk_redis
import qmk_storage
from qmk_commands import checkout_qmk, find_firmware_file, store_source, checkout_chibios, checkout_lufa, checkout_vusb, write_version_txt
from qmk_redis import redis

API_URL = environ.get('API_URL', 'https://api.qmk.fm/')
# The `keymap.c` template to use when a keyboard doesn't have its own
DEFAULT_KEYMAP_C = """#include QMK_KEYBOARD_H

const uint16_t PROGMEM keymaps[][MATRIX_ROWS][MATRIX_COLS] = {
__KEYMAP_GOES_HERE__
};
"""


# Local Helper Functions
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

    qmk_storage.save_fd(json_obj, filename)


def store_firmware_binary(result):
    """Called while PWD is qmk_firmware to store the firmware hex.
    """
    firmware_file = 'qmk_firmware/%s' % result['firmware_filename']
    firmware_storage_path = '%(id)s/%(firmware_filename)s' % result

    if not path.exists(firmware_file):
        return False

    qmk_storage.save_file(firmware_file, firmware_storage_path)
    result['firmware_binary_url'] = [path.join(API_URL, 'v1', 'compile', result['id'], 'download')]


def store_firmware_source(result):
    """Called while PWD is the top-level directory to store the firmware source.
    """
    # Store the keymap source
    qmk_storage.save_file(path.join('qmk_firmware', result['keymap_archive']), path.join(result['id'], result['keymap_archive']))

    # Store the full source
    result['source_archive'] = 'qmk_firmware-%(keyboard)s-%(keymap)s.zip' % (result)
    result['source_archive'] = result['source_archive'].replace('/', '-')
    store_source(result['source_archive'], 'qmk_firmware', result['id'])
    result['firmware_keymap_url'] = ['/'.join((API_URL, 'v1', 'compile', result['id'], 'keymap'))]
    result['firmware_source_url'] = ['/'.join((API_URL, 'v1', 'compile', result['id'], 'source'))]


def compile_keymap(job, result):
    logging.debug('Executing build: %s', result['command'])
    chdir('qmk_firmware/')
    try:
        result['output'] = check_output(result['command'], stderr=STDOUT, universal_newlines=True)
        result['returncode'] = 0
        result['firmware_filename'] = find_firmware_file()

    except CalledProcessError as build_error:
        print('Could not build firmware (%s): %s' % (build_error.cmd, build_error.output))
        result['returncode'] = build_error.returncode
        result['cmd'] = build_error.cmd
        result['output'] = build_error.output

    finally:
        store_firmware_metadata(job, result)
        chdir('..')


# Public functions
@job('default', connection=redis, timeout=900)
def compile_firmware(keyboard, keymap, layout, layers, source_ip=None):
    """Compile a firmware.
    """
    keyboard_safe_chars = keyboard.replace('/', '-')
    keymap_safe_chars = keymap.replace('/', '-')
    keymap_json_file = '%s-%s.json' % (keyboard_safe_chars, keymap_safe_chars)
    keymap_json = json.dumps({
        'keyboard': keyboard,
        'keymap': keymap,
        'layout': layout,
        'layers': layers,
        'author': '',
        'notes': '',
        'version': 1,
        'documentation': 'This file is a configurator export. You can compile it directly inside QMK using the command `bin/qmk compile %s`' % (keymap_json_file,)
    })
    result = {
        'keyboard': keyboard,
        'layout': layout,
        'keymap': keymap,
        'keymap_archive': keymap_json_file,
        'command': ['bin/qmk', 'compile', keymap_json_file],
        'returncode': -2,
        'output': '',
        'firmware': None,
        'firmware_filename': '',
        'source_ip': source_ip,
    }

    try:
        kb_data = qmk_redis.get('qmk_api_kb_' + keyboard)
        job = get_current_job()
        result['id'] = job.id
        checkout_qmk()

        # Sanity checks
        if not path.exists('qmk_firmware/keyboards/' + keyboard):
            print('Unknown keyboard: %s' % (keyboard,))
            return {'returncode': -1, 'command': '', 'output': 'Unknown keyboard!', 'firmware': None}

        # If this keyboard needs a submodule check it out
        if kb_data.get('protocol') in ['ChibiOS', 'LUFA']:
            checkout_lufa()

        if kb_data.get('protocol') == 'ChibiOS':
            checkout_chibios()

        if kb_data.get('protocol') == 'V-USB':
            checkout_vusb()

        # Write the keymap file
        with open(path.join('qmk_firmware', keymap_json_file), 'w') as fd:
            fd.write(keymap_json + '\n')

        # Compile the firmware
        store_firmware_source(result)
        remove(result['source_archive'])
        compile_keymap(job, result)
        store_firmware_binary(result)

    except Exception as e:
        result['returncode'] = -3
        result['exception'] = e.__class__.__name__
        result['stacktrace'] = format_exc()

        if not result['output']:
            result['output'] = result['stacktrace']

    return result


@job('default', connection=redis, timeout=900)
def compile_json(keyboard_keymap_data, source_ip=None):
    """Compile a keymap.

    Arguments:

        keyboard_keymap_data
            A configurator export file that's been deserialized

        source_ip
            The IP that submitted the compile job
    """
    result = {
        'returncode': -2,
        'output': '',
        'firmware': None,
        'firmware_filename': '',
        'source_ip': source_ip,
        'output': 'Unknown error',
    }
    try:
        for key in ('keyboard', 'layout', 'keymap'):
            result[key] = keyboard_keymap_data[key]

        result['keymap_archive'] = '%s-%s.json' % (result['keyboard'].replace('/', '-'), result['keymap'].replace('/', '-'))
        result['keymap_json'] = json.dumps(keyboard_keymap_data)
        result['command'] = ['bin/qmk', 'compile', result['keymap_archive']]

        kb_data = qmk_redis.get('qmk_api_kb_' + result['keyboard'])
        job = get_current_job()
        result['id'] = job.id
        checkout_qmk()

        # Sanity checks
        if not path.exists('qmk_firmware/keyboards/' + result['keyboard']):
            print('Unknown keyboard: %s' % (result['keyboard'],))
            return {'returncode': -1, 'command': '', 'output': 'Unknown keyboard!', 'firmware': None}

        # If this keyboard needs a submodule check it out
        if kb_data.get('protocol') in ['ChibiOS', 'LUFA']:
            checkout_lufa()

        if kb_data.get('protocol') == 'ChibiOS':
            checkout_chibios()

        if kb_data.get('protocol') == 'V-USB':
            checkout_vusb()

        # Write the keymap file
        with open(path.join('qmk_firmware', result['keymap_archive']), 'w') as fd:
            fd.write(result['keymap_json'] + '\n')

        # Compile the firmware
        store_firmware_source(result)
        remove(result['source_archive'])
        compile_keymap(job, result)
        store_firmware_binary(result)

    except Exception as e:
        result['returncode'] = -3
        result['exception'] = e.__class__.__name__
        result['stacktrace'] = format_exc()

    return result


@job('default', connection=redis)
def ping():
    """Write a timestamp to redis to make sure at least one worker is running ok.
    """
    return redis.set('qmk_api_last_ping', time())
