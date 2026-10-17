"""Tabular JSON result contract, metric extraction and manifest signing for Business monitors.

Pure functions (no database, no HTTP) so the contract is unit tested and shared by API and tests.
Rules (spec section 7): the column list is always present; each row is ONE object holding every
configured column; a missing value is null; an empty result is a success with rows = []; nothing is
coerced to zero. Decimals travel as numbers or numeric strings and are kept exact (Decimal, never float).
"""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

from .settings import get_settings

COLUMN_TYPES = ("string", "integer", "decimal", "boolean", "date", "datetime")
COLUMN_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,62}$")
MAX_COLUMNS = 50
# Platform ceilings: the Master may tune limits per monitor only inside these.
CEILING = {"timeout_seconds": 60, "max_rows": 5000, "max_bytes": 5 * 1024 * 1024}
DEFAULT_LIMITS = {"timeout_seconds": 15, "max_rows": 500, "max_bytes": 5 * 1024 * 1024}
METRIC_KINDS = ("row_count", "sum", "max", "count_nonnull", "first_value")
NUMERIC_TYPES = ("integer", "decimal")


class ContractError(ValueError):
    """The submitted schema, limits or result do not satisfy the contract."""


def validate_columns(columns: Any) -> list[dict[str, str]]:
    if not isinstance(columns, list) or not 1 <= len(columns) <= MAX_COLUMNS:
        raise ContractError(f"A monitor needs between 1 and {MAX_COLUMNS} columns")
    seen: set[str] = set()
    clean = []
    for column in columns:
        if not isinstance(column, dict) or set(column) != {"name", "type"}:
            raise ContractError("Each column needs exactly a name and a type")
        name, kind = column["name"], column["type"]
        if not isinstance(name, str) or not COLUMN_NAME.fullmatch(name) or name.lower() in seen:
            raise ContractError("Invalid or duplicated column name")
        if kind not in COLUMN_TYPES:
            raise ContractError("Unsupported column type")
        seen.add(name.lower())
        clean.append({"name": name, "type": kind})
    return clean


def validate_limits(limits: dict[str, Any] | None) -> dict[str, int]:
    merged = {**DEFAULT_LIMITS, **(limits or {})}
    if set(merged) != set(DEFAULT_LIMITS):
        raise ContractError("Unknown limit")
    for key, value in merged.items():
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= CEILING[key]:
            raise ContractError(f"{key} must be between 1 and {CEILING[key]}")
    return merged


def validate_metric(metric: Any, columns: list[dict[str, str]]) -> dict[str, str]:
    if not isinstance(metric, dict) or metric.get("kind") not in METRIC_KINDS:
        raise ContractError("Unsupported metric")
    kind = metric["kind"]
    if kind == "row_count":
        if set(metric) != {"kind"}:
            raise ContractError("row_count takes no column")
        return {"kind": kind}
    if set(metric) != {"kind", "column"}:
        raise ContractError("This metric needs a column")
    by_name = {c["name"]: c["type"] for c in columns}
    column = metric["column"].strip() if isinstance(metric["column"], str) else metric["column"]  # espacos de copiar/colar nao contam
    kind_of_column = by_name.get(column)
    if kind_of_column is None:
        raise ContractError(f"Metric column is not in the schema (columns: {', '.join(by_name)})")
    if kind != "count_nonnull" and kind_of_column not in NUMERIC_TYPES:
        raise ContractError("Metric needs a numeric column")
    return {"kind": kind, "column": column}


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ContractError("Value is not numeric")
    try:
        number = Decimal(str(value))
    except InvalidOperation as error:
        raise ContractError("Value is not numeric") from error
    if not number.is_finite():
        raise ContractError("Value is not finite")
    return number


def _check_cell(kind: str, value: Any) -> Any:
    if value is None:
        return None  # nulls are preserved, never turned into zero or empty text
    if kind == "string":
        if not isinstance(value, str):
            raise ContractError("Expected string")
        return value
    if kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ContractError("Expected integer")
        return value
    if kind == "decimal":
        return str(_decimal(value))
    if kind == "boolean":
        if not isinstance(value, bool):
            raise ContractError("Expected boolean")
        return value
    if kind == "date":
        try:
            return date.fromisoformat(str(value)).isoformat()
        except ValueError as error:
            raise ContractError("Expected ISO date") from error
    try:  # datetime: the zone is mandatory so thresholds never depend on a guessed timezone
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise ContractError("Expected ISO datetime") from error
    if parsed.tzinfo is None:
        raise ContractError("Datetime needs a timezone")
    return parsed.isoformat()


@dataclass(frozen=True)
class ValidatedResult:
    columns: list[dict[str, str]]
    rows: list[dict[str, Any]]
    row_count: int
    size_bytes: int
    truncated: bool


def validate_result(schema: list[dict[str, str]], limits: dict[str, int], payload: dict[str, Any]) -> ValidatedResult:
    """Check a worker's SUCCEEDED payload against the published revision."""
    if payload.get("columns") != schema:
        raise ContractError("Returned columns do not match the published schema")
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise ContractError("rows must be a list (empty list when there are no records)")
    if len(rows) > limits["max_rows"]:
        raise ContractError("Result exceeds max_rows")
    names = [c["name"] for c in schema]
    clean_rows = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != set(names):
            raise ContractError("Every row must be one object with every configured column")
        clean_rows.append({c["name"]: _check_cell(c["type"], row[c["name"]]) for c in schema})
    if payload.get("row_count") != len(clean_rows):
        raise ContractError("row_count does not match rows")
    truncated = payload.get("truncated")
    if not isinstance(truncated, bool):
        raise ContractError("truncated must be a boolean")
    if truncated and len(clean_rows) != limits["max_rows"]:
        raise ContractError("truncated is only valid when max_rows was reached")
    size = len(json.dumps(clean_rows, separators=(",", ":"), ensure_ascii=False).encode())
    if size > limits["max_bytes"]:
        raise ContractError("Result exceeds max_bytes")
    return ValidatedResult(schema, clean_rows, len(clean_rows), size, truncated)


def compute_metric(metric: dict[str, str], rows: list[dict[str, Any]]) -> Decimal | None:
    """Value fed to the indicator. None = no value (the indicator is not updated, so it ages to sem_dados)."""
    kind = metric["kind"]
    if kind == "row_count":
        return Decimal(len(rows))
    values = [r[metric["column"]] for r in rows]
    present = [v for v in values if v is not None]
    if kind == "count_nonnull":
        return Decimal(len(present))
    if kind == "first_value":
        return _decimal(present[0]) if rows and values[0] is not None else None
    if not present:
        return Decimal(0) if (kind == "sum" and not rows) else None  # empty result: nothing owed; all-null: unknown
    numbers = [_decimal(v) for v in present]
    return sum(numbers, Decimal(0)) if kind == "sum" else max(numbers)


# ------------------------------------------------------------------ manifest signing (Ed25519)
def _private_key() -> Ed25519PrivateKey:
    seed = get_settings().business_signing_key
    if not seed:
        raise RuntimeError("BUSINESS_SIGNING_KEY is not configured")
    raw = base64.b64decode(seed)
    if len(raw) != 32:
        raise RuntimeError("BUSINESS_SIGNING_KEY must be a base64 encoded 32-byte seed")
    return Ed25519PrivateKey.from_private_bytes(raw)


def canonical(manifest: dict[str, Any]) -> bytes:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()


def sign_manifest(manifest: dict[str, Any]) -> str:
    return base64.b64encode(_private_key().sign(canonical(manifest))).decode()


def public_key_b64() -> str:
    from cryptography.hazmat.primitives import serialization
    raw = _private_key().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def verify_manifest(manifest: dict[str, Any], signature: str, public_key: str) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key)).verify(base64.b64decode(signature), canonical(manifest))
        return True
    except (InvalidSignature, ValueError):
        return False
