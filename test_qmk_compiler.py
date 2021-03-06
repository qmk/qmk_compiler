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

import qmk_commands

############################################################################
# Setup Environment                                                        #
############################################################################


def test_0000_checkout_qmk_master():
    """Make sure that we successfully git clone qmk_firmware and generate the version.txt hash.
    """
    qmk_commands.checkout_qmk(branch='master')
    assert os.path.exists('qmk_firmware/version.txt')


############################################################################
# Begin Tests                                                              #
############################################################################


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


############################################################################
# Clean Up Environment                                                     #
############################################################################


def test_9999_teardown():
    shutil.rmtree('qmk_firmware')
    assert not os.path.exists('qmk_firmware')
