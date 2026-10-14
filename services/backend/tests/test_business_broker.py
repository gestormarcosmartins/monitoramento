import json
import unittest
from unittest import mock

try:
    from central_monitor import business_broker as bb
except ImportError:  # backend dependencies are not installed
    bb = None

WID = "11111111-2222-3333-4444-555555555555"
MESSAGE = {"execution_id": "e1", "tenant_id": "t1", "worker_id": WID, "correlation_id": "c1"}


class FakeChannel:
    """Just enough of pika's BlockingChannel to check topology, confirms and ack/nack behaviour."""

    def __init__(self, queued=None):
        self.queued = list(queued or [])
        self.calls = []
        self.published = []

    def exchange_declare(self, name, **kw): self.calls.append(("exchange_declare", name, kw.get("exchange_type")))
    def queue_declare(self, name, **kw):
        self.calls.append(("queue_declare", name, kw))
        return mock.Mock(method=mock.Mock(message_count=len(self.queued)))
    def queue_bind(self, queue, exchange, routing_key=""): self.calls.append(("queue_bind", queue, exchange, routing_key))
    def confirm_delivery(self): self.calls.append(("confirm_delivery",))
    def basic_publish(self, exchange, key, body, props, mandatory=False):
        self.published.append((exchange, key, json.loads(body), props, mandatory))
    def basic_get(self, queue, auto_ack=False):
        if not self.queued:
            return None, None, None
        body = self.queued.pop(0)
        return mock.Mock(delivery_tag=len(self.calls) + 100), mock.Mock(headers=None), body
    def basic_ack(self, tag): self.calls.append(("ack", tag))
    def basic_nack(self, tag, requeue=True): self.calls.append(("nack", tag, requeue))


def broker_with(channel):
    broker = bb.RabbitJobBroker("amqp://u:p@h/%2F")
    connection = mock.Mock(is_open=True)
    connection.channel.return_value = channel
    return broker, mock.patch("pika.BlockingConnection", return_value=connection)


@unittest.skipIf(bb is None, "backend dependencies not installed")
class RabbitContract(unittest.TestCase):
    def test_publish_declares_topology_confirms_and_routes_to_the_worker_queue(self):
        channel = FakeChannel()
        broker, patcher = broker_with(channel)
        with patcher:
            broker.publish(MESSAGE, WID, 90_000)
        kinds = [c[0] for c in channel.calls]
        self.assertIn("confirm_delivery", kinds)  # the outbox row is only completed after a broker confirm
        queue_declares = [c for c in channel.calls if c[0] == "queue_declare" and c[1] == bb.queue_name(WID)]
        self.assertEqual(queue_declares[0][2]["durable"], True)
        self.assertEqual(queue_declares[0][2]["arguments"]["x-dead-letter-exchange"], bb.DLX)
        exchange, key, body, props, mandatory = channel.published[0]
        self.assertEqual((exchange, key, mandatory), (bb.EXCHANGE, bb.routing_key(WID), True))
        self.assertEqual(props.delivery_mode, 2)  # persistent
        self.assertEqual(props.expiration, "90000")
        self.assertEqual(body, MESSAGE)
        self.assertNotIn("query", body)  # identifiers only: no SQL, no connection data

    def test_quorum_queue_option(self):
        broker = bb.RabbitJobBroker("amqp://x", "quorum")
        self.assertEqual(broker.queue_args["x-queue-type"], "quorum")

    def test_unroutable_or_broker_error_is_reported_as_unavailable(self):
        from pika.exceptions import UnroutableError
        channel = FakeChannel()
        channel.basic_publish = mock.Mock(side_effect=UnroutableError([]))
        broker, patcher = broker_with(channel)
        with patcher, self.assertRaises(bb.BrokerUnavailable):
            broker.publish(MESSAGE, WID, 1000)

    def test_connection_failure_is_reported_as_unavailable(self):
        from pika.exceptions import AMQPConnectionError
        broker = bb.RabbitJobBroker("amqp://u:p@h/%2F")
        with mock.patch("pika.BlockingConnection", side_effect=AMQPConnectionError("down")), self.assertRaises(bb.BrokerUnavailable):
            broker.publish(MESSAGE, WID, 1000)

    def test_consume_ack_after_caller_decides_and_unreadable_body_is_dead_lettered(self):
        channel = FakeChannel([json.dumps(MESSAGE).encode(), b"not json", json.dumps({"execution_id": "e2"}).encode()])
        broker, patcher = broker_with(channel)
        with patcher:
            with broker.consume(WID, 5) as deliveries:
                self.assertEqual([d.body["execution_id"] for d in deliveries], ["e1", "e2"])
                deliveries[0].ack()
                deliveries[1].reject_to_dlq()
                deliveries[1].reject_to_dlq()  # idempotent: a delivery is settled once
        acks = [c for c in channel.calls if c[0] == "ack"]
        nacks = [c for c in channel.calls if c[0] == "nack"]
        self.assertEqual(len(acks), 1)
        self.assertEqual([n[2] for n in nacks], [False, False])  # unreadable + rejected: requeue=False -> DLX, never a hot loop

    def test_peek_dlq_leaves_messages_in_place(self):
        channel = FakeChannel([json.dumps(MESSAGE).encode()])
        broker, patcher = broker_with(channel)
        with patcher:
            listed = broker.peek_dlq(10)
        self.assertEqual(listed[0]["execution_id"], "e1")
        self.assertTrue(any(c[0] == "nack" and c[2] is True for c in channel.calls))  # requeued: peek only

    def test_settings_choose_mode(self):
        with mock.patch.object(bb, "get_settings", return_value=mock.Mock(rabbitmq_url="", rabbitmq_queue_type="classic")):
            self.assertIsNone(bb.get_broker())  # db mode
        with mock.patch.object(bb, "get_settings", return_value=mock.Mock(rabbitmq_url="amqp://u:p@h/%2F", rabbitmq_queue_type="classic")):
            self.assertIsInstance(bb.get_broker(), bb.RabbitJobBroker)


@unittest.skipIf(bb is None, "backend dependencies not installed")
class InMemoryDouble(unittest.TestCase):
    def test_same_contract_as_the_real_broker(self):
        broker = bb.InMemoryJobBroker()
        broker.publish(MESSAGE, WID, 1000)
        with broker.consume(WID, 4) as deliveries:
            self.assertEqual(len(deliveries), 1)  # taken, not yet acked
        self.assertEqual(broker.stats([WID])["queues"][WID], 1)  # unsettled -> redelivered
        with broker.consume(WID, 4) as deliveries:
            deliveries[0].reject_to_dlq()
        self.assertEqual(broker.stats([WID])["dlq_depth"], 1)
        self.assertEqual(broker.stats([WID])["queues"][WID], 0)


if __name__ == "__main__":
    unittest.main()
