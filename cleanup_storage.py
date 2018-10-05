import logging
from datetime import datetime, timedelta
from os import environ

from pytz import utc
from rq.decorators import job

from qmk_redis import redis
from qmk_storage import list_objects, delete

# Configuration
STORAGE_TIME_HOURS = int(environ.get('S3_STORAGE_TIME', 24))


@job('default', connection=redis)
def cleanup_storage():
    storage_time = timedelta(hours=STORAGE_TIME_HOURS)
    now = datetime.now(utc)
    files = list_objects()

    if files:
        i = 0
        for file in files:
            file_age = now-file['LastModified']
            if 'qmk_api_tasks_test_compile' in file['Key'] or file_age > storage_time:
                i += 1
                print('Deleting #%s: %s (Age: %s)' % (i, file['Key'], file_age))
                delete(file['Key'])

    return True


if __name__ == '__main__':
    cleanup_storage()
