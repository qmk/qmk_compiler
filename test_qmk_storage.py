import datetime
from io import BytesIO
from tempfile import NamedTemporaryFile

import qmk_storage


def test_put_and_get():
    """Make sure we can store a string and retrieve it.
    """
    test_key = 'qmk_compiler_test_unique_key_name'

    # Make sure our test key doesn't exist
    try:
        qmk_storage.get(test_key)
        raise RuntimeError('%s exists on S3 when it should not!' % test_key)
    except Exception as e:
        if e.__class__.__name__ != 'NoSuchKey':
            raise

    # Write it to S3
    qmk_storage.put(test_key, 'hello')

    # Make sure we can retrieve it
    saved_file = qmk_storage.get(test_key)
    qmk_storage.delete(test_key)
    assert saved_file == 'hello'


def test_delete():
    """Create and then delete an object from s3, make sure we can't fetch it afterward."""
    test_key = 'qmk_compiler_test_unique_key_name'

    # Make sure our test key doesn't exist
    try:
        qmk_storage.get(test_key)
        raise RuntimeError('%s exists on S3 when it should not!' % test_key)
    except Exception as e:
        if e.__class__.__name__ != 'NoSuchKey':
            raise

    # Store a test key we can delete
    qmk_storage.put(test_key, 'hello')
    assert qmk_storage.get(test_key) == 'hello'
    qmk_storage.delete(test_key)

    # Make sure it actually deleted
    try:
        qmk_storage.get(test_key)
        raise RuntimeError('%s exists on S3 when it should not!' % test_key)
    except Exception as e:
        if e.__class__.__name__ != 'NoSuchKey':
            raise


def test_list_objects():
    """Make sure we can list objects on S3.
    """
    x = 0
    for obj in qmk_storage.list_objects():
        assert 'Key' in obj
        assert type(obj.get('LastModified')) == datetime.datetime

        if x > 5:
            break
        x += 1


# FIXME: Add a test for pagination here.


def test_save_fd():
    """Make sure we can stream file-like objects to S3.
    """
    test_key = 'qmk_compiler_test_unique_key_name'

    # Make sure our test key doesn't exist
    try:
        qmk_storage.get(test_key)
        raise RuntimeError('%s exists on S3 when it should not!' % test_key)
    except Exception as e:
        if e.__class__.__name__ != 'NoSuchKey':
            raise

    # Save our file in S3
    with BytesIO(b'hello') as fd:
        qmk_storage.save_fd(fd, test_key)

    # Make sure we get it back
    saved_file = qmk_storage.get(test_key)
    qmk_storage.delete(test_key)
    assert saved_file == 'hello'


def test_save_file():
    """Make sure we can store a file and retrieve it.
    """
    test_key = 'qmk_compiler_test_unique_key_name'

    # Make sure our test key doesn't exist
    try:
        qmk_storage.get(test_key)
        raise RuntimeError('%s exists on S3 when it should not!' % test_key)
    except Exception as e:
        if e.__class__.__name__ != 'NoSuchKey':
            raise

    # Write it to S3
    with NamedTemporaryFile(mode='w', encoding='utf-8') as tempfile:
        tempfile.write('hello')
        tempfile.flush()
        qmk_storage.save_file(tempfile.name, test_key)

    # Make sure we can retrieve it
    saved_file = qmk_storage.get(test_key)
    qmk_storage.delete(test_key)
    assert saved_file == 'hello'


def test_get_fd():
    """Make sure we can get a file with a file-like interface
    """
    test_key = 'qmk_compiler_test_unique_key_name'

    # Make sure our test key doesn't exist
    try:
        qmk_storage.get(test_key)
        raise RuntimeError('%s exists on S3 when it should not!' % test_key)
    except Exception as e:
        if e.__class__.__name__ != 'NoSuchKey':
            raise

    # Create it on S3
    qmk_storage.put(test_key, 'hello')

    # Make sure we can retrieve it
    fd = qmk_storage.get_fd(test_key)
    saved_file = fd.read()
    fd.close()
    qmk_storage.delete(test_key)
    assert saved_file == b'hello'
