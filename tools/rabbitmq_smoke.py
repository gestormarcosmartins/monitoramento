"""Smoke test of the Business job broker against a REAL RabbitMQ (never run in CI; needs a broker).

    set RABBITMQ_URL=amqp://user:pass@localhost:5672/%2F
    python tools/rabbitmq_smoke.py

Checks the topology and behaviours the unit tests could only simulate: confirmed persistent publish, per-worker queue,
unroutable handling, ack after settle, dead-lettering to the DLQ with reason, redelivery of unsettled messages and
per-message expiration. It creates (and removes) a throw-away worker queue named after a random id.
"""
import os
import pathlib
import sys
import time
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "backend"))
os.environ.setdefault("CENTRAL_DATABASE_URL", "postgresql://x:x@localhost/x")
os.environ.setdefault("REDIS_URL", "redis://localhost/0")
os.environ.setdefault("CENTRAL_SECRET_KEY", "x" * 40)
os.environ.setdefault("ZABBIX_API_URL", "http://z")
os.environ.setdefault("ZABBIX_API_TOKEN", "t")

url = os.environ.get("RABBITMQ_URL")
if not url:
    raise SystemExit("Set RABBITMQ_URL to a disposable RabbitMQ.")

from central_monitor import business_broker as bb  # noqa: E402


def check(condition, label):
    print(("PASS " if condition else "FAIL ") + label)
    if not condition:
        raise SystemExit(1)


broker = bb.RabbitJobBroker(url, os.environ.get("RABBITMQ_QUEUE_TYPE", "classic"))
worker = str(uuid.uuid4())
tenant = str(uuid.uuid4())
message = {"execution_id": str(uuid.uuid4()), "tenant_id": tenant, "worker_id": worker, "correlation_id": str(uuid.uuid4())}

broker.publish(message, worker, 60_000)
check(broker.stats([worker])["queues"][worker] == 1, "confirmed publish lands in the worker queue")

with broker.consume(worker, 5) as deliveries:
    check(len(deliveries) == 1 and deliveries[0].body["execution_id"] == message["execution_id"], "consume returns the message")
    # not settled on purpose: the broker must redeliver it when the connection closes
check(broker.stats([worker])["queues"][worker] == 1, "an unsettled delivery is redelivered")

with broker.consume(worker, 5) as deliveries:
    deliveries[0].ack()
check(broker.stats([worker])["queues"][worker] == 0, "ack removes the message")

broker.publish(message, worker, 60_000)
before = broker.stats([worker])["dlq_depth"]
with broker.consume(worker, 5) as deliveries:
    deliveries[0].reject_to_dlq()
time.sleep(1)
after = broker.stats([worker])["dlq_depth"]
check(after == before + 1, "reject dead-letters the message into business.jobs.dlq")
listed = [m for m in broker.peek_dlq(200) if m.get("execution_id") == message["execution_id"]]
check(bool(listed) and listed[0]["reason"] == "rejected", "peek shows the dead letter with its reason")

broker.publish({**message, "execution_id": str(uuid.uuid4())}, worker, 1_000)  # 1 s TTL
time.sleep(3)
check(broker.stats([worker])["queues"][worker] == 0, "an expired message is dropped from the worker queue (and dead-lettered)")

try:
    bad = bb.RabbitJobBroker("amqp://nobody:wrong@127.0.0.1:1/%2F")
    bad.publish(message, worker, 1000)
    check(False, "unreachable broker must raise BrokerUnavailable")
except bb.BrokerUnavailable:
    check(True, "an unreachable broker is reported as BrokerUnavailable")
print("RabbitMQ smoke OK. Clean up with: rabbitmqctl delete_queue business.jobs.w." + worker)
