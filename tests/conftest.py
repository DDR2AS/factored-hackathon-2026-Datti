import sys
from pathlib import Path

# Same import root as the Lambda package: src/
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
