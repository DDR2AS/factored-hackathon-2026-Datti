"""G3 offline judge from the repo root. See src/eval/judge.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eval.judge import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
