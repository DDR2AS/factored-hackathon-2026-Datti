"""``STORE_BACKEND=dynamodb``: the SessionStore, CaseStore and ReviewStore of ``conversation/store.py``
over the DynamoDB tables of ``infra/stacks/data_stack.py`` (names from #9: ``TABLE_SESSIONS``,
``TABLE_CASES``). Same behaviour as the memory stores (copies on read and write, conditional
write on ``version``), with these choices:

Sessions (``TABLE_SESSIONS``, partition key ``session_id``, TTL attribute ``expires_at_epoch``)
- session item ``{session_id: "S-...", kind: "session", token_hash, expires_at (the ISO string the
  app wrote), expires_at_epoch (NUMBER, epoch seconds: DynamoDB TTL only reads numbers),
  doc | doc_gz, updated_at}``.
- token pointer ``{session_id: "TOKEN#<sha256>", kind: "token", target: "S-...", expires_at_epoch}``:
  ``get_by_token_hash`` is two strongly consistent GetItems. A GSI on the hash would be eventually
  consistent and could answer 401 to the first chat turn of a session created milliseconds earlier.
- TTL deletes expired items within hours; until then the app's own ``expires_at`` check answers
  401 (``SessionService.authenticate``), so there is no ``purge_expired`` here.
- Last writer wins, like the memory store. Turns of one session are serialised per container
  (``SessionService.turn_guard``); two containers serving the same session at the same time is
  possible in theory (the front end sends one message at a time and ``client_msg_id`` is idempotent).

Cases (``TABLE_CASES``, partition key ``case_id``)
- case item ``{case_id: "EV-...", kind: "case", doc | doc_gz (the CaseRecord), version, session_id,
  status, created_at, customer_id, lane, queue_lane (B or C only), updated_at}``. ``status``,
  ``customer_id`` and ``created_at`` feed the GSIs ``by_status`` and ``by_customer``; ``queue_lane``
  feeds the sparse KEYS_ONLY GSI ``by_queue`` (lanes B and C: what the analyst queue shows).
- ``put`` is ONE conditional UpdateItem on ``version`` (an existing case) and, only for a new
  case, a second conditional write (``attribute_not_exists``): a stale version raises
  ``CaseVersionConflict`` (409 for the analyst decision). Only the listed attributes are written,
  so the internal lifecycle attributes of ``cloud/stepfunctions_lifecycle.py`` (``execution_arn``,
  ``execution_round``, ``decided_round``, ``task_token``, ``task_token_at``, ``task_error``,
  ``lc_step``) survive every put; they are not part of the CaseRecord and never reach a view.
- ``all()`` = the ``by_queue`` index (keys only) + consistent BatchGetItem of the cases: the
  analyst queue only lists lanes B and C, and a lane A case (answered in the chat) is never in it.
  Fine for the hackathon volume (hundreds of cases); a real queue would page by status/breach_at.

Reviews (``ReviewStore``): extra items in the same Cases table, so every function that already
reads cases (API, investigator, case steps) can use them without a new table, variable or grant:
- ``REPORT#<case_id>``: the investigator report (#6).
- ``CONV#<case_id>``: the case's redacted conversation, the last ``MAX_CONVERSATION_TURNS`` (200)
  turns as one gzipped document with a ``rev`` counter (optimistic retry on concurrent appends).
- ``DECISION#<key>``: the stored reply of an analyst decision (``put`` only if absent: first wins).
Their ``case_id`` never matches ``EV-...``; ``CaseStore.get`` ignores any item whose ``kind`` is not
``case`` and the GSIs never index them (they lack the key attributes).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from cloud import ddb
from conversation.case import CaseRecord
from conversation.store import (
    MAX_CONVERSATION_TURNS, CaseVersionConflict, StoreConfigError, StoredCase, Stores,
)

TOKEN_PREFIX = "TOKEN#"
REPORT_PREFIX = "REPORT#"
CONV_PREFIX = "CONV#"
DECISION_PREFIX = "DECISION#"
QUEUE_INDEX = "by_queue"
QUEUE_LANES = ("B", "C")
TTL_ATTRIBUTE = "expires_at_epoch"
CONVERSATION_ATTEMPTS = 6
BATCH_GET_LIMIT = 100
INTERNAL_ATTRIBUTES = ("execution_arn", "execution_round", "decided_round", "task_token", "task_token_at",
                       "task_execution_arn", "task_error", "lc_step")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _epoch(expires_at: Any) -> int | None:
    """Epoch seconds of an ISO 8601 string (``...Z`` accepted); None if it is not one."""
    if not isinstance(expires_at, str) or not expires_at:
        return None
    try:
        dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _default_client() -> Any:
    from cloud import aws

    return aws.client("dynamodb")


# ---------------------------------------------------------------- sessions

class DynamoSessionStore:
    def __init__(self, table: str, client: Any = None) -> None:
        self.table = table
        self.client = client or _default_client()

    def put(self, session: dict[str, Any]) -> None:
        data = json.loads(json.dumps(session))  # JSON-ready check and copy, like the memory store
        sid, token_hash = str(data["session_id"]), str(data["token_hash"])
        if sid.startswith(TOKEN_PREFIX):
            raise ValueError("session_id cannot use the token prefix")
        epoch = _epoch(data.get("expires_at"))
        item = {"session_id": ddb.s(sid), "kind": ddb.s("session"), "token_hash": ddb.s(token_hash),
                "updated_at": ddb.s(_iso(_utc_now())), **ddb.pack(data)}
        if data.get("expires_at"):
            item["expires_at"] = ddb.s(data["expires_at"])
        if epoch is not None:
            item[TTL_ATTRIBUTE] = ddb.n(epoch)
        # Usual case (every turn saves its session): same token, same expiry, so the pointer is
        # already right. One conditional write and no old item sent back over the network.
        values = {":th": ddb.s(token_hash), **({":ttl": ddb.n(epoch)} if epoch is not None else {})}
        try:
            self.client.put_item(
                TableName=self.table, Item=item, ExpressionAttributeNames={"#th": "token_hash", "#ttl": TTL_ATTRIBUTE},
                ConditionExpression="#th = :th AND " + ("#ttl = :ttl" if epoch is not None else "attribute_not_exists(#ttl)"),
                ExpressionAttributeValues=values)
            return
        except Exception as e:
            if not ddb.is_conditional_failure(e):
                raise
        # A new session (or a new token or expiry): write it, then (re)point the token at it.
        old = self.client.put_item(TableName=self.table, Item=item, ReturnValues="ALL_OLD").get("Attributes") or {}
        old_hash = (old.get("token_hash") or {}).get("S")
        old_epoch = (old.get(TTL_ATTRIBUTE) or {}).get("N")
        if old_hash != token_hash or old_epoch != (str(epoch) if epoch is not None else None):
            pointer = {"session_id": ddb.s(TOKEN_PREFIX + token_hash), "kind": ddb.s("token"), "target": ddb.s(sid)}
            if epoch is not None:
                pointer[TTL_ATTRIBUTE] = ddb.n(epoch)
            self.client.put_item(TableName=self.table, Item=pointer)
        if old_hash and old_hash != token_hash:
            self._drop_pointer(old_hash, sid)

    def get(self, session_id: str) -> dict[str, Any] | None:
        if not session_id or str(session_id).startswith(TOKEN_PREFIX):
            return None
        item = self._get(str(session_id))
        if not item or (item.get("kind") or {}).get("S") != "session":
            return None
        return ddb.unpack(item)

    def get_by_token_hash(self, token_hash: str) -> dict[str, Any] | None:
        if not token_hash:
            return None
        pointer = self._get(TOKEN_PREFIX + str(token_hash))
        target = ((pointer or {}).get("target") or {}).get("S")
        if not target:
            return None
        item = self._get(target)
        if not item or (item.get("token_hash") or {}).get("S") != token_hash:
            return None  # a stale pointer (the session rotated its token or is gone)
        return ddb.unpack(item)

    def delete(self, session_id: str) -> None:
        if not session_id or str(session_id).startswith(TOKEN_PREFIX):
            return
        old = self.client.delete_item(TableName=self.table, Key={"session_id": ddb.s(session_id)},
                                      ReturnValues="ALL_OLD").get("Attributes") or {}
        token_hash = (old.get("token_hash") or {}).get("S")
        if token_hash:
            self._drop_pointer(token_hash, str(session_id))

    def _get(self, key: str) -> dict[str, Any] | None:
        return self.client.get_item(TableName=self.table, Key={"session_id": ddb.s(key)},
                                    ConsistentRead=True).get("Item")

    def _drop_pointer(self, token_hash: str, sid: str) -> None:
        try:
            self.client.delete_item(TableName=self.table, Key={"session_id": ddb.s(TOKEN_PREFIX + token_hash)},
                                    ConditionExpression="#target = :sid", ExpressionAttributeNames={"#target": "target"},
                                    ExpressionAttributeValues={":sid": ddb.s(sid)})
        except Exception as e:
            if not ddb.is_conditional_failure(e):
                raise


# ---------------------------------------------------------------- cases

class DynamoCaseStore:
    def __init__(self, table: str, client: Any = None, clock: Callable[[], datetime] | None = None) -> None:
        self.table = table
        self.client = client or _default_client()
        self.clock = clock  # build_app wires the app clock (updated_at), like MemoryCaseStore

    # ------------------------------------------------------------ protocol

    def put(self, case: CaseRecord, session_id: str) -> None:
        now = (self.clock or _utc_now)()
        expected = int(case.version)
        updated = case.model_copy(update={"version": expected + 1, "updated_at": now})
        try:
            self._write(updated, session_id, now, "attribute_exists(case_id) AND #version = :expected",
                        {":expected": ddb.n(expected)})
        except Exception as e:
            if not ddb.is_conditional_failure(e):
                raise
            stored_version = self._stored_version(case.case_id)
            if stored_version is not None:
                raise CaseVersionConflict(
                    f"case {case.case_id}: version {expected} is stale (stored {stored_version})") from None
            updated = case.model_copy(update={"version": max(1, expected), "updated_at": now})
            try:
                self._write(updated, session_id, now, "attribute_not_exists(case_id)", {})
            except Exception as e2:
                if ddb.is_conditional_failure(e2):
                    raise CaseVersionConflict(f"case {case.case_id}: created concurrently") from None
                raise
        case.version = updated.version
        case.updated_at = now

    def get(self, case_id: str) -> StoredCase | None:
        if not case_id:
            return None
        item = self.client.get_item(TableName=self.table, Key={"case_id": ddb.s(case_id)},
                                    ConsistentRead=True).get("Item")
        return self._stored(item)

    def all(self) -> list[StoredCase]:
        """Every case the analyst queue can show (lanes B and C), read consistently."""
        ids: list[str] = []
        for lane in QUEUE_LANES:
            kwargs: dict[str, Any] = {
                "TableName": self.table, "IndexName": QUEUE_INDEX,
                "KeyConditionExpression": "#lane = :lane", "ExpressionAttributeNames": {"#lane": "queue_lane"},
                "ExpressionAttributeValues": {":lane": ddb.s(lane)},
            }
            while True:
                page = self.client.query(**kwargs)
                ids += [it["case_id"]["S"] for it in page.get("Items", [])]
                if not page.get("LastEvaluatedKey"):
                    break
                kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
        out = []
        for item in self._batch_get(list(dict.fromkeys(ids))):
            stored = self._stored(item)
            if stored is not None:
                out.append(stored)
        out.sort(key=lambda sc: (sc.case.created_at, sc.case.case_id))
        return out

    # ------------------------------------------------------------ internal lifecycle attributes

    def get_internal(self, case_id: str) -> dict[str, Any]:
        """The cloud lifecycle attributes of a case (task token, execution ...), {} if none."""
        item = self.client.get_item(TableName=self.table, Key={"case_id": ddb.s(case_id)}, ConsistentRead=True,
                                    ProjectionExpression=", ".join(f"#{a}" for a in INTERNAL_ATTRIBUTES),
                                    ExpressionAttributeNames={f"#{a}": a for a in INTERNAL_ATTRIBUTES}).get("Item")
        out = ddb.plain(item)
        for k, v in list(out.items()):
            if k in ("execution_round", "decided_round", "lc_step") and v is not None:
                out[k] = int(v)
        return out

    def set_internal(self, case_id: str, condition: str | None = None,
                     condition_values: dict[str, dict] | None = None, **attrs: Any) -> bool:
        """SET internal attributes without touching the case document or its version. False if the
        case does not exist or ``condition`` failed."""
        bad = set(attrs) - set(INTERNAL_ATTRIBUTES)
        if bad:
            raise ValueError(f"not internal attributes: {sorted(bad)}")
        names = {f"#{k}": k for k in attrs}
        values = {f":{k}": (ddb.n(v) if isinstance(v, int) and not isinstance(v, bool) else ddb.s(v))
                  for k, v in attrs.items()}
        expr = "attribute_exists(case_id)" + (f" AND ({condition})" if condition else "")
        try:
            self.client.update_item(TableName=self.table, Key={"case_id": ddb.s(case_id)},
                                    UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in attrs),
                                    ConditionExpression=expr, ExpressionAttributeNames=names,
                                    ExpressionAttributeValues={**values, **(condition_values or {})})
        except Exception as e:
            if ddb.is_conditional_failure(e):
                return False
            raise
        return True

    def remove_internal(self, case_id: str, *names: str) -> None:
        bad = set(names) - set(INTERNAL_ATTRIBUTES)
        if bad:
            raise ValueError(f"not internal attributes: {sorted(bad)}")
        if not names:
            return
        try:
            self.client.update_item(TableName=self.table, Key={"case_id": ddb.s(case_id)},
                                    UpdateExpression="REMOVE " + ", ".join(f"#{k}" for k in names),
                                    ConditionExpression="attribute_exists(case_id)",
                                    ExpressionAttributeNames={f"#{k}": k for k in names})
        except Exception as e:
            if not ddb.is_conditional_failure(e):
                raise

    def next_trace_step(self, case_id: str) -> int:
        """Atomic counter for the ``step`` of the case's lifecycle trace (``lc-<case_id>``), shared by
        every Lambda that writes to it. Does not bump ``version``."""
        out = self.client.update_item(TableName=self.table, Key={"case_id": ddb.s(case_id)},
                                      UpdateExpression="ADD #step :one", ConditionExpression="attribute_exists(case_id)",
                                      ExpressionAttributeNames={"#step": "lc_step"},
                                      ExpressionAttributeValues={":one": ddb.n(1)}, ReturnValues="UPDATED_NEW")
        return int(out["Attributes"]["lc_step"]["N"])

    # ------------------------------------------------------------ helpers

    def _write(self, case: CaseRecord, session_id: str, now: datetime, condition: str,
               condition_values: dict[str, dict]) -> None:
        doc = case.model_dump(mode="json", by_alias=True)
        sets, removes, names, values = ddb.doc_update(doc)
        fields = {
            "kind": ddb.s("case"), "version": ddb.n(case.version), "session_id": ddb.s(session_id),
            "status": ddb.s(case.status.value), "created_at": ddb.s(doc["created_at"]),
            "customer_id": ddb.s(case.customer_id), "updated_at": ddb.s(_iso(now)),
        }
        lane = case.lane.value if case.lane is not None else None
        if lane is not None:
            fields["lane"] = ddb.s(lane)
        else:
            removes.append("#lane")
            names["#lane"] = "lane"
        if lane in QUEUE_LANES:
            fields["queue_lane"] = ddb.s(lane)
        else:
            removes.append("#queue_lane")
            names["#queue_lane"] = "queue_lane"
        for k, v in fields.items():
            sets.append(f"#{k} = :{k}")
            names[f"#{k}"] = k
            values[f":{k}"] = v
        expr = "SET " + ", ".join(sets) + (" REMOVE " + ", ".join(removes) if removes else "")
        self.client.update_item(TableName=self.table, Key={"case_id": ddb.s(case.case_id)}, UpdateExpression=expr,
                                ConditionExpression=condition, ExpressionAttributeNames=names,
                                ExpressionAttributeValues={**values, **condition_values})

    def _stored_version(self, case_id: str) -> int | None:
        item = self.client.get_item(TableName=self.table, Key={"case_id": ddb.s(case_id)}, ConsistentRead=True,
                                    ProjectionExpression="#v", ExpressionAttributeNames={"#v": "version"}).get("Item")
        if not item:
            return None
        return int((item.get("version") or {}).get("N") or 0)

    @staticmethod
    def _stored(item: dict[str, Any] | None) -> StoredCase | None:
        if not item or (item.get("kind") or {}).get("S") != "case":
            return None
        doc = ddb.unpack(item)
        if doc is None:
            return None
        return StoredCase(case=CaseRecord.model_validate(doc), session_id=item["session_id"]["S"])

    def _batch_get(self, ids: list[str]) -> Iterable[dict[str, Any]]:
        for start in range(0, len(ids), BATCH_GET_LIMIT):
            keys = [{"case_id": ddb.s(i)} for i in ids[start:start + BATCH_GET_LIMIT]]
            request = {self.table: {"Keys": keys, "ConsistentRead": True}}
            for attempt in range(8):
                out = self.client.batch_get_item(RequestItems=request)
                yield from out.get("Responses", {}).get(self.table, [])
                request = out.get("UnprocessedKeys") or {}
                if not request:
                    break
                time.sleep(min(1.0, 0.05 * 2 ** attempt))


# ---------------------------------------------------------------- reviews

class DynamoReviewStore:
    """Reports, decision replies and case conversations as extra items of the Cases table."""

    def __init__(self, table: str, client: Any = None, max_turns: int = MAX_CONVERSATION_TURNS) -> None:
        self.table = table
        self.client = client or _default_client()
        self.max_turns = max_turns

    def put_report(self, case_id: str, report: dict[str, Any]) -> None:
        item = {"case_id": ddb.s(REPORT_PREFIX + case_id), "kind": ddb.s("report"),
                "created_at": ddb.s(_iso(_utc_now())), **ddb.pack(report)}
        self.client.put_item(TableName=self.table, Item=item)

    def get_report(self, case_id: str) -> dict[str, Any] | None:
        return ddb.unpack(self._get(REPORT_PREFIX + case_id) or {})

    def put_decision_reply(self, key: str, body_hash: str, response: dict[str, Any]) -> None:
        item = {"case_id": ddb.s(DECISION_PREFIX + key), "kind": ddb.s("decision_reply"),
                "body_hash": ddb.s(body_hash), "created_at": ddb.s(_iso(_utc_now())), **ddb.pack(response)}
        try:
            self.client.put_item(TableName=self.table, Item=item, ConditionExpression="attribute_not_exists(case_id)")
        except Exception as e:
            if not ddb.is_conditional_failure(e):
                raise  # the first stored reply wins (idempotency), like setdefault in memory

    def get_decision_reply(self, key: str) -> tuple[str, dict[str, Any]] | None:
        item = self._get(DECISION_PREFIX + key)
        if not item:
            return None
        return item["body_hash"]["S"], ddb.unpack(item)

    def append_conversation(self, case_id: str, turns: list[dict[str, Any]]) -> None:
        turns = json.loads(ddb.dumps(list(turns)))  # copy, JSON-ready
        if not turns:
            return
        key = CONV_PREFIX + case_id
        for attempt in range(CONVERSATION_ATTEMPTS):
            item = self._get(key)
            rev = int((item or {}).get("rev", {}).get("N") or 0)
            conv = (ddb.unpack(item) or []) if item else []
            conv = (conv + turns)[-self.max_turns:]
            new_item = {"case_id": ddb.s(key), "kind": ddb.s("conversation"), "rev": ddb.n(rev + 1),
                        "turns": ddb.n(len(conv)), "updated_at": ddb.s(_iso(_utc_now())), **ddb.pack(conv)}
            try:
                if item is None:
                    self.client.put_item(TableName=self.table, Item=new_item,
                                         ConditionExpression="attribute_not_exists(case_id)")
                else:
                    self.client.put_item(TableName=self.table, Item=new_item, ConditionExpression="#rev = :rev",
                                         ExpressionAttributeNames={"#rev": "rev"},
                                         ExpressionAttributeValues={":rev": ddb.n(rev)})
                return
            except Exception as e:
                if not ddb.is_conditional_failure(e):
                    raise
                time.sleep(0.02 * (attempt + 1))
        raise CaseVersionConflict(f"conversation of {case_id}: too many concurrent appends")

    def get_conversation(self, case_id: str) -> list[dict[str, Any]]:
        return list(ddb.unpack(self._get(CONV_PREFIX + case_id) or {}) or [])

    def _get(self, key: str) -> dict[str, Any] | None:
        return self.client.get_item(TableName=self.table, Key={"case_id": ddb.s(key)},
                                    ConsistentRead=True).get("Item")


# ---------------------------------------------------------------- selection

def make_dynamodb_stores(env: dict[str, str], client: Any = None) -> Stores:
    """The three stores plus the trace sink, from ``TABLE_SESSIONS``/``TABLE_CASES`` (#9)."""
    missing = [k for k in ("TABLE_SESSIONS", "TABLE_CASES") if not (env.get(k) or "").strip()]
    if missing:
        raise StoreConfigError(
            f"STORE_BACKEND=dynamodb needs {', '.join(missing)}: the CDK DataStack (Andrés's infra/, #9) sets "
            "them in every Lambda; locally use STORE_BACKEND=memory.")
    import importlib.util

    if importlib.util.find_spec("boto3") is None:  # always in the Lambda runtime
        raise StoreConfigError("STORE_BACKEND=dynamodb needs boto3")
    from cloud.traces import make_trace_sink

    client = client or _default_client()
    cases_table = env["TABLE_CASES"].strip()
    return Stores(
        sessions=DynamoSessionStore(env["TABLE_SESSIONS"].strip(), client=client),
        cases=DynamoCaseStore(cases_table, client=client),
        traces=make_trace_sink(env),
        backend="dynamodb",
        reviews=DynamoReviewStore(cases_table, client=client),
    )
