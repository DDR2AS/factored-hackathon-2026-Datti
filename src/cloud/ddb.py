"""Small helpers for the DynamoDB adapters (low-level client, explicit attribute values).

Documents (a session, a CaseRecord, a report, a conversation) are stored as ONE attribute holding
their JSON, never as native DynamoDB maps:

- exact round trip: floats stay floats and ints stay ints (a native map would turn every number
  into a Decimal, and free-form dicts such as ``evidence.transaction`` or the M3 features would
  come back with Decimals that pydantic serialises as strings);
- any key is valid (DynamoDB map keys cannot be empty) and nesting depth is not an issue;
- ``doc`` (a readable JSON string) up to ``INLINE_DOC_BYTES``, else ``doc_gz`` (gzip, binary), so
  a long session (30 stored replies) stays far below the 400 KB item limit.

The attributes that tables and indexes need (keys, ``status``, ``version`` ...) are written next to
the document as plain attributes.
"""

from __future__ import annotations

import gzip
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

INLINE_DOC_BYTES = 32 * 1024
MAX_ITEM_BYTES = 400 * 1024


class DocumentTooLarge(ValueError):
    """The compressed document still does not fit in one DynamoDB item."""


def _default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() and value.as_tuple().exponent >= 0 else float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=_default)


def pack(obj: Any) -> dict[str, dict]:
    """The document as attribute values: ``{"doc": {"S": json}}`` or ``{"doc_gz": {"B": gzip}}``."""
    text = dumps(obj)
    raw = text.encode("utf-8")
    if len(raw) <= INLINE_DOC_BYTES:
        return {"doc": {"S": text}}
    packed = gzip.compress(raw, compresslevel=6)
    if len(packed) > MAX_ITEM_BYTES - 8 * 1024:
        raise DocumentTooLarge(f"document of {len(raw)} bytes is {len(packed)} bytes gzipped")
    return {"doc_gz": {"B": packed}}


def unpack(item: dict[str, dict]) -> Any:
    """The document of an item read with the low-level client (None if it has none)."""
    if "doc" in item:
        return json.loads(item["doc"]["S"])
    if "doc_gz" in item:
        blob = item["doc_gz"]["B"]
        return json.loads(gzip.decompress(bytes(blob)).decode("utf-8"))
    return None


def doc_update(obj: Any) -> tuple[list[str], list[str], dict[str, str], dict[str, dict]]:
    """SET/REMOVE parts of an UpdateExpression that store ``obj`` and drop the other encoding."""
    attrs = pack(obj)
    keep, drop = ("doc", "doc_gz") if "doc" in attrs else ("doc_gz", "doc")
    names = {"#doc_keep": keep, "#doc_drop": drop}
    values = {":doc_keep": attrs[keep]}
    return ["#doc_keep = :doc_keep"], ["#doc_drop"], names, values


def s(value: Any) -> dict:
    return {"S": str(value)}


def n(value: Any) -> dict:
    return {"N": str(value)}


def plain(item: dict[str, dict] | None) -> dict[str, Any]:
    """Scalar attributes of a low-level item as Python values (S, N, BOOL, NULL; maps/lists via the
    boto3 deserializer). Numbers come back as Decimal."""
    if not item:
        return {}
    from boto3.dynamodb.types import TypeDeserializer

    des = TypeDeserializer()
    return {k: des.deserialize(v) for k, v in item.items() if k not in ("doc", "doc_gz")}


def is_conditional_failure(exc: BaseException) -> bool:
    from cloud.aws import error_code

    return error_code(exc) in ("ConditionalCheckFailedException", "TransactionCanceledException")
