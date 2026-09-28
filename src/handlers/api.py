"""HTTP API entry point (API Gateway HTTP API, payload format 2.0).

Placeholder until the chat API owner (arturo) implements INTERFACES.md #1. Only
``GET /health`` works; every other route answers 501 with a stable error code.
"""

import json
import os


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def handler(event, context):
    if event.get("warmup"):
        return {"warm": True}

    route = event.get("routeKey", "")
    if route == "GET /health":
        return _response(200, {"status": "ok", "stage": os.environ.get("STAGE")})

    return _response(
        501,
        {"error": {"code": "not_implemented", "message": f"{route} is not implemented yet"}},
    )
