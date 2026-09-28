"""G2 investigator entry point, invoked by the case state machine.

Placeholder until the investigator (andres) implements INTERFACES.md #6. Returns a
report marked as not produced, so the state machine still reaches the analyst step.
"""


def handler(event, context):
    return {
        "case_id": event.get("case_id"),
        "status": "not_implemented",
        "citations_valid": False,
    }
