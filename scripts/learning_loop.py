"""One demonstration cycle of the learning loop (SIMULATED analyst decisions). See src/eval/learning_loop.py.
Run after make pipeline, make train and make eval."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eval.learning_loop import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
