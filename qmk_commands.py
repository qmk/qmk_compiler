import functools
import logging
from os import chdir, listdir, environ, remove
from os.path import exists
from shutil import rmtree
from subprocess import check_output, CalledProcessError, STDOUT

import qmk_storage


GIT_BRANCH = environ.get('GIT_BRANCH', 'master')
GIT_URL = environ.get('GIT_URL', 'https://github.com/qmk/qmk_firmware.git')

def checkout_qmk():
    """Do whatever is needed to get the latest version of QMK.
    """
    if exists('qmk_firmware'):
        rmtree('qmk_firmware')

    if not fetch_qmk_source():
        git_clone_qmk()


def git_clone_qmk():
    """Clone QMK from the github source.
    """
    command = ['git', 'clone', '--single-branch', '-b', GIT_BRANCH, GIT_URL]
    try:
        check_output(command, stderr=STDOUT, universal_newlines=True)
        chdir('qmk_firmware/')
        hash = check_output(['git', 'rev-parse', 'HEAD'])
        open('version.txt', 'w').write(hash.decode('cp437') + '\n')
        chdir('..')
        store_qmk_source('qmk_firmware.zip', 'cache/qmk_firmware.zip')
        return True
    except CalledProcessError as build_error:
        logging.error("Could not check out qmk: %s (returncode:%s)" % (build_error.output, build_error.returncode))
        logging.exception(build_error)
        chdir('..')


def fetch_qmk_source():
    """Retrieve a copy of the QMK source from storage.
    """
    if exists('qmk_firmware.zip'):
        remove('qmk_firmware.zip')

    try:
        zipfile_data = qmk_storage.get('cache/qmk_firmware.zip')
    except qmk_storage.exceptions.ClientError as e:
        logging.warning('Could not fetch zip from S3: %s', e.__class__.__name__)
        logging.warning(e)
        return False

    with open('qmk_firmware.zip', 'xb') as zipfile:
        zipfile.write(zipfile_data)

    zip_command = ['unzip', 'qmk_firmware.zip']
    try:
        logging.debug('Unzipping QMK Source: %s', zip_command)
        check_output(zip_command)
        remove('qmk_firmware.zip')
        return True

    except CalledProcessError as build_error:
        logging.error('Could not unzip source, Return Code %s, Command %s', build_error.returncode, build_error.cmd)
        logging.error(build_error.output)
        return False


def store_qmk_source(zipfile_name, storage_path):
    """Store a copy of the QMK source in storage.
    """
    if exists(zipfile_name):
        remove(zipfile_name)

    zip_command = ['zip', '-x', 'qmk_firmware/.build/*', '-x', 'qmk_firmware/.git/*', '-r', zipfile_name, 'qmk_firmware']
    try:
        logging.debug('Zipping Source: %s', zip_command)
        check_output(zip_command)
    except CalledProcessError as build_error:
        logging.error('Could not zip source, Return Code %s, Command %s', build_error.returncode, build_error.cmd)
        logging.error(build_error.output)
        remove(zipfile_name)
        return False

    qmk_storage.save_file(zipfile_name, storage_path, 'application/zip')
    remove(zipfile_name)
    return True


def find_firmware_file():
    """Returns the first firmware file we find.

    Since `os.listdir()` gives us unordered results we can not guarantee which
    file will be delivered in the case of multiple firmware files. The
    assumption is that there will only be one.
    """
    for file in listdir('.'):
        if file[-4:] in ('.hex', '.bin'):
            return file


def git_hash():
    """Returns the current commit hash for qmk_firmware.
    """
    if not exists('qmk_firmware'):
        checkout_qmk()

    return open('qmk_firmware/version.txt').read().strip()


def memoize(obj):
    """Cache the results from a function call.
    """
    cache = obj.cache = {}

    @functools.wraps(obj)
    def memoizer(*args, **kwargs):
        key = str(args) + str(kwargs)
        if key not in cache:
            cache[key] = obj(*args, **kwargs)
        return cache[key]

    return memoizer
