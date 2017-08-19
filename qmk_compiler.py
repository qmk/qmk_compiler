import json
import logging

from hashids import Hashids
from os import chdir, mkdir
from os.path import exists
from redis import Redis
from rq import get_current_job
from rq.decorators import job
from shutil import rmtree, copy
from subprocess import check_output, CalledProcessError, STDOUT


# The `keymap.c` template to use when a keyboard doesn't have its own
DEFAULT_KEYMAP_C = """#include "__KEYBOARD_NAME__.h"

// Helpful defines
#define _______ KC_TRNS

const uint16_t PROGMEM keymaps[][MATRIX_ROWS][MATRIX_COLS] = {
__KEYMAP_GOES_HERE__
};
"""

# Objects we need to instaniate
hashids = Hashids()
redis = Redis()


def generate_keymap_c(keyboard_name, layers):
    if exists('qmk_firmware/keyboards/%s/templates/keymap.c'):
        keymap_c = open('keyboards/%s/keymap.c' % keyboard_name).read()
    else:
        keymap_c = DEFAULT_KEYMAP_C.replace('__KEYBOARD_NAME__', keyboard_name)

    layers = []
    for layer_num, layer in enumerate(layers):
        rows = ['\t\t{' + ', '.join(row) + '}' for row in layer]
        layer_txt = ['\t[%s] = {' % layer_num]
        layer_txt.append(', \\\n'.join(rows))
        layer_txt.append('\t}')
        layers.append(''.join(layer_txt))

    keymap = '\n'.join(layers)
    keymap_c = keymap_c.replace('__KEYMAP_GOES_HERE__', keymap)

    return keymap_c


def checkout_qmk():
    if exists('qmk_firmware'):
        rmtree('qmk_firmware')

    command = ['git', 'clone', 'https://github.com/qmk/qmk_firmware.git']
    try:
        check_output(command, stderr=STDOUT, universal_newlines=True)
        return True
    except CalledProcessError as build_error:
        print("Could not check out qmk: %s (returncode:%s)" % (build_error.output, build_error.returncode))


# Public functions
@job('default', connection=redis)
def compile_firmware(keyboard_name, subproject, keymap_name, layers):
    """Compile a firmware.
    """
    checkout_qmk()
    keymap_c = generate_keymap_c(keyboard_name, layers)
    job = get_current_job()

    # Sanity checks
    if not exists('qmk_firmware/keyboards/%s' % keyboard_name):
        logging.error('Unknown keyboard: %s', keyboard_name)
        return {
            'returncode': -1,
            'command': '',
            'output': 'Unknown keyboard!',
            'firmware': None
        }

    if exists('qmk_firmware/keyboards/%s/keymaps/%s' % (keyboard_name, keymap_name)):
        logging.error('Name collision! This should not happen!')
        return {
            'returncode': -1,
            'command': '',
            'output': 'Keymap name collision!',
            'firmware': None
        }

    # Setup the keymap and build environment
    mkdir('qmk_firmware/keyboards/%s/keymaps/%s' % (keyboard_name, keymap_name))
    mkdir('firmwares/%s' % job.id)
    with open('qmk_firmware/keyboards/%s/keymaps/%s/keymap.c' % (keyboard_name, keymap_name), 'w') as keymap_file:
        keymap_file.write(keymap_c)
    with open('qmk_firmware/keyboards/%s/keymaps/%s/layers.json' % (keyboard_name, keymap_name), 'w') as layers_file:
        json.dump(layers, layers_file)

    command = ['make', '%s-%s-%s' % (keyboard_name, subproject, keymap_name)]
    result = {
        'keyboard': keyboard_name,
        'subproject': subproject,
        'keymap': keymap_name,
        'returncode': -2,
        'command': command,
        'output': '',
        'firmware': None
    }

    # Build the keyboard firmware
    try:
        logging.debug('Executing build: %s', command)
        chdir('qmk_firmware/')
        result['output'] = check_output(command, stderr=STDOUT, universal_newlines=True)
        result['returncode'] = 0
        firmware_file = '%s_%s_%s.hex' % (keyboard_name, subproject, keymap_name)

        if exists(firmware_file):
            result['firmware'] = open(firmware_file, 'r').read()
            copy(firmware_file, '../firmwares/%s/' % job.id)

    except CalledProcessError as build_error:
        result['returncode'] = build_error.returncode
        result['cmd'] = build_error.cmd
        result['output'] = build_error.output

    # Prepare the source distribution
    hash = check_output(['git', 'rev-parse', 'HEAD'])
    open('version.txt', 'w').write(hash.decode('cp437') + '\n')
    chdir('..')
    zip_file = 'firmwares/%s/qmk_firmware.zip' % job.id
    zip_command = ['zip', '-x', 'qmk_firmware/.build/*', '-x', 'qmk_firmware/.git/*', '-r', zip_file, 'qmk_firmware']
    try:
        logging.debug('Zipping Source: %s', zip_command)
        check_output(zip_command)
    except CalledProcessError as build_error:
        logging.error('Could not zip source, Return Code %s, Command %s', build_error.returncode, build_error.cmd)
        logging.error(build_error.output)

    return result
