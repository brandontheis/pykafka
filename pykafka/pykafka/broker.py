import logging

from pykafka import base
from .connection import BrokerConnection
from .handlers import RequestHandler
from .protocol import (
    FetchRequest, FetchResponse, OffsetRequest,
    OffsetResponse, MetadataRequest, MetadataResponse,
    OffsetCommitRequest, OffsetCommitResponse,
    OffsetFetchRequest, OffsetFetchResponse
)


logger = logging.getLogger(__name__)


class Broker(base.BaseBroker):

    def __init__(self,
                 id_,
                 host,
                 port,
                 handler,
                 socket_timeout_ms,
                 offsets_channel_socket_timeout_ms,
                 buffer_size=64 * 1024):
        """Init a Broker.

        :param handler: TODO: Fill in
        :type handler: TODO: Fill in
        :param timeout: TODO: Fill in
        :type timeout: :class:int
        """
        self._connected = False
        self._offsets_channel_connected = False
        self._id = int(id_)
        self._host = host
        self._port = port
        self._handler = handler
        self._req_handler = None
        self._offsets_channel_req_handler = None
        self._socket_timeout_ms = socket_timeout_ms
        self._offsets_channel_socket_timeout_ms = offsets_channel_socket_timeout_ms
        self._buffer_size = buffer_size
        self.connect()

    @classmethod
    def from_metadata(cls,
                      metadata,
                      handler,
                      socket_timeout_ms,
                      offsets_channel_socket_timeout_ms,
                      buffer_size=64 * 1024):
        """ Create a Broker using BrokerMetadata

        :param metadata: Metadata that describes the broker.
        :type metadata: :class:`kafka.pykafka.protocol.BrokerMetadata.`
        """
        return cls(metadata.id, metadata.host,
                   metadata.port, handler, socket_timeout_ms,
                   offsets_channel_socket_timeout_ms,
                   buffer_size=buffer_size)

    @property
    def connected(self):
        """Returns True if the connected to the broker."""
        return self._connected

    @property
    def offsets_channel_connected(self):
        """Returns True if the connected to the broker."""
        return self._offsets_channel_connected

    @property
    def id(self):
        """The broker's ID within the Kafka cluster."""
        return self._id

    @property
    def host(self):
        """The host where the broker is available."""
        return self._host

    @property
    def port(self):
        """The port where the broker is available."""
        return self._port

    @property
    def handler(self):
        """The primary :class:`kafka.handlers.RequestHandler` for this broker.

        This handler handles all requests outside of the commit/fetch api
        """
        return self._req_handler

    @property
    def offsets_channel_handler(self):
        """The offset channel :class:`kafka.handlers.RequestHandler` for this broker.

        This handler handles all requests that use the commit/fetch api
        """
        return self._offsets_channel_req_handler

    def connect(self):
        """Establish a connection to the Broker."""
        conn = BrokerConnection(self.host, self.port, self._buffer_size)
        conn.connect(self._socket_timeout_ms)
        self._req_handler = RequestHandler(self._handler, conn)
        self._req_handler.start()
        self._connected = True

    def connect_offsets_channel(self):
        """Establish a connection to the Broker for the offsets channel"""
        conn = BrokerConnection(self.host, self.port, self._buffer_size)
        conn.connect(self._offsets_channel_socket_timeout_ms)
        self._offsets_channel_req_handler = RequestHandler(self._handler, conn)
        self._offsets_channel_req_handler.start()
        self._offsets_channel_connected = True

    def fetch_messages(self,
                       partition_requests,
                       timeout=30000,
                       min_bytes=1):
        """Fetch messages from a set of partitions.

        :param partition_requests: Requests of messages to fetch.
        :type partition_requests: Iterable of
            :class:`kafka.pykafka.protocol.PartitionFetchRequest`
        """
        future = self._req_handler.request(FetchRequest(
            partition_requests=partition_requests,
            timeout=timeout,
            min_bytes=min_bytes,
        ))
        # XXX - this call returns even with less than min_bytes of messages?
        return future.get(FetchResponse)

    def produce_messages(self, produce_request):
        """Produce messages to a set of partitions.

        :type partition_requests: Iterable of
            :class:`kafka.pykafka.protocol.ProduceRequest`
        """
        if produce_request.required_acks == 0:
            self._req_handler.request(produce_request, has_response=False)
        else:
            self._req_handler.request(produce_request).get()
            # Any errors will be decoded and raised in the `.get()`
        return None

    def request_offset_limits(self, partition_requests):
        """Request offset information for a set of topic/partitions"""
        future = self._req_handler.request(OffsetRequest(partition_requests))
        return future.get(OffsetResponse)

    def request_metadata(self, topics=None):
        future = self._req_handler.request(MetadataRequest(topics=topics))
        return future.get(MetadataResponse)

    ######################
    #  Commit/Fetch API  #
    ######################

    def commit_consumer_group_offsets(self,
                                      consumer_group,
                                      consumer_group_generation_id,
                                      consumer_id,
                                      preqs):
        """Commit the offsets of all messages consumed

        Commit the offsets of all messages consumed so far by this consumer
            group with the Offset Commit/Fetch API

        Based on Step 2 here https://cwiki.apache.org/confluence/display/KAFKA/Committing+and+fetching+consumer+offsets+in+Kafka

        :param consumer_group: the name of the consumer group for which to
            commit offsets
        :type consumer_group: str
        :param preqs: a sequence of <protocol.PartitionOffsetCommitRequest>
        :type preqs: sequence
        """
        if not self._offsets_channel_connected:
            self.connect_offsets_channel()
        # TODO - exponential backoff
        req = OffsetCommitRequest(consumer_group,
                                  consumer_group_generation_id,
                                  consumer_id,
                                  partition_requests=preqs)
        self._offsets_channel_req_handler.request(req).get(OffsetCommitResponse)

    def fetch_consumer_group_offsets(self, consumer_group, preqs):
        """Fetch the offsets stored in Kafka with the Offset Commit/Fetch API

        Based on Step 2 here https://cwiki.apache.org/confluence/display/KAFKA/Committing+and+fetching+consumer+offsets+in+Kafka

        :param consumer_group: the name of the consumer group for which to
            commit offsets
        :type consumer_group: str
        :param preqs: a sequence of <protocol.PartitionOffsetFetchRequest>
        :type preqs: sequence
        """
        if not self._offsets_channel_connected:
            self.connect_offsets_channel()
        # TODO - exponential backoff
        req = OffsetFetchRequest(consumer_group, partition_requests=preqs)
        return self._offsets_channel_req_handler.request(req).get(OffsetFetchResponse)
