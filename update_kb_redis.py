from pathlib import Path
from os import chdir
from os.path import exists
from shutil import rmtree
from subprocess import check_output, STDOUT, run, PIPE
from time import strftime
import json
import re

from rq.decorators import job

import qmk_redis
from qmk_commands import checkout_qmk, memoize, git_hash

debug = False


@job('default', connection=qmk_redis.redis)
def update_needed(**update_info):
    """Called when updates happen to QMK Firmware.
    """
    qmk_redis.set('qmk_needs_update', True)


@job('default', connection=qmk_redis.redis)
def update_kb_redis():
    """Called to update qmk_firmware.
    """
    update_kb_redis = Path('update_kb_redis')
    qmk_firmware = Path('qmk_firmware')

    # Clean up the environment and fetch the latest source
    if not debug:
        # Create and enter a separate update_kb_redis directory to avoid conflicting with live builds
        if update_kb_redis.exists():
            rmtree(update_kb_redis)

        update_kb_redis.mkdir()
        chdir(update_kb_redis)

    if not debug or not qmk_firmware.exists():
        checkout_qmk(skip_cache=True)

    # Enter the qmk_firmware directory
    chdir(qmk_firmware)

    # Update redis with the latest keyboard data
    run(['qmk', 'generate-api'])
    api_dir = Path('api_data/v1')
    keyboards_dir = api_dir / 'keyboards'

    for keyboard_dir in keyboards_dir.glob('**'):
        keyboard_name = keyboard_dir.relative_to(keyboards_dir).as_posix()
        keyboard_info = keyboard_dir / 'info.json'
        keyboard_readme = keyboard_dir / 'readme.md'

        if keyboard_info.exists():
            info_json = json.load(keyboard_info.open())
            redis_json = info_json['keyboards'][keyboard_name]
            qmk_redis.set('qmk_api_kb_%s' % (keyboard_name), redis_json)

        if keyboard_readme.exists():
            qmk_redis.set('qmk_api_kb_%s_readme' % (keyboard_name), keyboard_readme.read_text())

    # Update the USB list
    usb_json = json.load((api_dir / 'usb.json').open())
    redis_usb = usb_json['usb']
    qmk_redis.set('qmk_api_usb_list', redis_usb)

    # Update the Keyboard list
    keyboard_json = json.load((api_dir / 'keyboard_list.json').open())
    redis_keyboard = keyboard_json['keyboards']
    qmk_redis.set('qmk_api_keyboards', redis_keyboard)

    # Leave qmk_firmware
    chdir('..')

    # Set some metadata
    qmk_redis.set('qmk_api_last_updated', {'git_hash': git_hash(), 'last_updated': strftime('%Y-%m-%d %H:%M:%S %Z')})
    qmk_redis.set('qmk_needs_update', False)
    print('\n*** All keys successfully written to redis!')

    if not debug:
        # Leave update_kb_redis
        chdir('..')

    return True


if __name__ == '__main__':
    debug = True

    update_kb_redis()
