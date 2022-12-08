import json
import logging
import sys
from io import BytesIO
from os import chdir, environ, path, remove
from socket import gethostname
from subprocess import check_output, CalledProcessError, STDOUT
from time import strftime, time
from traceback import format_exc

import graphyte
from geoip import geolite2
from rq import get_current_job
from rq.decorators import job

import qmk_redis
import qmk_storage
from qmk_commands import QMK_GIT_BRANCH, checkout_qmk, find_firmware_file, store_source, checkout_chibios, checkout_lufa, checkout_vusb, write_version_txt
from qmk_redis import redis

DEBUG = int(environ.get('DEBUG', 0))
API_URL = environ.get('API_URL', 'https://api.qmk.fm/')
GRAPHITE_HOST = environ.get('GRAPHITE_HOST', 'qmk_metrics_aggregator')
GRAPHITE_PORT = int(environ.get('GRAPHITE_PORT', 2023))

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
    firmware_storage_path = '%(id)s/%(firmware_filename)s' % result

    if not result['firmware_filename'] or not path.exists(result['firmware_filename']):
        return False

    qmk_storage.save_file(result['firmware_filename'], firmware_storage_path)
    result['firmware_binary_url'] = [path.join(API_URL, 'v1', 'compile', result['id'], 'download')]

    if result['public_firmware']:
        file_ext = result["firmware_filename"].split(".")[-1]
        file_name = f'compiled/{result["keyboard"]}/default.{file_ext}'
        qmk_storage.save_file(result['firmware_filename'], file_name, bucket=qmk_storage.COMPILE_S3_BUCKET, public=True)


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
    try:
        result['output'] = check_output(result['command'], stderr=STDOUT, universal_newlines=True)
        result['returncode'] = 0
        result['firmware_filename'] = find_firmware_file()

        if not result['firmware_filename']:
            # Build returned success but no firmware file on disk
            result['return_code'] = -4

    except CalledProcessError as build_error:
        print('Could not build firmware (%s): %s' % (build_error.cmd, build_error.output))
        result['returncode'] = build_error.returncode
        result['cmd'] = build_error.cmd
        result['output'] = build_error.output


@job('default', connection=redis, timeout=900)
def compile_json(keyboard_keymap_data, source_ip=None, send_metrics=True, public_firmware=False):
    """Compile a keymap.

    Arguments:

        keyboard_keymap_data
            A configurator export file that's been deserialized

        source_ip
            The IP that submitted the compile job
    """
    start_time = time()
    base_metric = f'{gethostname()}.qmk_compiler.compile_json'
    result = {
        'keyboard': 'unknown',
        'returncode': -2,
        'output': '',
        'firmware': None,
        'firmware_filename': '',
        'source_ip': source_ip,
        'output': 'Unknown error',
        'public_firmware': public_firmware,
    }

    if DEBUG:
        print('Pointing graphite at', GRAPHITE_HOST)

    send_metrics=True
    if send_metrics:
        graphyte.init(GRAPHITE_HOST, GRAPHITE_PORT)

    try:
        for key in ('keyboard', 'layout', 'keymap'):
            result[key] = keyboard_keymap_data[key]

        # Gather information
        result['keymap_archive'] = '%s-%s.json' % (result['keyboard'].replace('/', '-'), result['keymap'].replace('/', '-'))
        result['keymap_json'] = json.dumps(keyboard_keymap_data)
        result['command'] = ['qmk', 'compile', result['keymap_archive']]
        job = get_current_job()
        result['id'] = job.id
        branch = keyboard_keymap_data.get('branch', QMK_GIT_BRANCH)
        converter = keyboard_keymap_data.get('converter', None)

        # Fetch the appropriate version of QMK
        git_start_time = time()
        checkout_qmk(branch=branch)
        git_time = time() - git_start_time
        chdir('qmk_firmware')

        # Sanity check
        if not path.exists('keyboards/' + result['keyboard']):
            print('Unknown keyboard: %s' % (result['keyboard'],))
            return {'returncode': -1, 'command': '', 'output': 'Unknown keyboard!', 'firmware': None}

        # Pull in the modules from the QMK we just checked out
        if './lib/python' not in sys.path:
            sys.path.append('./lib/python')

        from qmk.info import info_json

        # If this keyboard needs a submodule check it out
        submodule_start_time = time()
        kb_info = info_json(result['keyboard'])
        if 'protocol' not in kb_info:
            kb_info['protocol'] = 'unknown'

        # FIXME: Query qmk_firmware as not all converters will be ChibiOS
        if converter:
            kb_info['protocol'] = 'ChibiOS'

        if kb_info['protocol'] in ['ChibiOS', 'LUFA']:
            checkout_lufa()

        if kb_info['protocol'] == 'ChibiOS':
            checkout_chibios()

        if kb_info['protocol'] == 'V-USB':
            checkout_vusb()
        submodule_time = time() - submodule_start_time

        # Write the keymap file
        with open(result['keymap_archive'], 'w') as fd:
            fd.write(result['keymap_json'] + '\n')

        # Compile the firmware
        compile_start_time = time()
        compile_keymap(job, result)
        compile_time = time() - compile_start_time

        # Store the source in S3
        storage_start_time = time()
        store_firmware_binary(result)
        chdir('..')
        store_firmware_source(result)
        remove(result['source_archive'])
        storage_time = time() - storage_start_time

        # Send metrics about this build
        if send_metrics:
            graphyte.send(f'{base_metric}.{result["keyboard"]}.all_layouts', 1)
            graphyte.send(f'{base_metric}.{result["keyboard"]}.{result["layout"]}', 1)
            graphyte.send(f'{base_metric}.{result["keyboard"]}.git_time', git_time)
            graphyte.send(f'{base_metric}.all_keyboards.git_time', git_time)
            graphyte.send(f'{base_metric}.{result["keyboard"]}.submodule_time', submodule_time)
            graphyte.send(f'{base_metric}.all_keyboards.submodule_time', submodule_time)
            graphyte.send(f'{base_metric}.{result["keyboard"]}.compile_time', compile_time)
            graphyte.send(f'{base_metric}.all_keyboards.compile_time', compile_time)

            if result['returncode'] == 0:
                graphyte.send(f'{base_metric}.{result["keyboard"]}.compile_time', compile_time)
                graphyte.send(f'{base_metric}.all_keyboards.compile_time', compile_time)
            else:
                graphyte.send(f'{base_metric}.{result["keyboard"]}.errors', 1)

            if source_ip:
                ip_location = geolite2.lookup(source_ip)

                if ip_location:
                    if ip_location.subdivisions:
                        location_key = f'{ip_location.country}_{"_".join(ip_location.subdivisions)}'
                    else:
                        location_key = ip_location.country

                    graphyte.send(f'{gethostname()}.qmk_compiler.geoip.{location_key}', 1)

            total_time = time() - start_time
            graphyte.send(f'{base_metric}.{result["keyboard"]}.storage_time', storage_time)
            graphyte.send(f'{base_metric}.all_keyboards.storage_time', storage_time)
            graphyte.send(f'{base_metric}.{result["keyboard"]}.total_time', total_time)
            graphyte.send(f'{base_metric}.all_keyboards.total_time', total_time)

    except Exception as e:
        result['returncode'] = -3
        result['exception'] = e.__class__.__name__
        result['stacktrace'] = format_exc()

        if send_metrics:
            graphyte.send(f'{base_metric}.{result["keyboard"]}.errors', 1)

    store_firmware_metadata(job, result)

    return result


@job('default', connection=redis)
def ping():
    """Write a timestamp to redis to make sure at least one worker is running ok.
    """
    return redis.set('qmk_api_last_ping', time())
