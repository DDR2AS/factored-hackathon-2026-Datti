"""Data pipeline (INTERFACES.md #5): raw CSV -> bronze -> silver -> quality -> gold -> serving.

Ported from Arturo's v1.4 pipeline (repo Arturo-GA/Hackathon-Factored: silver SQL, contracts)
and extended with the v2 gold tables. One partition at a time (``run_partition(table, date)``)
so the same functions run locally and inside a Lambda; ``backfill`` processes every partition of
a table in one DuckDB statement (same output layout, faster on a laptop).
"""

from pipeline.run import build_all, run_partition  # noqa: F401
