import functools
import logging
import os
from pathlib import Path
from shutil import rmtree
from subprocess import check_output, CalledProcessError, STDOUT

from dhooks import Embed, Webhook

import qmk_storage
from qmk_errors import NoSuchKeyboardError

## Environment setup
if 'GIT_BRANCH' in os.environ:
    for key in 'CHIBIOS_GIT_BRANCH', 'CHIBIOS_CONTRIB_GIT_BRANCH', 'LUFA_GIT_BRANCH', 'VUSB_GIT_BRANCH', 'QMK_GIT_BRANCH':
        if key not in os.environ:
            os.environ[key] = os.environ['GIT_BRANCH']

DISCORD_WARNING_SENT = False
DISCORD_WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK_URL')
DISCORD_WEBHOOK_INFO_URL = os.environ.get('DISCORD_WEBHOOK_INFO_URL', DISCORD_WEBHOOK_URL)
DISCORD_WEBHOOK_WARNING_URL = os.environ.get('DISCORD_WEBHOOK_WARNING_URL', DISCORD_WEBHOOK_URL)
DISCORD_WEBHOOK_ERROR_URL = os.environ.get('DISCORD_WEBHOOK_ERROR_URL', DISCORD_WEBHOOK_URL)

QMK_GIT_BRANCH = os.environ.get('QMK_GIT_BRANCH', 'master')
QMK_GIT_URL = os.environ.get('QMK_GIT_URL', 'https://github.com/qmk/qmk_firmware.git')
CHIBIOS_GIT_BRANCH = os.environ.get('CHIBIOS_GIT_BRANCH', 'qmk')
CHIBIOS_GIT_URL = os.environ.get('CHIBIOS_GIT_URL', 'https://github.com/qmk/ChibiOS')
CHIBIOS_CONTRIB_GIT_BRANCH = os.environ.get('CHIBIOS_CONTRIB_GIT_BRANCH', 'qmk')
CHIBIOS_CONTRIB_GIT_URL = os.environ.get('CHIBIOS_CONTRIB_GIT_URL', 'https://github.com/qmk/ChibiOS-Contrib')
LUFA_GIT_BRANCH = os.environ.get('LUFA_GIT_BRANCH', 'master')
LUFA_GIT_URL = os.environ.get('LUFA_GIT_URL', 'https://github.com/qmk/lufa')
VUSB_GIT_BRANCH = os.environ.get('VUSB_GIT_BRANCH', 'master')
VUSB_GIT_URL = os.environ.get('VUSB_GIT_URL', 'https://github.com/obdev/v-usb')

ZIP_EXCLUDES = {
    'qmk_firmware': ('qmk_firmware/.build/*', 'qmk_firmware/.git/*', 'qmk_firmware/lib/chibios/.git', 'qmk_firmware/lib/chibios-contrib/.git'),
    'chibios': ('chibios/.git/*'),
    'chibios-contrib': ('chibios-contrib/.git/*'),
}

severities = {
    'error': (':open_mouth:', DISCORD_WEBHOOK_ERROR_URL),
    'info': (':nerd_face:', DISCORD_WEBHOOK_INFO_URL),
    'warning': (':upside_down_face:', DISCORD_WEBHOOK_WARNING_URL),
}


## Helper functions
def discord_msg(severity, message, include_icon=True):
    """Send a simple text message to discord.
    """
    global DISCORD_WARNING_SENT

    severity_icon, discord_url = severities[severity]
    if include_icon:
        message = severity_icon + ' ' + message

    if not discord_url or discord_url == 'none':
        if not DISCORD_WARNING_SENT:
            DISCORD_WARNING_SENT = True
            logging.warning('DISCORD_WEBHOOK_URL not configured, will not send messages to discord.')
        logging.info('Discord message not sent: %s', message)
        return

    try:
        discord = Webhook(discord_url)
        discord.send(message)
    except Exception as e:
        logging.error('Unhandled exception when sending discord message:')
        logging.exception(e)


def discord_embed(severity, source, title, description=None, **fields):
    """Send an embedded message to discord.
    """
    global DISCORD_WARNING_SENT

    severity_icon, discord_url = severities[severity]

    if not discord_url or discord_url == 'none':
        if not DISCORD_WARNING_SENT:
            DISCORD_WARNING_SENT = True
            logging.warning('DISCORD_WEBHOOK_URL not configured, will not send messages to discord.')
        logging.info('Discord embed not sent: %s: %s: %s', title, description, fields)
        return

    try:
        discord = Webhook(discord_url)
        title = severity_icon + ' ' + title
        embed = Embed(title=title, description=description, color=0xff0000, timestamp='now')
        embed.set_author(source)
        for field, value in fields.items():
            embed.add_field(field, value)
        discord.send(embed=embed)
    except Exception as e:
        logging.error('Unhandled exception when sending discord embed:')
        logging.exception(e)


def checkout_qmk(skip_cache=False, require_cache=False):
    """Do whatever is needed to get the latest version of QMK.

    If require_cache is true we only fetch the cached zip file. If
    skip_cache is true we only clone the source from git. Default
    behavior is to attempt to fetch the cached zip and if that
    fails fall back to cloning from git.

    As AssertionError will be thrown if both skip_cache and
    require_cache are True.
    """
    if skip_cache and require_cache:
        raise ValueError('skip_cache and require_cache conflict!')

    if os.path.exists('qmk_firmware'):
        rmtree('qmk_firmware')

    if require_cache:
        fetch_source(repo_name(QMK_GIT_URL))
    elif skip_cache or not fetch_source(repo_name(QMK_GIT_URL)):
        git_clone(QMK_GIT_URL, QMK_GIT_BRANCH)


def checkout_submodule(name, url, branch):
    """Clone a submodule to the lib directory.
    """
    os.chdir('qmk_firmware/lib')

    if os.path.exists(name):
        rmtree(name)

    if not fetch_source(name):
        git_clone(url, branch)

    os.chdir('../..')


def checkout_chibios():
    """Do whatever is needed to get the latest version of ChibiOS and ChibiOS-Contrib.
    """
    checkout_submodule('chibios', CHIBIOS_GIT_URL, CHIBIOS_GIT_BRANCH)
    checkout_submodule('chibios-contrib', CHIBIOS_CONTRIB_GIT_URL, CHIBIOS_CONTRIB_GIT_BRANCH)


def checkout_lufa():
    """Do whatever is needed to get the latest version of LUFA.
    """
    checkout_submodule('lufa', LUFA_GIT_URL, LUFA_GIT_BRANCH)


def checkout_vusb():
    """Do whatever is needed to get the latest version of V-USB.
    """
    checkout_submodule('vusb', VUSB_GIT_URL, VUSB_GIT_BRANCH)


def git_clone(git_url=QMK_GIT_URL, git_branch=QMK_GIT_BRANCH):
    """Clone a git repo.
    """
    repo = repo_name(git_url)
    zipfile_name = repo + '.zip'
    command = ['git', 'clone', '--single-branch', '-b', git_branch, git_url, repo]

    try:
        logging.debug('Cloning qmk_firmware: %s', ' '.join(command))
        check_output(command, stderr=STDOUT, universal_newlines=True)
        os.chdir(repo)
        write_version_txt()
        repo_cloned = True

    except CalledProcessError as build_error:
        repo_cloned = False
        logging.error("Could not clone %s: %s (returncode: %s)" % (repo, build_error.output, build_error.returncode))
        logging.exception(build_error)

    os.chdir('..')

    if repo_cloned:
        store_source(zipfile_name, repo, 'cache')

    return True


def fetch_source(repo, uncompress=True):
    """Retrieve a copy of source from storage.
    """
    repo_zip = repo + '.zip'

    if os.path.exists(repo_zip):
        os.remove(repo_zip)

    try:
        zipfile_data = qmk_storage.get('cache/%s.zip' % repo)
    except qmk_storage.exceptions.ClientError as e:
        logging.warning('Could not fetch %s.zip from S3: %s', repo, e.__class__.__name__)
        logging.warning(e)
        return False

    with open(repo_zip, 'xb') as zipfile:
        zipfile.write(zipfile_data)

    if uncompress:
        return unzip_source(repo_zip)
    else:
        return True

def unzip_source(repo_zip):
    """Unzip a source repo.
    """
    zip_command = ['unzip', repo_zip]
    try:
        logging.debug('Unzipping Source: %s', (zip_command))
        check_output(zip_command)
        os.remove(repo_zip)  # FIXME: Do I need to remove this? It's removed in #48, but I think I need it?
        return True

    except CalledProcessError as build_error:
        logging.error('Could not unzip source, Return Code %s, Command %s', build_error.returncode, build_error.cmd)
        logging.error(build_error.output)
        return False


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
    if not os.path.exists('qmk_firmware'):
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


def write_version_txt():
    """Write the current git hash to version.txt.
    """
    hash = check_output(['git', 'rev-parse', 'HEAD'], universal_newlines=True)
    version_txt = Path('version.txt')
    version_txt.write_text(hash + '\n')
