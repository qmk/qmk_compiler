import logging
import minio.helpers
from minio import Minio
from minio.error import ResponseError, BucketAlreadyOwnedByYou, BucketAlreadyExists
from os import environ, mkdir
from os.path import dirname, exists
from shutil import copyfile, copyfileobj

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

# The `keymap.c` template to use when a keyboard doesn't have its own
DEFAULT_KEYMAP_C = """#include QMK_KEYBOARD_H

// Helpful defines
#define _______ KC_TRNS

const uint16_t PROGMEM keymaps[][MATRIX_ROWS][MATRIX_COLS] = {
__KEYMAP_GOES_HERE__
};
"""

# Objects we need to instaniate
minio = Minio(MINIO_HOST, access_key=MINIO_ACCESS_KEY, secret_key=MINIO_SECRET_KEY, secure=MINIO_SECURE)

# Make sure our minio store is properly setup
try:
    minio.make_bucket(MINIO_BUCKET, location=MINIO_LOCATION)
except BucketAlreadyOwnedByYou as err:
    pass
except BucketAlreadyExists as err:
    pass


def save_fd(fd, filename, length, content_type='application/json'):
    """Store the contents of a file-like object in the configured storage engine.
    """
    if STORAGE_ENGINE == 'minio':
        logging.debug('Uploading %s to minio.', filename)
        try:
            minio.put_object(MINIO_BUCKET, filename, fd, length, content_type)
        except ResponseError as err:
            logging.error('Could not upload firmware binary to minio: %s', err)
            logging.exception(err)
    else:
        logging.debug('Writing to %s/%s.', FILESYSTEM_PATH, filename)
        if FILESYSTEM_PATH[0] == '/':
            file_path = '%s/%s' % (FILESYSTEM_PATH, filename)
        else:
            file_path = '../%s/%s' % (FILESYSTEM_PATH, filename)
        mkdir(dirname(file_path))
        copyfileobj(fd, open(file_path, 'w'))


def save_file(local_filename, remote_filename, content_type='application/json'):
    """Store the contents of a file in the configured storage engine.
    """
    if STORAGE_ENGINE == 'minio':
        logging.debug('Uploading %s to minio: %s.', local_filename, remote_filename)
        try:
            minio.fput_object(MINIO_BUCKET, remote_filename, local_filename, content_type)
        except ResponseError as err:
            logging.error('Could not upload firmware binary to minio: %s', err)
            logging.exception(err)
    else:
        logging.debug('Writing to %s/%s.', FILESYSTEM_PATH, remote_filename)
        if FILESYSTEM_PATH[0] == '/':
            file_path = '%s/%s' % (FILESYSTEM_PATH, remote_filename)
        else:
            file_path = '../%s/%s' % (FILESYSTEM_PATH, remote_filename)
        mkdir(dirname(file_path))
        copyfile(local_filename, remote_filename)


def get(filename):
    """Returns the contents of a requested file.
    """
    if STORAGE_ENGINE == 'minio':
        object = minio.get_object(MINIO_BUCKET, filename)
        return object.data.decode('utf-8')
    else:
        file_path = '/'.join((FILESYSTEM_PATH, filename))
        if exists(file_path):
            return open(file_path).read().decode('utf-8')
