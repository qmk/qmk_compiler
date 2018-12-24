import functools
import logging
import os
from os.path import exists
from shutil import rmtree
from subprocess import check_output, CalledProcessError, STDOUT

from dhooks import Embed, Webhook

import qmk_storage


CHIBIOS_GIT_BRANCH = os.environ.get('GIT_BRANCH', 'qmk')
CHIBIOS_GIT_URL = os.environ.get('CHIBIOS_GIT_URL', 'https://github.com/qmk/ChibiOS')
CHIBIOS_CONTRIB_GIT_BRANCH = os.environ.get('GIT_BRANCH', 'qmk')
CHIBIOS_CONTRIB_GIT_URL = os.environ.get('CHIBIOS_CONTRIB_GIT_URL', 'https://github.com/qmk/ChibiOS-Contrib')
DISCORD_WARNING_SENT = False
DISCORD_WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK_URL')
DISCORD_WEBHOOK_INFO_URL = os.environ.get('DISCORD_WEBHOOK_INFO_URL', DISCORD_WEBHOOK_URL)
DISCORD_WEBHOOK_WARNING_URL = os.environ.get('DISCORD_WEBHOOK_WARNING_URL', DISCORD_WEBHOOK_URL)
DISCORD_WEBHOOK_ERROR_URL = os.environ.get('DISCORD_WEBHOOK_ERROR_URL', DISCORD_WEBHOOK_URL)
QMK_GIT_BRANCH = os.environ.get('GIT_BRANCH', 'master')
QMK_GIT_URL = os.environ.get('QMK_GIT_URL', 'https://github.com/qmk/qmk_firmware.git')
ZIP_EXCLUDES = {
    'qmk_firmware': ('qmk_firmware/.build/*', 'qmk_firmware/.git/*', 'qmk_firmware/lib/chibios/.git', 'qmk_firmware/lib/chibios-contrib/.git'),
    'chibios': ('chibios/.git/*'),
    'chibios-contrib': ('chibios-contrib/.git/*')
}

severities = {
    'error': (':open_mouth:', DISCORD_WEBHOOK_ERROR_URL),
    'info': (':nerd_face:', DISCORD_WEBHOOK_INFO_URL),
    'warning': (':upside_down_face:', DISCORD_WEBHOOK_WARNING_URL)
}


def discord_msg(severity, message, include_icon=True):
    """Send a simple text message to discord.
    """
    global DISCORD_WARNING_SENT

    severity_icon, discord_url = severities[severity]

    if not discord_url or discord_url == 'none':
        if not DISCORD_WARNING_SENT:
            DISCORD_WARNING_SENT = True
            print('Warning: DISCORD_WEBHOOK_URL not configured, will not send messages to discord.')
        return

    discord = Webhook(discord_url)

    if include_icon:
        discord.send(severity_icon + ' ' + message)
    else:
        discord.send(message)


def discord_embed(severity, source, title, description=None, **fields):
    """Send an embedded message to discord.
    """
    global DISCORD_WARNING_SENT

    severity_icon, discord_url = severities[severity]

    if not discord_url or discord_url == 'none':
        if not DISCORD_WARNING_SENT:
            DISCORD_WARNING_SENT = True
            print('Warning: DISCORD_WEBHOOK_URL not configured, will not send messages to discord.')
        return

    discord = Webhook(discord_url)
    title = severity_icon + ' ' + title
    embed = Embed(title=title, description=description, color=0xff0000, timestamp='now')
    embed.set_author(source)
    for field, value in fields.items():
        embed.add_field(field, value)
    discord.send(embed=embed)


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

    if exists('qmk_firmware'):
        rmtree('qmk_firmware')

    if require_cache:
        fetch_source(repo_name(QMK_GIT_URL))
    elif skip_cache or not fetch_source(repo_name(QMK_GIT_URL)):
        git_clone(QMK_GIT_URL, QMK_GIT_BRANCH)


def checkout_chibios():
    """Do whatever is needed to get the latest version of ChibiOS and ChibiOS-Contrib.
    """
    chibios = ('chibios', CHIBIOS_GIT_URL, CHIBIOS_GIT_BRANCH)
    chibios_contrib = ('chibios-contrib', CHIBIOS_CONTRIB_GIT_URL, CHIBIOS_CONTRIB_GIT_BRANCH)

    os.chdir('qmk_firmware/lib')

    for submodule, git_url, git_branch in chibios, chibios_contrib:
        if exists(submodule):
            rmtree(submodule)

        if not fetch_source(submodule):
            git_clone(git_url, git_branch)

    os.chdir('../..')


def git_clone(git_url=QMK_GIT_URL, git_branch=QMK_GIT_BRANCH):
    """Clone a git repo.
    """
    repo = repo_name(git_url)
    zipfile_name = repo + '.zip'
    command = ['git', 'clone', '--single-branch', '-b', git_branch, git_url, repo]

    try:
        check_output(command, stderr=STDOUT, universal_newlines=True)
        os.chdir(repo)
        hash = check_output(['git', 'rev-parse', 'HEAD'])
        open('version.txt', 'w').write(hash.decode('cp437') + '\n')
        repo_cloned = True

    except CalledProcessError as build_error:
        repo_cloned = False
        logging.error("Could not clone %s: %s (returncode: %s)" % (repo, build_error.output, build_error.returncode))
        logging.exception(build_error)

    os.chdir('..')

    if repo_cloned:
        store_source(zipfile_name, repo, 'cache')

    return True


def fetch_source(repo):
    """Retrieve a copy of source from storage.
    """
    repo_zip = repo + '.zip'

    if exists(repo_zip):
        os.remove(repo_zip)

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
        os.remove(repo_zip)
        return True

    except CalledProcessError as build_error:
        logging.error('Could not unzip source, Return Code %s, Command %s', build_error.returncode, build_error.cmd)
        logging.error(build_error.output)
        return False


def store_source(zipfile_name, directory, storage_directory):
    """Store a copy of source in storage.
    """
    if directory in ZIP_EXCLUDES:
        zip_command = ['zip', '-x ' + '-x'.join(ZIP_EXCLUDES[directory]), '-r', zipfile_name, directory]
    else:
        zip_command = ['zip', '-r', zipfile_name, directory]

    if exists(zipfile_name):
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
