"""Evaluation harness (plan v2 section 11, ``make eval``).

Planted scenarios over real (gold) customers and transactions run END TO END through the same app
code as the chat (ChatApp -> orchestrator -> gateway -> lane rules -> case -> lifecycle ->
investigator), with a simulated customer that answers the buttons like a careful person. The same
scenarios run for v2 and for the v1.4 rules-only baseline (B1), so the report shows exactly what
ML and GenAI add. Every message is generated and labelled synthetic.
"""
