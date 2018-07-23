import logging
from os import environ, mkdir
from os.path import dirname, exists
from shutil import copyfile, copyfileobj

import boto3
import botocore.client
import botocore.exceptions as exceptions


# Configuration
STORAGE_ENGINE = environ.get('STORAGE_ENGINE', 's3')  # 's3' or 'filesystem'
FILESYSTEM_PATH = environ.get('FILESYSTEM_PATH', 'firmwares')
S3_HOST = environ.get('S3_HOST', 'http://127.0.0.1:9000')
S3_LOCATION = environ.get('S3_LOCATION', 'nyc3')
S3_BUCKET = environ.get('S3_BUCKET', 'qmk')
S3_ACCESS_KEY = environ.get('S3_ACCESS_KEY', 'minio_dev')
S3_SECRET_KEY = environ.get('S3_SECRET_KEY', 'minio_dev_secret')
S3_SECURE = False

# The `keymap.c` template to use when a keyboard doesn't have its own
DEFAULT_KEYMAP_C = """#include QMK_KEYBOARD_H

// Helpful defines
#define _______ KC_TRNS

const uint16_t PROGMEM keymaps[][MATRIX_ROWS][MATRIX_COLS] = {
__KEYMAP_GOES_HERE__
};
"""

# Objects we need to instaniate
s3 = boto3.session.Session().client('s3', region_name=S3_LOCATION, endpoint_url=S3_HOST, aws_access_key_id=S3_ACCESS_KEY, aws_secret_access_key=S3_SECRET_KEY)

# Make sure our s3 store is properly setup
try:
    s3.create_bucket(Bucket=S3_BUCKET)
except botocore.exceptions.ClientError as e:
    if e.__class__.__name__ not in ['BucketAlreadyOwnedByYou', 'BucketAlreadyExists']:
        raise


def delete(object, **kwargs):
    """Delete an object from S3.

    Parameters

    * Bucket (string) -- [REQUIRED]
    * Key (string) -- [REQUIRED]
    * MFA (string) -- The concatenation of the authentication device's serial number, a space, and the value that is displayed on your authentication device.
    * VersionId (string) -- VersionId used to reference a specific version of the object.
    * RequestPayer (string) -- Confirms that the requester knows that she or he will be charged for the request. Bucket owners need not specify this parameter in their requests. Documentation on downloading objects from requester pays buckets can be found at http://docs.aws.amazon.com/AmazonS3/latest/dev/ObjectsinRequesterPaysBuckets.html
    """
    return s3.delete_object(Bucket=S3_BUCKET, Key=object, **kwargs)


def list_objects(**kwargs):
    """List the objects in our bucket.

    Parameters

    * Bucket (string) -- [REQUIRED]
    * Delimiter (string) -- A delimiter is a character you use to group keys.
    * EncodingType (string) -- Requests Amazon S3 to encode the object keys in the response and specifies the encoding method to use. An object key may contain any Unicode character; however, XML 1.0 parser cannot parse some characters, such as characters with an ASCII value from 0 to 10. For characters that are not supported in XML 1.0, you can add this parameter to request that Amazon S3 encode the keys in the response.
    * Marker (string) -- Specifies the key to start with when listing objects in a bucket.
    * MaxKeys (integer) -- Sets the maximum number of keys returned in the response. The response might contain fewer keys but will never contain more.
    * Prefix (string) -- Limits the response to keys that begin with the specified prefix.
    * RequestPayer (string) -- Confirms that the requester knows that she or he will be charged for the list objects request. Bucket owners need not specify this parameter in their requests.
    """
    return s3.list_objects_v2(Bucket=S3_BUCKET, **kwargs)


def save_fd(fd, filename, length, content_type='application/json'):
    """Store the contents of a file-like object in the configured storage engine.
    """
    if STORAGE_ENGINE == 's3':
        logging.debug('Uploading %s to s3.', filename)
        s3.upload_fileobj(fd, S3_BUCKET, filename)
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
    if STORAGE_ENGINE == 's3':
        logging.debug('Uploading %s to s3: %s.', local_filename, remote_filename)
        s3.upload_file(local_filename, S3_BUCKET, remote_filename)
    else:
        logging.debug('Writing to %s/%s.', FILESYSTEM_PATH, remote_filename)
        if FILESYSTEM_PATH[0] == '/':
            file_path = '%s/%s' % (FILESYSTEM_PATH, remote_filename)
        else:
            file_path = '../%s/%s' % (FILESYSTEM_PATH, remote_filename)
        mkdir(dirname(file_path))
        copyfile(local_filename, remote_filename)


def put(filename, value):
    """Uploads an object to S3.
    """
    if STORAGE_ENGINE == 's3':
        try:
            object = s3.put_object(Bucket=S3_BUCKET, Key=filename, Body=value)
            return object
        except botocore.exceptions.ClientError as e:
            if e.response['Error']['Code'] == "404":
                return False
            else:
                raise
    else:
        logging.debug('Writing to %s.', FILESYSTEM_PATH, filename)
        if FILESYSTEM_PATH[0] == '/':
            file_path = '%s/%s' % (FILESYSTEM_PATH, filename)
        else:
            file_path = '../%s/%s' % (FILESYSTEM_PATH, filename)
        mkdir(dirname(file_path))
        open(file_path, 'w').write(value)


def get_fd(filename):
    """Retrieve an object from S3 and return a file-like object
    """
    if STORAGE_ENGINE == 's3':
        s3_object = s3.get_object(Bucket=S3_BUCKET, Key=filename)
        return s3_object['Body']

    else:
        file_path = '/'.join((FILESYSTEM_PATH, filename))
        if exists(file_path):
            return open(file_path)
        else:
            raise FileNotFoundError(filename)


def get(filename):
    """Retrieve an object from S3
    """
    fd = get_fd(filename)
    data = fd.read()
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return data


if __name__ == '__main__':
    print(1, put('foo', 'bar'))
    print(2, get('foo'))
