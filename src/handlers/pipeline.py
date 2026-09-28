"""Pipeline entry point: one partition per invocation, fanned out by Step Functions.

Placeholder until the pipeline owner (diego) exposes ``run_partition(table, date)``
(INTERFACES.md #5). The same function runs locally with DuckDB.
"""


def handler(event, context):
    table = event.get("table")
    date = event.get("date")
    if not table or not date:
        raise ValueError("Each partition needs 'table' and 'date'.")
    return {"table": table, "date": date, "status": "not_implemented"}
