#!/usr/bin/env python
# Special thanks to Hynek Schlawack for providing excellent documentation:
#
# https://hynek.me/articles/sharing-your-labor-of-love-pypi-quick-and-dirty/
import os
from setuptools import setup


def read(*paths):
    """Build a file path from *paths* and return the contents."""
    with open(os.path.join(*paths), 'r') as f:
        return f.read()


setup(
    name='qmk_compiler_worker',
    version='0.0.1',
    description='Library for turning keyboard-layout-editor.com raw code into X/Y coordinates',
    long_description='\n\n'.join((read('README.md'), read('AUTHORS.md'))),
    url='https://github.com/qmk/qmk_compiler_worker',
    license='all_rights_reserved',
    author='skullY',
    author_email='skullydazed@gmail.com',
    install_requires=['rq'],
    py_modules=['cleanup_storage', 'qmk_compiler', 'qmk_commands', 'qmk_redis', 'qmk_storage', 'qmk_errors', 'update_kb_redis'],
    include_package_data=True,
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Intended Audience :: Developers',
        'Intended Audience :: System Administrators',
        'Natural Language :: English',
        'License :: Restrictive',
        'Topic :: System :: Systems Administration',
    ],
)
