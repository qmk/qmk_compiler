import json
from os import environ

from redis import Redis

# Configuration
REDIS_HOST = environ.get('REDIS_HOST', 'redis.qmk-api')

# Objects we need to instaniate
redis = Redis(REDIS_HOST)


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
