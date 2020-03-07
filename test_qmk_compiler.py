"""Test suite for qmk_compiler.

This file tests most of the functionality in qmk_compiler. Given the nature
of qmk_compiler most of the functionality depends on qmk_firmware being
checked out and available. To satisfy this requirement
`test_0000_checkout_qmk_skip_cache()` must be tested first, and
`test_9999_teardown()` must be tested last. It would be better if this were
setup as a test fixture, and some day it will be. Setting up a proper test
fixture here is a non-trivial task and will take work that the author has
not had time to put in yet.
"""
import filecmp
import os
import os.path
import re
import shutil
from tempfile import mkstemp

import pytest

import update_kb_redis
import qmk_commands

############################################################################
# Setup Environment                                                        #
############################################################################


def test_0000_checkout_qmk_skip_cache():
    """Make sure that we successfully git clone qmk_firmware and generate the version.txt hash.
    """
    qmk_commands.checkout_qmk(skip_cache=True)
    assert os.path.exists('qmk_firmware/version.txt')


############################################################################
# Begin Tests                                                              #
############################################################################


# Source code retrieval testing. Make sure we're storing and fetching the
# correct source every way we can. At least test_0001 must be run before any
# other test in this file or they will all fail.
def test_0001_fetch_source_qmk():
    """Make sure that we can upload a qmk_firmware.zip and download it again.
    """
    os.rename('qmk_firmware.zip', 'qmk_firmware_cloned.zip')
    qmk_commands.fetch_source('qmk_firmware', uncompress=False)
    assert filecmp.cmp('qmk_firmware_cloned.zip', 'qmk_firmware.zip')
    os.remove('qmk_firmware_cloned.zip')
    os.remove('qmk_firmware.zip')


def test_0002_checkout_qmk_cache():
    """Make sure that we fetch the cache from QMK and don't clone from git.
    """
    git_hash = open('qmk_firmware/version.txt', 'r').read()
    shutil.rmtree('qmk_firmware')
    update_kb_redis.checkout_qmk(require_cache=True)
    cached_hash = open('qmk_firmware/version.txt', 'r').read()
    assert git_hash == cached_hash


def test_0003_checkout_qmk_skip_and_require_cache():
    """Make sure that we can't pass skip_cache and require_cache at the same time
    """
    with pytest.raises(ValueError):
        update_kb_redis.checkout_qmk(skip_cache=True, require_cache=True)


# Test out functions that require qmk_firmware be in place on disk.
def test_0010_keyboard_list():
    """Test qmk_commands.keyboard.list()
    """
    keyboard_list = update_kb_redis.list_keyboards()
    assert len(keyboard_list) > 0

    for keyboard in keyboard_list:
        assert os.path.exists(os.path.join('qmk_firmware', 'keyboards', keyboard))


def test_0011_find_firmware_file_hex():
    """Make sure that qmk_commands.find_firmware_file() can find a hex file.
    """
    fd, test_firmware = mkstemp(suffix='.hex', dir='.')
    firmware_file = qmk_commands.find_firmware_file()
    os.remove(test_firmware)
    assert os.path.split(test_firmware)[-1] == firmware_file


def test_0012_find_firmware_file_bin():
    """Make sure that qmk_commands.find_firmware_file() can find a bin file.
    """
    fd, test_firmware = mkstemp(suffix='.bin', dir='.')
    firmware_file = qmk_commands.find_firmware_file()
    os.remove(test_firmware)
    assert os.path.split(test_firmware)[-1] == firmware_file


def test_0013_git_hash():
    """Make sure that we get a valid hex string in the git hash.
    """
    hash = qmk_commands.git_hash()
    assert re.match(r'^[0-9a-f]+$', hash)
    assert len(hash) == 40


def test_0014_repo_name_qmk_firmware():
    """Make sure that the qmk_firmware git url is reliably turned into qmk_firmware.
    """
    assert qmk_commands.repo_name('https://github.com/qmk/qmk_firmware.git') == 'qmk_firmware'


def test_0015_repo_name_chibios():
    """Make sure that the qmk_firmware git url is reliably turned into qmk_firmware.
    """
    assert qmk_commands.repo_name('https://github.com/qmk/ChibiOS.git') == 'chibios'


def test_0016_repo_name_chibios_contrib():
    """Make sure that the qmk_firmware git url is reliably turned into qmk_firmware.
    """
    assert qmk_commands.repo_name('https://github.com/qmk/ChibiOS-Contrib.git') == 'chibios-contrib'


def test_0017_find_all_layouts_cluecard():
    """Make sure that update_kb_redis.find_all_layouts() can find the cluecard layout.
    """
    layouts = update_kb_redis.find_all_layouts('clueboard/card')
    assert list(layouts) == ['LAYOUT']
    assert layouts['LAYOUT'] == {
        'key_count': 12,
        'layout': [
            {'label': 'k00', 'w': 1, 'x': 0, 'y': 0},
            {'label': 'k01', 'w': 1, 'x': 1, 'y': 0},
            {'label': 'k02', 'w': 1, 'x': 2, 'y': 0},
            {'label': 'k10', 'w': 1, 'x': 0, 'y': 1},
            {'label': 'k12', 'w': 1, 'x': 1, 'y': 1},
            {'label': 'k20', 'w': 1, 'x': 0, 'y': 2},
            {'label': 'k21', 'w': 1, 'x': 1, 'y': 2},
            {'label': 'k22', 'w': 1, 'x': 2, 'y': 2},
            {'label': 'k11', 'w': 1, 'x': 0, 'y': 3},
            {'label': 'k30', 'w': 1, 'x': 0, 'y': 4},
            {'label': 'k31', 'w': 1, 'x': 1, 'y': 4},
            {'label': 'k32', 'w': 1, 'x': 2, 'y': 4},
        ]
    } # yapf: disable


### FIXME(skullydazed/anyone): Need to write a test for update_kb_redis.parse_config_h()


def test_0019_parse_config_h_file_cluecard():
    """Make sure the config.h parsing works.
    """
    config_h = update_kb_redis.parse_config_h_file('qmk_firmware/keyboards/clueboard/card/config.h')
    assert config_h == {
        'VENDOR_ID': '0xC1ED',
        'PRODUCT_ID': '0x2330',
        'DEVICE_VER': '0x0001',
        'MANUFACTURER': 'Clueboard',
        'PRODUCT': 'ATMEGA32U4 Firmware Dev Kit',
        'DESCRIPTION': 'A small board to help you hack on QMK.',
        'MATRIX_ROWS': '4',
        'MATRIX_COLS': '3',
        'MATRIX_ROW_PINS': '{ F0, F5, F4, B4 }',
        'MATRIX_COL_PINS': '{ F1, F7, F6 }',
        'UNUSED_PINS': True,
        'DIODE_DIRECTION': 'ROW2COL',
        'DEBOUNCE': '20',
        'BACKLIGHT_LEVELS': '6',
        'RGB_DI_PIN': 'E6',
        'RGBLED_NUM': '4',
        'RGBLIGHT_HUE_STEP': '10',
        'RGBLIGHT_SAT_STEP': '17',
        'RGBLIGHT_VAL_STEP': '17',
    }


## FIXME(skullydazed/anyone): Write a test for update_kb_redis.parse_rules_mk


def test_0021_parse_rules_mk_file_cluecard():
    """Make sure the rules.mk parsing works.
    """
    rules_mk = update_kb_redis.parse_rules_mk_file('qmk_firmware/keyboards/clueboard/card/rules.mk')
    assert rules_mk == {
        'ARCH': 'AVR8',
        'AUDIO_ENABLE': 'yes',
        'BACKLIGHT_ENABLE': 'yes',
        'BLUETOOTH_ENABLE': 'no',
        'BOOTMAGIC_ENABLE': 'no',
        'COMMAND_ENABLE': 'yes',
        'CONSOLE_ENABLE': 'yes',
        'EXTRAKEY_ENABLE': 'yes',
        'F_CPU': '16000000',
        'F_USB': '$(F_CPU)',
        'LINK_TIME_OPTIMIZATION_ENABLE': 'yes',
        'MCU': 'atmega32u4',
        'MIDI_ENABLE': 'no',
        'MOUSEKEY_ENABLE': 'yes',
        'NKRO_ENABLE': 'no',
        'OPT_DEFS': '-DINTERRUPT_CONTROL_ENDPOINT -DBOOTLOADER_SIZE=4096',
        'RGBLIGHT_ENABLE': 'yes',
        'UNICODE_ENABLE': 'no',
    }


def test_0022_default_key():
    """Test update_kb_redis.default_key().
    """
    new_key1 = update_kb_redis.default_key()
    new_key2 = update_kb_redis.default_key()
    assert new_key1 == {'x': 3, 'y': 4, 'w': 1}
    assert new_key2 == {'x': 4, 'y': 4, 'w': 1}


def test_0022_default_key_label():
    """Test update_kb_redis.default_key('label').
    """
    new_key1 = update_kb_redis.default_key('label')
    new_key2 = update_kb_redis.default_key('label')
    assert new_key1 == {'label': 'label', 'x': 5, 'y': 4, 'w': 1}
    assert new_key2 == {'label': 'label', 'x': 6, 'y': 4, 'w': 1}


def test_0023_preprocess_source_cluecard():
    """Test the clang preprocessor function.
    """
    keymap_text = update_kb_redis.preprocess_source('qmk_firmware/keyboards/clueboard/card/keymaps/default/keymap.c')
    assert 'constuint16_tPROGMEMkeymaps' in keymap_text
    assert 'RGB_TOG,RGB_SAI,RGB_VAI,RGB_HUD,RGB_HUI,RGB_MOD,RGB_SAD,RGB_VAD,BL_STEP' in keymap_text


def test_0024_populate_enums_planck():
    """Test the enum extraction code.
    """
    keymap_file = 'qmk_firmware/keyboards/planck/keymaps/default/keymap.c'
    keymap_text = update_kb_redis.preprocess_source(keymap_file)
    keymap = update_kb_redis.extract_layouts(keymap_text, keymap_file)
    keymap_enums = update_kb_redis.populate_enums(keymap_text, keymap)
    assert '[0]=LAYOUT_planck_grid' in keymap_enums
    assert '[1]=LAYOUT_planck_grid' in keymap_enums
    assert '[2]=LAYOUT_planck_grid' in keymap_enums
    assert '[3]=LAYOUT_planck_grid' in keymap_enums
    assert '[4]=LAYOUT_planck_grid' in keymap_enums
    assert '[5]=LAYOUT_planck_grid' in keymap_enums
    assert '[6]=LAYOUT_planck_grid' in keymap_enums



def test_0025_extract_layouts_cluecard():
    """Test our layout extraction code.
    """
    keymap_file = 'qmk_firmware/keyboards/clueboard/card/keymaps/default/keymap.c'
    keymap_text = update_kb_redis.preprocess_source(keymap_file)
    layouts = update_kb_redis.extract_layouts(keymap_text, keymap_file)
    assert layouts == 'constuint16_tPROGMEMkeymaps[][MATRIX_ROWS][MATRIX_COLS]={[0]=LAYOUT(RGB_TOG,RGB_SAI,RGB_VAI,RGB_HUD,RGB_HUI,RGB_MOD,RGB_SAD,RGB_VAD,BL_STEP,F(0),F(1),F(2))}'


def test_0026_extract_keymap_cluecard():
    """Test our keymap extraction code.
    """
    keymap_file = 'qmk_firmware/keyboards/clueboard/card/keymaps/default/keymap.c'
    layout_macro, layer_list = update_kb_redis.extract_keymap(keymap_file)
    assert layout_macro == 'LAYOUT'
    assert layer_list == [['RGB_TOG', 'RGB_SAI', 'RGB_VAI', 'RGB_HUD', 'RGB_HUI', 'RGB_MOD', 'RGB_SAD', 'RGB_VAD', 'BL_STEP', 'F(0)', 'F(1)', 'F(2)']]


def test_0027_find_layouts_cluecard():
    """Test our layout detection code.
    """
    keymap_file = 'qmk_firmware/keyboards/clueboard/card/card.h'
    layouts = update_kb_redis.find_layouts(keymap_file)
    assert layouts == {
        'LAYOUT': {
            'key_count': 12,
            'layout': [
                {'x': 0, 'y': 0, 'w': 1, 'label': 'k00'}, {'x': 1, 'y': 0, 'w': 1, 'label': 'k01'},
                {'x': 2, 'y': 0, 'w': 1, 'label': 'k02'}, {'x': 0, 'y': 1, 'w': 1, 'label': 'k10'},
                {'x': 1, 'y': 1, 'w': 1, 'label': 'k12'}, {'x': 0, 'y': 2, 'w': 1, 'label': 'k20'},
                {'x': 1, 'y': 2, 'w': 1, 'label': 'k21'}, {'x': 2, 'y': 2, 'w': 1, 'label': 'k22'},
                {'x': 0, 'y': 3, 'w': 1, 'label': 'k11'}, {'x': 0, 'y': 4, 'w': 1, 'label': 'k30'},
                {'x': 1, 'y': 4, 'w': 1, 'label': 'k31'}, {'x': 2, 'y': 4, 'w': 1, 'label': 'k32'}
            ]
        }
    }  # yapf: disable


def test_0028_find_info_json_clueboard_66_rev3():
    """Make sure we can detect all the info.json files for cluecard.
    """
    info_json_files = update_kb_redis.find_info_json('clueboard/66/rev3')
    assert info_json_files == ['qmk_firmware/keyboards/clueboard/66/rev3/../../info.json', 'qmk_firmware/keyboards/clueboard/66/rev3/../info.json']


def test_0029_find_keymaps_cluecard():
    """Make sure our keymap iterator works.
    """
    keymap_names = ['default', 'rgb_effects']
    keymaps = [
        [['RGB_TOG', 'RGB_SAI', 'RGB_VAI', 'RGB_HUD', 'RGB_HUI', 'RGB_MOD', 'RGB_SAD', 'RGB_VAD', 'BL_STEP', 'F(0)',
          'SONG_SC', 'SONG_GB']],
        [['RGB_TOG', 'RGB_SAI', 'RGB_VAI', 'RGB_HUD', 'RGB_HUI', 'RGB_MOD', 'RGB_SAD', 'RGB_VAD', 'BL_STEP', 'KC_NO',
          'KC_NO', 'KC_NO']]
    ]  # yapf: disable
    for keymap_name, keymap_path, keymap_macro, keymap in update_kb_redis.find_keymaps('clueboard/card'):
        assert keymap_name == keymap_names.pop(0)
        assert keymap_path == 'qmk_firmware/keyboards/clueboard/card/keymaps'
        assert keymap_macro == 'LAYOUT'
        assert keymap == keymaps.pop(0)


def test_0030_merge_info_json_cluecard():
    """Test our code for merging an info.json into the existing keyboard info.
    """
    keyboard_info = {
        'keyboard_name': 'clueboard/card',
        'keyboard_folder': 'clueboard/card',
        'maintainer': 'qmk',
    }
    merged_keyboard_info = update_kb_redis.merge_info_json('qmk_firmware/keyboards/clueboard/info.json', keyboard_info)
    assert merged_keyboard_info == {'keyboard_name': 'clueboard/card', 'keyboard_folder': 'clueboard/card', 'maintainer': 'skullydazed', 'manufacturer': 'Clueboard'}


def test_0031_find_readme_cluecard():
    """Make sure we can find a readme.md file.
    """
    readme = update_kb_redis.find_readme('qmk_firmware/keyboards/clueboard/card')
    assert readme == 'qmk_firmware/keyboards/clueboard/card/readme.md'


############################################################################
# Clean Up Environment                                                     #
############################################################################


def test_9999_teardown():
    shutil.rmtree('qmk_firmware')
    assert not os.path.exists('qmk_firmware')
