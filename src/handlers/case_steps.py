"""Case lifecycle steps, invoked by the case state machine and by SLA timers.

Actions (``event["action"]``):
  register_task_token  store the analyst-wait token on the case (state machine waits)
  mark_incomplete      the investigator failed; flag the case for the analyst
  notify_customer      send the approved resolution to the customer
  sla_timer            an EventBridge Scheduler timer fired (24 h unassigned, 80% SLA, breach)

Placeholder: logs the action and returns. Owner: andres (with arturo for notify_customer).
"""

import json

ACTIONS = {"register_task_token", "mark_incomplete", "notify_customer", "sla_timer"}


def handler(event, context):
    action = event.get("action")
    if action not in ACTIONS:
        raise ValueError(f"Unknown case step action: {action!r}")
    # Never log the task token or message text; case ID and action are enough.
    print(json.dumps({"action": action, "case_id": event.get("case_id"), "status": "not_implemented"}))
    return {"case_id": event.get("case_id"), "action": action, "status": "not_implemented"}
