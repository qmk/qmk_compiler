import json
import logging
import minio.helpers
from hashids import Hashids
from io import BytesIO
from minio import Minio
from minio.error import ResponseError, BucketAlreadyOwnedByYou, BucketAlreadyExists
from os import chdir, environ, mkdir, listdir, remove
from os.path import exists, normpath
from redis import Redis
from rq import get_current_job
from rq.decorators import job
from shutil import rmtree, copy
from subprocess import check_output, CalledProcessError, STDOUT

# Ugly hack- disable minio's multipart upload feature
minio.helpers.MIN_PART_SIZE = minio.helpers.MAX_MULTIPART_OBJECT_SIZE

# Configuration
STORAGE_ENGINE = environ.get('STORAGE_ENGINE', 'minio')  # 'minio' or 'filesystem'
FILESYSTEM_PATH = environ.get('FILESYSTEM_PATH', 'firmwares')
MINIO_HOST = environ.get('MINIO_HOST', 'lb.minio:9000')
MINIO_LOCATION = environ.get('MINIO_LOCATION', 'us-east-1')
MINIO_BUCKET = environ.get('MINIO_BUCKET', 'compiled-qmk-firmware')
MINIO_ACCESS_KEY = environ.get('MINIO_ACCESS_KEY', '')
MINIO_SECRET_KEY = environ.get('MINIO_SECRET_KEY', '')
MINIO_SECURE = False
REDIS_HOST = environ.get('REDIS_HOST', 'redis.qmk-api')

# The `keymap.c` template to use when a keyboard doesn't have its own
DEFAULT_KEYMAP_C = """#include QMK_KEYBOARD_H

// Helpful defines
#define _______ KC_TRNS

const uint16_t PROGMEM keymaps[][MATRIX_ROWS][MATRIX_COLS] = {
__KEYMAP_GOES_HERE__
};
"""

# Objects we need to instaniate
hashids = Hashids()
redis = Redis(REDIS_HOST)
minio = Minio(MINIO_HOST, access_key=MINIO_ACCESS_KEY, secret_key=MINIO_SECRET_KEY, secure=MINIO_SECURE)

# Make sure our minio store is properly setup
try:
    minio.make_bucket(MINIO_BUCKET, location=MINIO_LOCATION)
except BucketAlreadyOwnedByYou as err:
    pass
except BucketAlreadyExists as err:
    pass


# Exceptions
class Error(Exception):
    pass


class NoSuchKeyboardError(Error):
    """Raised when we can't find a keyboard/keymap directory.
    """
    def __init__(self, message):
        self.message = message


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


def checkout_qmk(result=None):
    if exists('qmk_firmware'):
        rmtree('qmk_firmware')

    if not result:
        result = {}

    command = ['git', 'clone', 'https://github.com/qmk/qmk_firmware.git']
    try:
        check_output(command, stderr=STDOUT, universal_newlines=True)
        chdir('qmk_firmware/')
        hash = check_output(['git', 'rev-parse', 'HEAD'])
        open('version.txt', 'w').write(hash.decode('cp437') + '\n')
        chdir('..')
        return True
    except CalledProcessError as build_error:
        print("Could not check out qmk: %s (returncode:%s)" % (build_error.output, build_error.returncode))


def find_firmware_file():
    """Returns the first firmware file we find.

    Since `os.listdir()` gives us unordered results we can not guarantee which
    file will be delivered in the case of multiple firmware files. The
    assumption is that there will only be one.
    """
    for file in listdir('.'):
        if file[-4:] in ('.hex', '.bin'):
            return file


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

    if STORAGE_ENGINE == 'minio':
        logging.debug('Uploading %s to minio.', filename)
        try:
            minio.put_object(MINIO_BUCKET, '%s/%s' % (result['id'], filename), json_obj, len(json_data), 'application/json')
        except ResponseError as err:
            logging.error('Could not upload firmware binary to minio: %s', err)
            logging.exception(err)
    else:
        logging.debug('Copying %s to %s/%s.', filename, FILESYSTEM_PATH, result['id'])
        if FILESYSTEM_PATH[0] == '/':
            firmware_path = '%s/%s/' % (FILESYSTEM_PATH, result['id'])
        else:
            firmware_path = '../%s/%s/' % (FILESYSTEM_PATH, result['id'])
        mkdir(firmware_path)
        copy(result['filename'], firmware_path)

    return True


def store_firmware_binary(result):
    """Called while PWD is qmk_firmware to store the firmware hex in minio.
    """
    firmware_file = 'qmk_firmware/%s' % result['firmware_filename']
    if not exists(firmware_file):
        return False

    result['firmware'] = open(firmware_file, 'r').read()
    if STORAGE_ENGINE == 'minio':
        logging.debug('Uploading %s to minio.', firmware_file)
        try:
            minio.fput_object(MINIO_BUCKET, '%s/%s' % (result['id'], result['firmware_filename']), firmware_file)
        except ResponseError as err:
            logging.error('Could not upload firmware binary to minio: %s', err)
            logging.exception(err)
    else:
        logging.debug('Copying %s to %s/%s.', firmware_file, FILESYSTEM_PATH, result['id'])
        if FILESYSTEM_PATH[0] == '/':
            firmware_path = '%s/%s/' % (FILESYSTEM_PATH, result['id'])
        else:
            firmware_path = '../%s/%s/' % (FILESYSTEM_PATH, result['id'])
        mkdir(firmware_path)
        copy(result['firmware_filename'], firmware_path)

    return True


def store_firmware_source(result):
    """Called while PWD is the top-level directory to store the firmware source in minio.
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

    if STORAGE_ENGINE == 'minio':
        logging.debug('Uploading %s to minio.', result['source_archive'])
        try:
            minio.fput_object(MINIO_BUCKET, '%s/%s' % (result['id'], result['source_archive']), result['source_archive'])
        except ResponseError as err:
            logging.error('Could not upload firmware source to minio: %s', err)
            logging.exception(err)
        finally:
            remove(result['source_archive'])
    else:
        logging.debug('Copying %s to %s/%s.', result['source_archive'], FILESYSTEM_PATH, result['id'])
        if FILESYSTEM_PATH[0] == '/':
            firmware_path = '%s/%s/' % (FILESYSTEM_PATH, result['id'])
        else:
            firmware_path = '../%s/%s/' % (FILESYSTEM_PATH, result['id'])
        mkdir(firmware_path)
        copy(result['source_archive'], firmware_path)
        remove(result['source_archive'])


def create_keymap(result, layers):
    keymap_c = generate_keymap_c(result, layers)
    keymap_path = find_keymap_path(result)
    mkdir(keymap_path)
    with open('%s/keymap.c' % keymap_path, 'w') as keymap_file:
        keymap_file.write(keymap_c)
    with open('%s/layers.json' % keymap_path, 'w') as layers_file:
        json.dump(layers, layers_file)


def compile_keymap(result):
    logging.debug('Executing build: %s', result['command'])
    chdir('qmk_firmware/')
    try:
        hash = check_output(['git', 'rev-parse', 'HEAD'])
        open('version.txt', 'w').write(hash.decode('cp437') + '\n')
        result['output'] = check_output(result['command'], stderr=STDOUT, universal_newlines=True)
        result['returncode'] = 0
        result['firmware_filename'] = find_firmware_file()

    except CalledProcessError as build_error:
        logging.error('Could not build firmware (%s): %s', build_error.cmd, build_error.output)
        result['returncode'] = build_error.returncode
        result['cmd'] = build_error.cmd
        result['output'] = build_error.output

    finally:
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
    compile_keymap(result)

    # Store the results
    store_firmware_binary(result)
    store_firmware_source(result)
    store_firmware_metadata(job, result)

    return result
