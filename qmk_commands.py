import functools
import logging
import os
from os import chdir, listdir, environ, remove
from os.path import exists
from shutil import rmtree
from subprocess import check_output, CalledProcessError, STDOUT

import qmk_storage


GIT_BRANCH = environ.get('GIT_BRANCH', 'master')
CHIBIOS_GIT_URL = environ.get('CHIBIOS_GIT_URL', 'https://github.com/qmk/ChibiOS')
CHIBIOS_CONTRIB_GIT_URL = environ.get('CHIBIOS_CONTRIB_GIT_URL', 'https://github.com/qmk/ChibiOS-Contrib')
QMK_GIT_URL = environ.get('QMK_GIT_URL', 'https://github.com/qmk/qmk_firmware.git')
ZIP_EXCLUDES = {
    'qmk_firmware': ('qmk_firmware/.build/*', 'qmk_firmware/.git/*')
}


def checkout_qmk():
    """Do whatever is needed to get the latest version of QMK.
    """
    if exists('qmk_firmware'):
        rmtree('qmk_firmware')

    if not fetch_source():
        git_clone(QMK_GIT_URL, GIT_BRANCH)


def checkout_chibios():
    """Do whatever is needed to get the latest version of ChibiOS and ChibiOS-Contrib.
    """
    chdir('qmk_firmware/lib')

    for submodule in ('chibios', 'chibios-contrib'):
        try:
            check_output(['git', 'submodule', 'sync', submodule])
            check_output(['git', 'submodule', 'update', '--init', submodule])
        except CalledProcessError as git_error:
            logging.error('Could not fetch submodule %s!', submodule)
            logging.exception(git_error)
            logging.error(git_error.output)
            raise

    chdir('../..')


def git_clone(git_url=QMK_GIT_URL, git_branch=GIT_BRANCH):
    """Clone QMK from the github source.
    """
    repo = repo_name(git_url)
    command = ['git', 'clone', '--single-branch', '-b', git_branch, git_url, repo]

    try:
        check_output(command, stderr=STDOUT, universal_newlines=True)
        chdir(repo)
        hash = check_output(['git', 'rev-parse', 'HEAD'])
        open('version.txt', 'w').write(hash.decode('cp437') + '\n')
    except CalledProcessError as build_error:
        logging.error("Could not clone %s: %s (returncode: %s)" % (repo, build_error.output, build_error.returncode))
        logging.exception(build_error)

    chdir('..')

    if exists(repo):
        store_source(git_url)

    return True


def fetch_source(git_url=QMK_GIT_URL):
    """Retrieve a copy of source from storage.
    """
    repo = repo_name(git_url)
    repo_zip = repo + '.zip'

    if exists(repo_zip):
        remove(repo_zip)

    try:
        zipfile_data = qmk_storage.get('cache/%s.zip' % repo)
    except qmk_storage.exceptions.ClientError as e:
        logging.warning('Could not fetch %s.zip from S3: %s', repo, e.__class__.__name__)
        logging.warning(e)
        return False

    with open(repo_zip, 'xb') as zipfile:
        zipfile.write(zipfile_data)

    zip_command = ['unzip', repo_zip]
    try:
        logging.debug('Unzipping %s Source: %s', (repo, zip_command))
        check_output(zip_command)
        remove(repo_zip)
        return True

    except CalledProcessError as build_error:
        logging.error('Could not unzip source, Return Code %s, Command %s', build_error.returncode, build_error.cmd)
        logging.error(build_error.output)
        return False


def store_source(git_url=QMK_GIT_URL):
    """Store a copy of source in storage.
    """
    repo = repo_name(git_url)
    zipfile_name = repo + '.zip'

    if repo in ZIP_EXCLUDES:
        zip_command = ['zip', '-x ' + '-x'.join(ZIP_EXCLUDES[repo]), '-r', zipfile_name, repo]
    else:
        zip_command = ['zip', '-r', zipfile_name, repo]

    if exists(zipfile_name):
        remove(zipfile_name)

    try:
        logging.debug('Zipping Source: %s', zip_command)
        check_output(zip_command)
    except CalledProcessError as build_error:
        logging.error('Could not zip source, Return Code %s, Command %s', build_error.returncode, build_error.cmd)
        logging.error(build_error.output)
        remove(zipfile_name)
        return False

    qmk_storage.save_file(zipfile_name, os.path.join('cache', zipfile_name), 'application/zip')
    remove(zipfile_name)

    return True


def find_firmware_file(dir='.'):
    """Returns the first firmware file we find.

    Since `os.listdir()` gives us unordered results we can not guarantee which
    file will be delivered in the case of multiple firmware files. The
    assumption is that there will only be one.
    """
    for file in listdir(dir):
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


def repo_name(git_url):
    """Returns the name a git URL will be cloned to.
    """
    name = git_url.split('/')[-1]

    if name.endswith('.git'):
        name = name[:-4]

    return name.lower()
