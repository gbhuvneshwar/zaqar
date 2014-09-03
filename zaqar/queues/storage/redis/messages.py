# Copyright (c) 2014 Prashanth Raghu.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import functools
import uuid

import redis

from zaqar.common import decorators
from zaqar.openstack.common import strutils
from zaqar.openstack.common import timeutils
from zaqar.queues import storage
from zaqar.queues.storage import errors
from zaqar.queues.storage.redis import models
from zaqar.queues.storage.redis import utils

Message = models.Message


MESSAGE_IDS_SUFFIX = 'messages'
# The rank counter is an atomic index to rank messages
# in a FIFO manner.
MESSAGE_RANK_COUNTER_SUFFIX = 'rank_counter'

# NOTE(kgriffs): This value, in seconds, should be at least less than the
# minimum allowed TTL for messages (60 seconds).
RETRY_POST_TIMEOUT = 10


class MessageController(storage.Message):
    """Implements message resource operations using Redis.

    Messages are scoped by project + queue.

    Redis Data Structures:
    ----------------------
    1. Message id's list (Redis sorted set)

    Each queue in the system has a set of message ids currently
    in the queue. The list is sorted based on a ranking which is
    incremented atomically using the counter(MESSAGE_RANK_COUNTER_SUFFIX)
    also stored in the database for every queue.

    Key: <project-id.q-name>

    2. Messages(Redis Hash):

    Scoped by the UUID of the message, the redis datastructure
    has the following information.


        Name                    Field
        -----------------------------
        id                ->     id
        ttl               ->     t
        expires           ->     e
        body              ->     b
        claim             ->     c
        claim expiry time ->     c.e
        client uuid       ->     u
        created time      ->     cr
    """

    def __init__(self, *args, **kwargs):
        super(MessageController, self).__init__(*args, **kwargs)
        self._client = self.driver.connection

    @decorators.lazy_property(write=False)
    def _queue_ctrl(self):
        return self.driver.queue_controller

    @decorators.lazy_property(write=False)
    def _claim_ctrl(self):
        return self.driver.claim_controller

    def _active(self, queue_name, marker=None, echo=False,
                client_uuid=None, project=None,
                limit=None):
        return self._list(queue_name, project=project, marker=marker,
                          echo=echo, client_uuid=client_uuid,
                          include_claimed=False,
                          limit=limit, to_basic=False)

    def _get_count(self, msgset_key):
        """Get num messages in a Queue.

        Return the number of messages in a queue scoped by
        queue and project.
        """
        return self._client.zcard(msgset_key)

    @utils.raises_conn_error
    @utils.retries_on_connection_error
    def _delete_queue_messages(self, queue, project, pipe):
        """Method to remove all the messages belonging to a queue.

        Will be referenced from the QueueController.
        The pipe to execute deletion will be passed from the QueueController
        executing the operation.
        """
        client = self._client
        msgset_key = utils.scope_message_ids_set(queue, project,
                                                 MESSAGE_IDS_SUFFIX)
        message_ids = client.zrange(msgset_key, 0, -1)

        pipe.delete(msgset_key)
        for msg_id in message_ids:
            pipe.delete(msg_id)

    # TODO(prashanthr_): Look for better ways to solve the issue.
    def _find_first_unclaimed(self, queue, project, limit):
        """Find the first unclaimed message in the queue."""

        msgset_key = utils.scope_message_ids_set(queue, project,
                                                 MESSAGE_IDS_SUFFIX)
        now = timeutils.utcnow_ts()

        # TODO(kgriffs): Generalize this paging pattern (DRY)
        offset = 0

        while True:
            msg_keys = self._client.zrange(msgset_key, offset,
                                           offset + limit - 1)
            if not msg_keys:
                return None

            offset += len(msg_keys)

            messages = [Message.from_redis(self._client.hgetall(msg_key))
                        for msg_key in msg_keys]

            for msg in messages:
                if not utils.msg_claimed_filter(msg, now):
                    return msg.id

    def _exists(self, key):
        """Check if message exists in the Queue.

        Helper function which checks if a particular message_id
        exists in the sorted set of the queues message ids.
        """
        return self._client.exists(key)

    def _get_first_message_id(self, queue, project, sort):
        """Fetch head/tail of the Queue.

        Helper function to get the first message in the queue
        sort > 0 get from the left else from the right.
        """
        msgset_key = utils.scope_message_ids_set(queue, project,
                                                 MESSAGE_IDS_SUFFIX)

        sorter = self._client.zrange if sort == 1 else self._client.zrevrange
        message_ids = sorter(msgset_key, 0, 0)
        return message_ids[0] if message_ids else None

    def _get(self, message_id):
        msg = self._client.hgetall(message_id)
        return Message.from_redis(msg) if msg else None

    def _get_claim(self, message_id):
        claim = self._client.hmget(message_id, 'c', 'c.e')

        if claim == [None, None]:
            # NOTE(kgriffs): message_id was not found
            return None

        return {
            # NOTE(kgriffs): A "None" claim is serialized as an empty str
            'id': strutils.safe_decode(claim[0]) or None,
            'expires': int(claim[1]),
        }

    def _list(self, queue, project=None, marker=None,
              limit=storage.DEFAULT_MESSAGES_PER_PAGE,
              echo=False, client_uuid=None,
              include_claimed=False, to_basic=True):

        if not self._queue_ctrl.exists(queue, project):
            raise errors.QueueDoesNotExist(queue,
                                           project)

        msgset_key = utils.scope_message_ids_set(queue,
                                                 project,
                                                 MESSAGE_IDS_SUFFIX)
        client = self._client

        with self._client.pipeline() as pipe:
            if not marker and not include_claimed:
                # NOTE(kgriffs): Skip unclaimed messages at the head
                # of the queue; otherwise we would just filter them all
                # out and likely end up with an empty list to return.
                marker = self._find_first_unclaimed(queue, project, limit)
                start = client.zrank(msgset_key, marker) or 0
            else:
                rank = client.zrank(msgset_key, marker)
                start = rank + 1 if rank else 0

            message_ids = client.zrange(msgset_key, start,
                                        start + (limit - 1))

            for msg_id in message_ids:
                pipe.hgetall(msg_id)

            messages = pipe.execute()

            # NOTE(prashanthr_): Build a list of filters for checking
            # the following:
            #
            #     1. Message is expired
            #     2. Message is claimed
            #     3. Message should not be echoed
            #
            now = timeutils.utcnow_ts()
            filters = [functools.partial(utils.msg_expired_filter, now=now)]

            if not include_claimed:
                filters.append(functools.partial(utils.msg_claimed_filter,
                                                 now=now))

            if not echo:
                filters.append(functools.partial(utils.msg_echo_filter,
                                                 client_uuid=client_uuid))

            marker = {}

            yield _filter_messages(messages, pipe, filters, to_basic, marker)
            yield marker['next']

    @utils.raises_conn_error
    @utils.retries_on_connection_error
    def list(self, queue, project=None, marker=None,
             limit=storage.DEFAULT_MESSAGES_PER_PAGE,
             echo=False, client_uuid=None,
             include_claimed=False):

        return self._list(queue, project, marker, limit, echo,
                          client_uuid, include_claimed)

    @utils.raises_conn_error
    @utils.retries_on_connection_error
    def first(self, queue, project=None, sort=1):
        if sort not in (1, -1):
            raise ValueError(u'sort must be either 1 (ascending) '
                             u'or -1 (descending)')

        message_id = self._get_first_message_id(queue, project, sort)
        if not message_id:
            raise errors.QueueIsEmpty(queue, project)

        now = timeutils.utcnow_ts()
        return self._get(message_id).to_basic(now, include_created=True)

    @utils.raises_conn_error
    @utils.retries_on_connection_error
    def get(self, queue, message_id, project=None):
        if not self._queue_ctrl.exists(queue, project):
            raise errors.QueueDoesNotExist(queue, project)

        message = self._get(message_id)
        now = timeutils.utcnow_ts()

        if message and not utils.msg_expired_filter(message, now):
            return message.to_basic(now)
        else:
            raise errors.MessageDoesNotExist(message_id, queue, project)

    @utils.raises_conn_error
    @utils.retries_on_connection_error
    def bulk_get(self, queue, message_ids, project=None):
        if not self._queue_ctrl.exists(queue, project):
            raise errors.QueueDoesNotExist(queue, project)

        # NOTE(prashanthr_): Pipelining is used here purely
        # for performance.
        with self._client.pipeline() as pipe:
            for mid in message_ids:
                    pipe.hgetall(mid)

            messages = pipe.execute()

        # NOTE(kgriffs): Skip messages that may have been deleted
        now = timeutils.utcnow_ts()
        return (Message.from_redis(msg).to_basic(now)
                for msg in messages if msg)

    @utils.raises_conn_error
    @utils.retries_on_connection_error
    def post(self, queue, messages, client_uuid, project=None):
        if not self._queue_ctrl.exists(queue, project):
            raise errors.QueueDoesNotExist(queue, project)

        msgset_key = utils.scope_message_ids_set(queue, project,
                                                 MESSAGE_IDS_SUFFIX)

        counter_key = utils.scope_queue_index(queue, project,
                                              MESSAGE_RANK_COUNTER_SUFFIX)

        with self._client.pipeline() as pipe:

            start_ts = timeutils.utcnow_ts()

            # NOTE(kgriffs): Retry the operation if another transaction
            # completes before this one, in which case it may have
            # posted messages with the same rank counter the current
            # thread is trying to use, which would cause messages
            # to get out of order and introduce the risk of a client
            # missing a message while reading from the queue.
            #
            # This loop will eventually time out if we can't manage to
            # post any messages due to other threads continually beating
            # us to the punch.

            # TODO(kgriffs): Would it be beneficial (or harmful) to
            # introducce a backoff sleep in between retries?
            while (timeutils.utcnow_ts() - start_ts) < RETRY_POST_TIMEOUT:
                now = timeutils.utcnow_ts()
                prepared_messages = [
                    Message(
                        ttl=msg['ttl'],
                        created=now,
                        client_uuid=client_uuid,
                        claim_id=None,
                        claim_expires=now,
                        body=msg.get('body', {}),
                    )

                    for msg in messages
                ]

                try:
                    pipe.watch(counter_key)

                    rank_counter = pipe.get(counter_key)
                    rank_counter = int(rank_counter) if rank_counter else 0

                    pipe.multi()

                    keys = []
                    for i, msg in enumerate(prepared_messages):
                        msg.to_redis(pipe)
                        pipe.zadd(msgset_key, rank_counter + i, msg.id)
                        keys.append(msg.id)

                    pipe.incrby(counter_key, len(keys))
                    self._queue_ctrl._inc_counter(queue, project,
                                                  len(prepared_messages),
                                                  pipe=pipe)

                    pipe.execute()
                    return keys

                except redis.exceptions.WatchError:
                    continue

            raise errors.MessageConflict(queue, project)

    @utils.raises_conn_error
    @utils.retries_on_connection_error
    def delete(self, queue, message_id, project=None, claim=None):
        if not self._queue_ctrl.exists(queue, project):
            raise errors.QueueDoesNotExist(queue, project)

        # TODO(kgriffs): Create decorator for validating claim and message
        # IDs, since those are not checked at the transport layer. This
        # decorator should be applied to all relevant methods.
        if claim is not None:
            try:
                uuid.UUID(claim)
            except ValueError:
                raise errors.ClaimDoesNotExist(queue, project, claim)

        msg_claim = self._get_claim(message_id)

        # NOTE(kgriffs): The message does not exist, so
        # it is essentially "already deleted".
        if msg_claim is None:
            return

        now = timeutils.utcnow_ts()
        is_claimed = msg_claim['id'] and (now < msg_claim['expires'])

        if claim is None:
            if is_claimed:
                raise errors.MessageIsClaimed(message_id)

        elif not is_claimed:
            raise errors.MessageNotClaimed(message_id)

        elif msg_claim['id'] != claim:
            if not self._claim_ctrl._exists(queue, claim, project):
                raise errors.ClaimDoesNotExist(queue, project, claim)

            raise errors.MessageNotClaimedBy(message_id, claim)

        msgset_key = utils.scope_message_ids_set(queue, project,
                                                 MESSAGE_IDS_SUFFIX)
        with self._client.pipeline() as pipe:
            results = pipe.delete(message_id).zrem(msgset_key,
                                                   message_id).execute()

        # NOTE(prashanthr_): results[0] is 1 when the delete is
        # successful. Hence we use that case to identify successful
        # deletes.
        if results[0] == 1:
            self._queue_ctrl._inc_counter(queue, project, -1)

    @utils.raises_conn_error
    @utils.retries_on_connection_error
    def bulk_delete(self, queue, message_ids, project=None):
        if not self._queue_ctrl.exists(queue, project):
            raise errors.QueueDoesNotExist(queue,
                                           project)

        msgset_key = utils.scope_message_ids_set(queue, project,
                                                 MESSAGE_IDS_SUFFIX)

        with self._client.pipeline() as pipe:
            for message_id in message_ids:
                pipe.delete(message_id).zrem(msgset_key, message_id)

            results = pipe.execute()

        # NOTE(prashanthr_): None is returned for the cases where
        # the message might not exist or has been deleted/expired.
        # Hence we calculate the number of deletes as the
        # total number of message ids - number of failed deletes.
        amount = -1 * (len(results) - results.count(0)) / 2
        self._queue_ctrl._inc_counter(queue, project, int(amount))

    @utils.raises_conn_error
    @utils.retries_on_connection_error
    def pop(self, queue, limit, project=None):
        # Pop is implemented as a chain of the following operations:
        # 1. Create a claim.
        # 2. Delete the messages claimed.
        # 3. Delete the claim.

        claim_id, messages = self._claim_ctrl.create(
            queue, dict(ttl=1, grace=0), project, limit=limit)

        message_ids = [message['id'] for message in messages]
        self.bulk_delete(queue, message_ids, project)
        # NOTE(prashanthr_): Creating a claim controller reference
        # causes a recursive reference. Hence, using the reference
        # from the driver.
        self._claim_ctrl.delete(queue, claim_id, project)
        return messages


def _filter_messages(messages, pipe, filters, to_basic, marker):
    """Create a filtering iterator over a list of messages.

    The function accepts a list of filters to be filtered
    before the the message can be included as a part of the reply.
    """
    now = timeutils.utcnow_ts()

    for msg in messages:
        # NOTE(kgriffs): Message may have been deleted, so
        # check each value to ensure we got a message back
        if msg:
            msg = Message.from_redis(msg)

            # NOTE(kgriffs): Check to see if any of the filters
            # indiciate that this message should be skipped.
            for should_skip in filters:
                if should_skip(msg):
                    break
            else:
                marker['next'] = msg.id

                if to_basic:
                    yield msg.to_basic(now)
                else:
                    yield msg