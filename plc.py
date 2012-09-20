import redis

class PagingListStatistics(object):
    """ Handles reporting of weekling title paging list counts to redis. """

    def __init__(self, redis_host, redis_port, redis_key):
        self._redis = redis.StrictRedis(host=redis_host, port=redis_port, db=0)
        self._redis_key = redis_key


    def setBranchCount(self, branch, day, count):
        self._redis.hset(self._redis_key, "%s:%s" % (branch, day), count)

