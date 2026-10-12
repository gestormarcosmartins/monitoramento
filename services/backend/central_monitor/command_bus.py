import hashlib
import json
from typing import Any

from psycopg.types.json import Jsonb


def canonical_payload_hash(payload:dict[str,Any])->str:
    return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=False,default=str).encode()).hexdigest()


def enqueue_command(connection:Any,tenant_id:str,topic:str,payload:dict[str,Any],idempotency_key:str,requested_by:str|None=None)->dict[str,Any]:
    digest=canonical_payload_hash(payload)
    existing=connection.execute("SELECT * FROM cm_outbox WHERE tenant_id=%s AND idempotency_key=%s FOR UPDATE",(tenant_id,idempotency_key)).fetchone()
    if existing:
        if existing["payload_hash"]!=digest:
            return connection.execute("UPDATE cm_outbox SET state='conflict',state_reason='idempotency_key_payload_mismatch',last_error='Idempotency key reused with a different canonical payload' WHERE id=%s RETURNING *",(existing["id"],)).fetchone()
        return existing
    return connection.execute("INSERT INTO cm_outbox(tenant_id,topic,payload,idempotency_key,payload_hash,requested_by) VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",(tenant_id,topic,Jsonb(payload),idempotency_key,digest,requested_by)).fetchone()
