"""Job broker for Business query monitors (spec section 5).

Only the central API/scheduler talk to RabbitMQ; the Business Worker never sees it. Messages are small
(identifiers + validity) and carry no SQL or connection data. Delivery is at-least-once: the database
(`cm_business_executions`) is the idempotency/lease/audit record and tolerates duplicates and redeliveries.

Without RABBITMQ_URL the platform runs in "db" mode (the claim reads the executions table directly), which keeps
development and tests simple. With it, the scheduler writes a transactional outbox row, the publisher confirms the
message into the worker's queue, and the claim consumes from that queue.
"""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Protocol

from .settings import get_settings

logger = logging.getLogger(__name__)
EXCHANGE = "business.jobs"
DLX = "business.jobs.dlx"
DLQ = "business.jobs.dlq"


def queue_name(worker_id: Any) -> str:
    return f"business.jobs.w.{worker_id}"


def routing_key(worker_id: Any) -> str:
    return f"w.{worker_id}"


class BrokerUnavailable(RuntimeError):
    """The broker could not be reached or did not confirm the operation."""


@dataclass
class Delivery:
    body: dict[str, Any]
    _ack: Callable[[], None]
    _nack: Callable[[], None]
    settled: bool = field(default=False, init=False)

    def ack(self) -> None:
        if not self.settled:
            self.settled = True
            self._ack()

    def reject_to_dlq(self) -> None:
        if not self.settled:
            self.settled = True
            self._nack()


class JobBroker(Protocol):
    def publish(self, message: dict[str, Any], worker_id: Any, ttl_ms: int) -> None: ...
    def consume(self, worker_id: Any, limit: int) -> Any: ...  # context manager yielding list[Delivery]
    def stats(self, worker_ids: list[Any]) -> dict[str, Any]: ...
    def peek_dlq(self, limit: int) -> list[dict[str, Any]]: ...
    def discard_dlq(self, tenant_id: str, execution_id: str) -> int: ...  # removes that execution's dead letters, returns how many


class RabbitJobBroker:
    """RabbitMQ implementation (pika, blocking). One short-lived connection per operation: the volume is a
    handful of messages per worker per minute, and a stateless gateway cannot hold channels across HTTP calls."""

    def __init__(self, url: str, queue_type: str = "classic") -> None:
        self.url = url
        self.queue_args = {"x-dead-letter-exchange": DLX}
        if queue_type == "quorum":
            self.queue_args["x-queue-type"] = "quorum"

    @contextmanager
    def _channel(self) -> Iterator[Any]:
        import pika
        from pika.exceptions import AMQPError
        connection = None
        try:
            params = pika.URLParameters(self.url)
            params.socket_timeout = 5
            params.blocked_connection_timeout = 10
            connection = pika.BlockingConnection(params)
            channel = connection.channel()
            channel.exchange_declare(EXCHANGE, exchange_type="direct", durable=True)
            channel.exchange_declare(DLX, exchange_type="fanout", durable=True)
            channel.queue_declare(DLQ, durable=True)
            channel.queue_bind(DLQ, DLX)
            yield channel
        except AMQPError as error:
            raise BrokerUnavailable(type(error).__name__) from error
        finally:
            if connection is not None and connection.is_open:
                try:
                    connection.close()
                except Exception:  # closing must never mask the real outcome
                    pass

    def _declare_worker_queue(self, channel: Any, worker_id: Any) -> str:
        name = queue_name(worker_id)
        channel.queue_declare(name, durable=True, arguments=self.queue_args)
        channel.queue_bind(name, EXCHANGE, routing_key(worker_id))
        return name

    def publish(self, message: dict[str, Any], worker_id: Any, ttl_ms: int) -> None:
        import pika
        from pika.exceptions import AMQPError, UnroutableError
        with self._channel() as channel:
            self._declare_worker_queue(channel, worker_id)
            channel.confirm_delivery()  # the outbox row is only completed after the broker confirmed
            try:
                channel.basic_publish(
                    EXCHANGE, routing_key(worker_id), json.dumps(message, separators=(",", ":")).encode(),
                    pika.BasicProperties(delivery_mode=2, content_type="application/json", message_id=str(message["execution_id"]),
                                         correlation_id=str(message.get("correlation_id", "")), expiration=str(max(1000, ttl_ms))),
                    mandatory=True)
            except (UnroutableError, AMQPError) as error:
                raise BrokerUnavailable(type(error).__name__) from error

    @contextmanager
    def consume(self, worker_id: Any, limit: int) -> Iterator[list[Delivery]]:
        deliveries: list[Delivery] = []
        with self._channel() as channel:
            name = self._declare_worker_queue(channel, worker_id)
            for _ in range(limit):
                method, _props, body = channel.basic_get(name, auto_ack=False)
                if method is None:
                    break
                tag = method.delivery_tag
                try:
                    parsed = json.loads(body)
                    if not isinstance(parsed, dict):
                        raise ValueError("not an object")
                except ValueError:
                    channel.basic_nack(tag, requeue=False)  # unreadable: straight to the dead-letter queue
                    continue
                deliveries.append(Delivery(parsed, (lambda t=tag: channel.basic_ack(t)), (lambda t=tag: channel.basic_nack(t, requeue=False))))
            yield deliveries
            # Deliveries the caller did not settle are requeued by the broker when this connection closes.

    def stats(self, worker_ids: list[Any]) -> dict[str, Any]:
        with self._channel() as channel:
            queues = {}
            for worker_id in worker_ids:
                name = self._declare_worker_queue(channel, worker_id)
                queues[str(worker_id)] = channel.queue_declare(name, passive=True).method.message_count
            return {"mode": "rabbitmq", "queues": queues, "dlq_depth": channel.queue_declare(DLQ, passive=True).method.message_count}

    def peek_dlq(self, limit: int) -> list[dict[str, Any]]:
        out = []
        with self._channel() as channel:
            tags = []
            for _ in range(limit):
                method, props, body = channel.basic_get(DLQ, auto_ack=False)
                if method is None:
                    break
                tags.append(method.delivery_tag)
                deaths = (props.headers or {}).get("x-death") or [{}]
                try:
                    payload = json.loads(body)
                except ValueError:
                    payload = {}
                out.append({"execution_id": payload.get("execution_id"), "tenant_id": payload.get("tenant_id"),
                            "reason": deaths[0].get("reason"), "queue": deaths[0].get("queue")})
            for tag in tags:
                channel.basic_nack(tag, requeue=True)  # peek only: messages stay in the DLQ
        return out

    def discard_dlq(self, tenant_id: str, execution_id: str) -> int:
        """Remove this execution's dead letters (after a reprocess or an explicit discard). Others stay untouched."""
        removed = 0
        with self._channel() as channel:
            keep = []
            for _ in range(500):
                method, _props, body = channel.basic_get(DLQ, auto_ack=False)
                if method is None:
                    break
                try:
                    payload = json.loads(body)
                except ValueError:
                    payload = {}
                if str(payload.get("tenant_id")) == str(tenant_id) and str(payload.get("execution_id")) == str(execution_id):
                    channel.basic_ack(method.delivery_tag)
                    removed += 1
                else:
                    keep.append(method.delivery_tag)
            for tag in keep:
                channel.basic_nack(tag, requeue=True)
        return removed


class InMemoryJobBroker:
    """Test double with the same contract (ordered per-worker queues, DLQ, confirmed publish)."""

    def __init__(self) -> None:
        self.queues: dict[str, list[dict[str, Any]]] = {}
        self.dlq: list[dict[str, Any]] = []
        self.fail_publish = False

    def publish(self, message: dict[str, Any], worker_id: Any, ttl_ms: int) -> None:
        if self.fail_publish:
            raise BrokerUnavailable("simulated outage")
        self.queues.setdefault(str(worker_id), []).append(json.loads(json.dumps(message)))

    @contextmanager
    def consume(self, worker_id: Any, limit: int) -> Iterator[list[Delivery]]:
        queue = self.queues.setdefault(str(worker_id), [])
        taken = [queue.pop(0) for _ in range(min(limit, len(queue)))]
        deliveries = []
        for body in taken:
            deliveries.append(Delivery(body, (lambda: None), (lambda b=body: self.dlq.append(b))))
        yield deliveries
        for body, delivery in zip(taken, deliveries):
            if not delivery.settled:
                queue.insert(0, body)  # unsettled = broker redelivers

    def stats(self, worker_ids: list[Any]) -> dict[str, Any]:
        return {"mode": "memory", "queues": {str(w): len(self.queues.get(str(w), [])) for w in worker_ids}, "dlq_depth": len(self.dlq)}

    def peek_dlq(self, limit: int) -> list[dict[str, Any]]:
        return [{"execution_id": b.get("execution_id"), "tenant_id": b.get("tenant_id"), "reason": "rejected", "queue": None} for b in self.dlq[:limit]]


    def discard_dlq(self, tenant_id: str, execution_id: str) -> int:
        before = len(self.dlq)
        self.dlq = [b for b in self.dlq if not (str(b.get("tenant_id")) == str(tenant_id) and str(b.get("execution_id")) == str(execution_id))]
        return before - len(self.dlq)


_override: JobBroker | None = None


def set_broker_for_tests(broker: JobBroker | None) -> None:
    global _override
    _override = broker


def get_broker() -> JobBroker | None:
    """None means 'db' mode (no RabbitMQ configured)."""
    if _override is not None:
        return _override
    settings = get_settings()
    if not settings.rabbitmq_url:
        return None
    return RabbitJobBroker(settings.rabbitmq_url, settings.rabbitmq_queue_type)
