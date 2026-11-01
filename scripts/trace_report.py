"""Trace queries over data/traces/*.jsonl (same SQL as Athena in the cloud). See src/eval/traces.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eval.traces import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
