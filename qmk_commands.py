import functools
import logging
from os import chdir, listdir, environ
from os.path import exists
from shutil import rmtree
from subprocess import check_output, CalledProcessError, STDOUT


GIT_BRANCH = environ.get('GIT_BRANCH', 'master')
GIT_URL = environ.get('GIT_URL', 'https://github.com/qmk/qmk_firmware.git')

def checkout_qmk():
    if exists('qmk_firmware'):
        rmtree('qmk_firmware')

    command = ['git', 'clone', '--single-branch', '-b', GIT_BRANCH, GIT_URL]
    try:
        check_output(command, stderr=STDOUT, universal_newlines=True)
        chdir('qmk_firmware/')
        hash = check_output(['git', 'rev-parse', 'HEAD'])
        open('version.txt', 'w').write(hash.decode('cp437') + '\n')
        chdir('..')
        return True
    except CalledProcessError as build_error:
        logging.error("Could not check out qmk: %s (returncode:%s)" % (build_error.output, build_error.returncode))
        logging.exception(build_error)


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
