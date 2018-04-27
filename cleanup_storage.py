import logging
from datetime import datetime, timedelta
from os import environ

from pytz import utc
from rq.decorators import job

from qmk_redis import redis
from qmk_storage import list_objects, delete

# Configuration
STORAGE_TIME_HOURS = environ.get('S3_STORAGE_TIME', '48')


@job('default', connection=redis)
def cleanup_storage():
    storage_time = timedelta(hours=STORAGE_TIME_HOURS)
    now = datetime.now(utc)
    files = list_objects()

    if files:
        for file in files['Contents']:
            if now - file['LastModified'] > storage_time:
                logging.info('Removing %s', file['Key'])
                delete(file['Key'])


if __name__ == '__main__':
    cleanup_storage()
