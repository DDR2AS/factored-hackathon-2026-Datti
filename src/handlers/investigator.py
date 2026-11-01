"""G2 investigator entry point, invoked by the case state machine (WorkflowStack, ``Investigate``).

Event ``{"case_id": "EV-..."}``. Loads the case, runs ``make_investigator(os.environ)``
(``INVESTIGATOR_BACKEND``: g2 = the agent in ``src/investigator`` with ``LLM_PROVIDER``; stub = the
deterministic report) through the configured gateway (``GATEWAY_BACKEND``: composite + DynamoDB
serving table in AWS, demo customers from the packaged YAML), checks and stores the report (#6)
in the ReviewStore and links it to the case. Returns
``{case_id, status: report_ready | no_report | skipped | not_found, report_id?, error_code?}``.

An expected failure (tools down after retries, budget spent, invalid report, the model never
submitted) is NOT an exception: the case is flagged ``evidence_incomplete`` here and the state
machine goes on to the analyst. Anything unexpected raises, and the state machine's catch runs
``case_steps.mark_incomplete`` (the alarm on investigator errors fires).
"""

from __future__ import annotations

import json
import os

_PARTS = None


def parts():
    """(lifecycle, gateway, investigator) for this container, built once from the environment."""
    global _PARTS
    if _PARTS is None:
        from cloud.stepfunctions_lifecycle import StepFunctionsLifecycle
        from conversation.lifecycle import make_investigator
        from conversation.store import make_stores
        from gateway import make_gateway

        gateway = make_gateway(os.environ)
        lc = StepFunctionsLifecycle.from_env(os.environ, make_stores(os.environ), gateway=gateway)
        _PARTS = (lc, gateway, make_investigator(os.environ))
    return _PARTS


def set_parts(value) -> None:
    """Dependency injection for tests: (lifecycle, gateway, investigator)."""
    global _PARTS
    _PARTS = value


def handler(event, context):
    case_id = (event or {}).get("case_id")
    if not case_id or not isinstance(case_id, str):
        raise ValueError("investigator event without case_id")
    lc, gateway, investigator = parts()
    try:
        result = lc.run_investigation(case_id, investigator=investigator, gateway=gateway)
    finally:
        from cloud.traces import flush

        flush(lc.stores.traces)
    print(json.dumps({"case_id": case_id, "status": result.get("status"), "error_code": result.get("error_code")}))
    return result
