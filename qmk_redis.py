import json
from os import environ

from redis import Redis
from rq import Queue

# Configuration
REDIS_HOST = environ.get('REDIS_HOST', 'redis.qmk-api')
REDIS_TIMEOUT = int(environ.get('REDIS_TIMEOUT', 180))

# Objects we need to instaniate
redis = Redis(REDIS_HOST)
rq = Queue(connection=redis)


def enqueue(func, timeout=REDIS_TIMEOUT, *args, **kwargs):
    """Insert a job into RQ.
    """
    return rq.enqueue(func, timeout=timeout, *args, **kwargs)


def get(key):
    """Fetches a JSON serialized object from redis.
    """
    data = redis.get(key)
    if data:
        return json.loads(data.decode('utf-8'))


def set(key, value):
    """Writes a JSON serialized object to redis.
    """
    return redis.set(key, json.dumps(value))
