import functools
import logging
import os
from pathlib import Path
from shutil import rmtree
from subprocess import check_output, CalledProcessError, STDOUT

import qmk_storage
from qmk_errors import NoSuchKeyboardError

## Environment setup
if 'GIT_BRANCH' in os.environ:
    for key in 'CHIBIOS_GIT_BRANCH', 'CHIBIOS_CONTRIB_GIT_BRANCH', 'LUFA_GIT_BRANCH', 'VUSB_GIT_BRANCH', 'QMK_GIT_BRANCH':
        if key not in os.environ:
            os.environ[key] = os.environ['GIT_BRANCH']

QMK_GIT_BRANCH = os.environ.get('QMK_GIT_BRANCH', 'master')
QMK_GIT_URL = os.environ.get('QMK_GIT_URL', 'https://github.com/qmk/qmk_firmware.git')
CHIBIOS_GIT_BRANCH = os.environ.get('CHIBIOS_GIT_BRANCH', 'master')
CHIBIOS_GIT_URL = os.environ.get('CHIBIOS_GIT_URL', 'https://github.com/qmk/ChibiOS')
CHIBIOS_CONTRIB_GIT_BRANCH = os.environ.get('CHIBIOS_CONTRIB_GIT_BRANCH', 'master')
CHIBIOS_CONTRIB_GIT_URL = os.environ.get('CHIBIOS_CONTRIB_GIT_URL', 'https://github.com/qmk/ChibiOS-Contrib')
PRINTF_GIT_BRANCH = os.environ.get('PRINTF_GIT_BRANCH', 'master')
PRINTF_GIT_URL = os.environ.get('PRINTF_GIT_URL', 'https://github.com/qmk/printf')
PICO_SDK_GIT_BRANCH = os.environ.get('PICO_SDK_GIT_BRANCH', 'master')
PICO_SDK_GIT_URL = os.environ.get('PICO_SDK_GIT_URL', 'https://github.com/pico-sdk')
LUFA_GIT_BRANCH = os.environ.get('LUFA_GIT_BRANCH', 'master')
LUFA_GIT_URL = os.environ.get('LUFA_GIT_URL', 'https://github.com/qmk/lufa')
VUSB_GIT_BRANCH = os.environ.get('VUSB_GIT_BRANCH', 'master')
VUSB_GIT_URL = os.environ.get('VUSB_GIT_URL', 'https://github.com/qmk/v-usb')

KEYMAP_DOCUMENTATION = """"This file is a QMK Configurator export. You can import this at <https://config.qmk.fm>. It can also be used directly with QMK's source code.

To setup your QMK environment check out the tutorial: <https://docs.qmk.fm/#/newbs>

You can convert this file to a keymap.c using this command: `qmk json2c {keymap}`

You can compile this keymap using this command: `qmk compile {keymap}`"
"""

ZIP_EXCLUDES = {
    'qmk_firmware': ['qmk_firmware/.build/*', 'qmk_firmware/.git/*', 'qmk_firmware/lib/chibios/.git', 'qmk_firmware/lib/chibios-contrib/.git'],
    'chibios': ['chibios/.git/*'],
    'chibios-contrib': ['chibios-contrib/.git/*'],
}


## Helper functions
def checkout_qmk(skip_cache=False, require_cache=False, branch=QMK_GIT_BRANCH):
    """Clone QMK from git.

    Deprecated: skip_cache, require_cache

    As AssertionError will be thrown if both skip_cache and
    require_cache are True, for backward compatibility.
    """
    if skip_cache and require_cache:
        raise ValueError('skip_cache and require_cache conflict!')

    if os.path.exists('qmk_firmware'):
        rmtree('qmk_firmware')

    git_clone('qmk_firmware', QMK_GIT_URL, branch)


def checkout_submodule(name, url, branch):
    """Clone a submodule to the lib directory.
    """
    os.chdir('lib')

    if os.path.exists(name):
        rmtree(name)

    git_clone(name, url, branch)

    os.chdir('..')


def checkout_chibios():
    """Do whatever is needed to get the latest version of ChibiOS and ChibiOS-Contrib.
    """
    checkout_submodule('chibios', CHIBIOS_GIT_URL, CHIBIOS_GIT_BRANCH)
    checkout_submodule('chibios-contrib', CHIBIOS_CONTRIB_GIT_URL, CHIBIOS_CONTRIB_GIT_BRANCH)
    checkout_submodule('printf', PRINTF_GIT_URL, PRINTF_GIT_BRANCH)
    checkout_submodule('pico-sdk', PICO_SDK_GIT_URL, PICO_SDK_GIT_BRANCH)


def checkout_lufa():
    """Do whatever is needed to get the latest version of LUFA.
    """
    checkout_submodule('lufa', LUFA_GIT_URL, LUFA_GIT_BRANCH)


def checkout_vusb():
    """Do whatever is needed to get the latest version of V-USB.
    """
    checkout_submodule('vusb', VUSB_GIT_URL, VUSB_GIT_BRANCH)


def git_clone(repo, git_url, git_branch):
    """Clone a git repo.
    """
    zipfile_name = repo + '.zip'
    command = ['git', 'clone', '-q', '--depth', '1', '-b', git_branch, git_url, repo]

    try:
        logging.debug('Cloning repository: %s', ' '.join(command))
        check_output(command, stderr=STDOUT, universal_newlines=True)
        os.chdir(repo)
        write_version_txt()
        repo_cloned = True

    except CalledProcessError as build_error:
        repo_cloned = False
        logging.error("Could not clone %s: %s (returncode: %s)" % (repo, build_error.output, build_error.returncode))
        logging.exception(build_error)

    os.chdir('..')

    return True


def find_keymap_path(keyboard, keymap):
    for directory in ['.', '..', '../..', '../../..', '../../../..', '../../../../..']:
        basepath = os.path.normpath('qmk_firmware/keyboards/%s/%s/keymaps' % (keyboard, directory))
        if os.path.exists(basepath):
            return '/'.join((basepath, keymap))

    logging.error('Could not find keymaps directory!')
    raise NoSuchKeyboardError('Could not find keymaps directory for: %s' % keyboard)


def store_source(zipfile_name, directory, storage_directory):
    """Store a copy of source in storage.
    """
    if directory in ZIP_EXCLUDES:
        excludes = ['-x'] * (len(ZIP_EXCLUDES[directory]) * 2)
        excludes[1::2] = ZIP_EXCLUDES[directory]
        zip_command = ['zip'] + excludes + ['-q', '-r', zipfile_name, directory]
    else:
        zip_command = ['zip', '-q', '-r', zipfile_name, directory]

    if os.path.exists(zipfile_name):
        os.remove(zipfile_name)

    try:
        logging.debug('Zipping Source: %s', zip_command)
        check_output(zip_command)
    except CalledProcessError as build_error:
        logging.error('Could not zip source, Return Code %s, Command %s', build_error.returncode, build_error.cmd)
        logging.error(build_error.output)
        os.remove(zipfile_name)
        return False

    qmk_storage.save_file(zipfile_name, os.path.join(storage_directory, zipfile_name))

    return True


def find_firmware_file(dir='.'):
    """Returns the first firmware file we find.

    Since `os.listdir()` gives us unordered results we can not guarantee which
    file will be delivered in the case of multiple firmware files. The
    assumption is that there will only be one.
    """
    for file in os.listdir(dir):
        if file[-4:] in ('.hex', '.bin'):
            return file


def git_hash():
    """Returns the current commit hash for qmk_firmware.
    """
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


def write_version_txt():
    """Write the current git hash to version.txt.
    """
    hash = check_output(['git', 'rev-parse', 'HEAD'], universal_newlines=True)
    version_txt = Path('version.txt')
    version_txt.write_text(hash + '\n')


def keymap_skeleton():
    """Returns the minimal structure needed for a keymap.json.
    """
    return {
            'version': 1,
            'notes': '',
            'keyboard': None,
            'keymap': None,
            'layout': None,
            'layers': [],
            'documentation': KEYMAP_DOCUMENTATION,
    }
