"""Run the data pipeline from the repo root (``make pipeline``). Adds src/ to the import path,
like the Lambda package. See src/pipeline/run.py for the options."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pipeline.run import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
