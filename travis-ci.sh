#!/bin/sh

set -x

pip3 install -r requirements.txt
nose2
