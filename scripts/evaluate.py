"""``make eval`` from the repo root: planted scenarios through the app, v2 vs B1, report.
Adds src/ to the import path like the Lambda package. Options: see src/eval/report.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eval.report import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
