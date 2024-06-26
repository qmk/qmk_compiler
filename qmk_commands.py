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

QMK_FIRMWARE_PATH = Path(os.environ.get('QMK_FIRMWARE_PATH', 'qmk_firmware')).resolve()
QMK_GIT_BRANCH = os.environ.get('QMK_GIT_BRANCH', 'master')
QMK_GIT_URL = os.environ.get('QMK_GIT_URL', 'https://github.com/qmk/qmk_firmware.git')
CHIBIOS_GIT_BRANCH = os.environ.get('CHIBIOS_GIT_BRANCH', 'qmk-master')
CHIBIOS_GIT_URL = os.environ.get('CHIBIOS_GIT_URL', 'https://github.com/qmk/ChibiOS')
CHIBIOS_CONTRIB_GIT_BRANCH = os.environ.get('CHIBIOS_CONTRIB_GIT_BRANCH', 'qmk-master')
CHIBIOS_CONTRIB_GIT_URL = os.environ.get('CHIBIOS_CONTRIB_GIT_URL', 'https://github.com/qmk/ChibiOS-Contrib')
MCUX_SDK_GIT_BRANCH = os.environ.get('MCUX_SDK_GIT_BRANCH', 'qmk-master')
MCUX_SDK_GIT_URL = os.environ.get('MCUX_SDK_GIT_URL', 'https://github.com/qmk/mcux-sdk')
PRINTF_GIT_BRANCH = os.environ.get('PRINTF_GIT_BRANCH', 'qmk-master')
PRINTF_GIT_URL = os.environ.get('PRINTF_GIT_URL', 'https://github.com/qmk/printf')
PICOSDK_GIT_BRANCH = os.environ.get('PICOSDK_GIT_BRANCH', 'qmk-master')
PICOSDK_GIT_URL = os.environ.get('PICOSDK_GIT_URL', 'https://github.com/qmk/pico-sdk')
LUFA_GIT_BRANCH = os.environ.get('LUFA_GIT_BRANCH', 'qmk-master')
LUFA_GIT_URL = os.environ.get('LUFA_GIT_URL', 'https://github.com/qmk/lufa')
VUSB_GIT_BRANCH = os.environ.get('VUSB_GIT_BRANCH', 'qmk-master')
VUSB_GIT_URL = os.environ.get('VUSB_GIT_URL', 'https://github.com/qmk/v-usb')

KEYMAP_DOCUMENTATION = """"This file is a QMK Configurator export. You can import this at <https://config.qmk.fm>. It can also be used directly with QMK's source code.

To setup your QMK environment check out the tutorial: <https://docs.qmk.fm/#/newbs>

You can convert this file to a keymap.c using this command: `qmk json2c {keymap}`

You can compile this keymap using this command: `qmk compile {keymap}`"
"""

ZIP_EXCLUDES = [
    '*.zip',
    '.build/*',
    '.git/*',
    'lib/chibios/.git/*',
    'lib/chibios-contrib/.git/*',
    'lib/chibios-contrib/ext/mcux-sdk/.git/*',
    'lib/googletest/.git/*',
    'lib/lufa/.git/*',
    'lib/printf/.git/*',
    'lib/vusb/.git/*',
    'lib/pico-sdk/.git/*',
]


## Helper functions
def checkout_qmk(skip_cache=False, require_cache=False, branch=QMK_GIT_BRANCH):
    """Clone QMK from git.

    Deprecated: skip_cache, require_cache

    As AssertionError will be thrown if both skip_cache and
    require_cache are True, for backward compatibility.
    """
    if skip_cache and require_cache:
        raise ValueError('skip_cache and require_cache conflict!')

    if QMK_FIRMWARE_PATH.exists():
        rmtree(str(QMK_FIRMWARE_PATH))

    git_clone(QMK_FIRMWARE_PATH, QMK_GIT_URL, branch)


def checkout_submodule(relative_path, url, branch):
    """Clone a submodule to the lib directory.
    """
    submodule_path = (QMK_FIRMWARE_PATH / relative_path).resolve()

    if submodule_path.exists():
        rmtree(str(submodule_path))

    git_clone(submodule_path, url, branch)


def checkout_chibios():
    """Do whatever is needed to get the latest version of ChibiOS and ChibiOS-Contrib.
    """
    checkout_submodule('lib/chibios', CHIBIOS_GIT_URL, CHIBIOS_GIT_BRANCH)
    checkout_submodule('lib/chibios-contrib', CHIBIOS_CONTRIB_GIT_URL, CHIBIOS_CONTRIB_GIT_BRANCH)
    checkout_submodule('lib/printf', PRINTF_GIT_URL, PRINTF_GIT_BRANCH)
    checkout_submodule('lib/pico-sdk', PICOSDK_GIT_URL, PICOSDK_GIT_BRANCH)
    checkout_submodule('lib/chibios-contrib/ext/mcux-sdk', MCUX_SDK_GIT_URL, MCUX_SDK_GIT_BRANCH)


def checkout_lufa():
    """Do whatever is needed to get the latest version of LUFA.
    """
    checkout_submodule('lib/lufa', LUFA_GIT_URL, LUFA_GIT_BRANCH)


def checkout_vusb():
    """Do whatever is needed to get the latest version of V-USB.
    """
    checkout_submodule('lib/vusb', VUSB_GIT_URL, VUSB_GIT_BRANCH)


def git_clone(repo, git_url, git_branch):
    """Clone a git repo.
    """
    command = ['git', 'clone', '-q', '--depth', '1', '-b', git_branch, git_url, str(repo)]

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

    os.chdir(QMK_FIRMWARE_PATH)

    return True


def find_keymap_path(keyboard, keymap):
    for directory in ['.', '..', '../..', '../../..', '../../../..', '../../../../..']:
        basepath = (QMK_FIRMWARE_PATH / ('/keyboards/%s/%s/keymaps' % (keyboard, directory))).resolve()
        if basepath.exists():
            return basepath / keymap

    logging.error('Could not find keymaps directory!')
    raise NoSuchKeyboardError('Could not find keymaps directory for: %s' % keyboard)


def store_source(zipfile_name, directory, storage_directory):
    """Store a copy of source in storage.
    """
    excludes = ['-x'] * (len(ZIP_EXCLUDES) * 2)
    excludes[1::2] = ZIP_EXCLUDES

    zipfile_output = (directory / zipfile_name).resolve()
    zip_command = ['zip'] + excludes + ['-q', '-r', str(zipfile_output), '.'] # path of '.' will be relative to the os.chdir() below

    if os.path.exists(zipfile_output):
        os.remove(zipfile_output)

    orig_cwd = os.getcwd()
    try:
        logging.debug('Zipping Source: %s', zip_command)
        os.chdir(directory)
        check_output(zip_command)
    except CalledProcessError as build_error:
        logging.error('Could not zip source, Return Code %s, Command %s', build_error.returncode, build_error.cmd)
        logging.error(build_error.output)
        os.remove(zipfile_output)
        return False
    finally:
        os.chdir(orig_cwd)

    qmk_storage.save_file(str(zipfile_output), os.path.join(storage_directory, zipfile_name))
    os.remove(zipfile_output)

    return True


def find_firmware_file(dir='.'):
    """Returns the first firmware file we find.

    Since `os.listdir()` gives us unordered results we can not guarantee which
    file will be delivered in the case of multiple firmware files. The
    assumption is that there will only be one.
    """
    for file in os.listdir(dir):
        if file[-4:] in ('.hex', '.bin', '.uf2'):
            return file


def git_hash():
    """Returns the current commit hash for qmk_firmware.
    """
    return open(QMK_FIRMWARE_PATH / 'version.txt').read().strip()


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
