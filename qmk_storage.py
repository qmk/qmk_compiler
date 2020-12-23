import logging
from os import environ, mkdir
from os.path import dirname, exists
from shutil import copyfile, copyfileobj

import boto3
import botocore.client
import botocore.exceptions

# Configuration
STORAGE_ENGINE = environ.get('STORAGE_ENGINE', 's3')  # 's3' or 'filesystem'
FILESYSTEM_PATH = environ.get('FILESYSTEM_PATH', 'firmwares')
S3_HOST = environ.get('S3_HOST', 'http://127.0.0.1:9000')
S3_LOCATION = environ.get('S3_LOCATION', 'nyc3')
S3_BUCKET = environ.get('S3_BUCKET', 'qmk-api')
COMPILE_S3_BUCKET = environ.get('COMPILE_S3_BUCKET', 'qmk')
S3_ACCESS_KEY = environ.get('S3_ACCESS_KEY', 'minio_dev')
S3_SECRET_KEY = environ.get('S3_SECRET_KEY', 'minio_dev_secret')
S3_SECURE = False
S3_DOWNLOAD_TIMEOUT = 7200  # 2 hours, how long S3 download URLs are good for

# The `keymap.c` template to use when a keyboard doesn't have its own
DEFAULT_KEYMAP_C = """#include QMK_KEYBOARD_H

// Helpful defines
#define _______ KC_TRNS

const uint16_t PROGMEM keymaps[][MATRIX_ROWS][MATRIX_COLS] = {
__KEYMAP_GOES_HERE__
};
"""

# Objects we need to instaniate
session = boto3.session.Session()
s3 = session.client(
    's3',
    region_name=S3_LOCATION,
    endpoint_url=S3_HOST,
    aws_access_key_id=S3_ACCESS_KEY,
    aws_secret_access_key=S3_SECRET_KEY,
    config=botocore.client.Config(signature_version='s3'),
)

# Check to see if S3 is working, and if not print an error in the log.
for bucket in [S3_BUCKET, COMPILE_S3_BUCKET]:
    try:
        s3.create_bucket(Bucket=bucket)

    except botocore.exceptions.ClientError as e:
        if e.response['Error']['Code'] not in ['BucketAlreadyOwnedByYou', 'BucketAlreadyExists']:
            logging.warning('Could not contact S3! Storage related functionality will not work!')


def delete(object, *, bucket=S3_BUCKET, **kwargs):
    """Delete an object from S3.

    Parameters

    * Key (string) -- [REQUIRED]
    * MFA (string) -- The concatenation of the authentication device's serial number, a space, and the value that is displayed on your authentication device.
    * VersionId (string) -- VersionId used to reference a specific version of the object.
    * RequestPayer (string) -- Confirms that the requester knows that she or he will be charged for the request. Bucket owners need not specify this parameter in their requests. Documentation on downloading objects from requester pays buckets can be found at http://docs.aws.amazon.com/AmazonS3/latest/dev/ObjectsinRequesterPaysBuckets.html
    """
    return s3.delete_object(Bucket=bucket, Key=object, **kwargs)


def list_objects(*, bucket=S3_BUCKET, **kwargs):
    """List the objects in our bucket.

    This function yields objects and handles pagination for you. It will only fetch as many pages as you consume.

    Parameters

    * Bucket (string) -- [REQUIRED]
    * Delimiter (string) -- A delimiter is a character you use to group keys.
    * EncodingType (string) -- Requests Amazon S3 to encode the object keys in the response and specifies the encoding method to use. An object key may contain any Unicode character; however, XML 1.0 parser cannot parse some characters, such as characters with an ASCII value from 0 to 10. For characters that are not supported in XML 1.0, you can add this parameter to request that Amazon S3 encode the keys in the response.
    * Marker (string) -- Specifies the key to start with when listing objects in a bucket.
    * MaxKeys (integer) -- Sets the maximum number of keys returned in the response. The response might contain fewer keys but will never contain more.
    * Prefix (string) -- Limits the response to keys that begin with the specified prefix.
    * RequestPayer (string) -- Confirms that the requester knows that she or he will be charged for the list objects request. Bucket owners need not specify this parameter in their requests.
    """
    if 'Bucket' not in kwargs:
        kwargs['Bucket'] = bucket

    while True:
        resp = s3.list_objects(**kwargs)

        if 'Contents' in resp:
            for obj in resp['Contents']:
                yield obj

        if 'NextContinuationToken' in resp:
            print('\nFetching more results from the S3 API.\n')
            kwargs['ContinuationToken'] = resp['NextContinuationToken']
        elif 'NextMarker' in resp:
            print('\nFetching more results from the Spaces API.\n')
            kwargs['Marker'] = resp['NextMarker']
        else:
            if 'Contents' in resp:
                del resp['Contents']
            print('Could not find any pagination information:')
            print(resp)
            break


def save_fd(fd, filename, *, bucket=S3_BUCKET):
    """Store the contents of a file-like object in the configured storage engine.
    """
    if STORAGE_ENGINE == 's3':
        logging.debug('Uploading %s to s3.', filename)
        s3.upload_fileobj(fd, bucket, filename)
    else:
        logging.debug('Writing to %s/%s.', FILESYSTEM_PATH, filename)
        if FILESYSTEM_PATH[0] == '/':
            file_path = '%s/%s' % (FILESYSTEM_PATH, filename)
        else:
            file_path = '../%s/%s' % (FILESYSTEM_PATH, filename)
        mkdir(dirname(file_path))
        copyfileobj(fd, open(file_path, 'w'))


def save_file(local_filename, remote_filename, *, bucket=S3_BUCKET):
    """Store the contents of a file in the configured storage engine.
    """
    if STORAGE_ENGINE == 's3':
        logging.debug('Uploading %s to s3: %s.', local_filename, remote_filename)
        s3.upload_file(local_filename, bucket, remote_filename)
    else:
        logging.debug('Writing to %s/%s.', FILESYSTEM_PATH, remote_filename)
        if FILESYSTEM_PATH[0] == '/':
            file_path = '%s/%s' % (FILESYSTEM_PATH, remote_filename)
        else:
            file_path = '../%s/%s' % (FILESYSTEM_PATH, remote_filename)
        mkdir(dirname(file_path))
        copyfile(local_filename, remote_filename)


def put(filename, value, *, bucket=S3_BUCKET):
    """Uploads an object to S3.
    """
    if STORAGE_ENGINE == 's3':
        try:
            object = s3.put_object(Bucket=bucket, Key=filename, Body=value)
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


def get_fd(filename, *, bucket=S3_BUCKET):
    """Retrieve an object from S3 and return a file-like object

    FIXME: This doesn't work as a context manager.
    """
    if STORAGE_ENGINE == 's3':
        s3_object = s3.get_object(Bucket=bucket, Key=filename)
        return s3_object['Body']

    else:
        file_path = '/'.join((FILESYSTEM_PATH, filename))
        if exists(file_path):
            return open(file_path)
        else:
            raise FileNotFoundError(filename)


def get(filename, *, bucket=S3_BUCKET):
    """Retrieve an object from S3
    """
    fd = get_fd(filename, bucket=bucket)
    data = fd.read()

    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return data


def get_public_url(filename, *, bucket=S3_BUCKET):
    """Returns an S3 URL a client can use to download a file.
    """
    params = {'Bucket': bucket, 'Key': filename}
    return s3.generate_presigned_url(ClientMethod='get_object', Params=params, ExpiresIn=S3_DOWNLOAD_TIMEOUT)


if __name__ == '__main__':
    print(1, put('foo', 'bar'))
    print(2, get('foo'))
