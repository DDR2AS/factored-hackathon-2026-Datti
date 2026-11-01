"""M2 runtime: load the artifact once per process and classify one message. Pure Python.

Standard library only for the math (``array`` for the weights, ``math`` for the softmax), plus
PyYAML (already a Lambda dependency) to read ``meta.yaml`` and ``thresholds.yaml``. Importing
this module or classifying never imports numpy, scipy or scikit-learn (``tests/test_m2.py``
checks it in a subprocess).

Artifact (written by ``models.m2.train``), ``src/models/artifacts/m2/<MODEL_VERSION>/``:

- ``weights.bin``: float32 little-endian, ``N_BUCKETS x n_classes`` weights (bucket-major),
  then ``n_classes`` biases. About 1.2 MB.
- ``meta.yaml``: version, class order, feature parameters, temperature, sha256 of the
  weights, training data fingerprints and the headline metrics.

Gate thresholds come from ``src/models/thresholds.yaml`` (key ``m2``), never from code.
"""

from __future__ import annotations

import hashlib
import math
import sys
import threading
from array import array
from pathlib import Path
from typing import Any

from models.m2.features import FEATURES_VERSION, N_BUCKETS, embed, readable

MODEL_VERSION = "m2-hashlr-1"
CLASSES = (
    "dispute_charge", "dispute_fee", "complaint_other", "case_status", "account_query",
    "lost_card", "human_request", "out_of_scope", "manipulation",
)
GATE_ACTIONS = ("accept", "ask", "human")
MODELS_DIR = Path(__file__).resolve().parents[1]
ARTIFACTS_ROOT = MODELS_DIR / "artifacts" / "m2"
THRESHOLDS_FILE = MODELS_DIR / "thresholds.yaml"
WEIGHTS_FILE = "weights.bin"
META_FILE = "meta.yaml"
# Output for a message with nothing to read (empty, only emoji or punctuation): the same
# convention as the keyword stand-in (m2-keywords-0), so the orchestrator shows its start
# buttons instead of handing an empty message to a person.
EMPTY_CLASS = "complaint_other"


class ArtifactError(RuntimeError):
    """The artifact or the thresholds are missing, corrupt or don't match this code."""


def gate_for(p: float, accept_threshold: float, ask_threshold: float) -> str:
    """accept if p >= accept_threshold; ask if p >= ask_threshold; human below."""
    if p >= accept_threshold:
        return "accept"
    if p >= ask_threshold:
        return "ask"
    return "human"


def read_thresholds(path: Path = THRESHOLDS_FILE) -> dict[str, Any]:
    """The ``m2`` section of thresholds.yaml (raises ArtifactError if absent)."""
    import yaml  # lazy: only when the model loads

    if not Path(path).exists():
        raise ArtifactError(f"{path} not found: run `python -m models.m2.train`")
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    section = data.get("m2")
    if not isinstance(section, dict) or "accept_threshold" not in section or "ask_threshold" not in section:
        raise ArtifactError(f"{path} has no m2.accept_threshold / m2.ask_threshold")
    return section


class M2Model:
    """Hashed n-grams + multinomial logistic regression + temperature. Immutable after load."""

    def __init__(self, weights: array, bias: list[float], classes: tuple[str, ...], temperature: float,
                 accept_threshold: float, ask_threshold: float, version: str, meta: dict | None = None):
        if len(weights) != N_BUCKETS * len(classes):
            raise ArtifactError(f"weights have {len(weights)} values, expected {N_BUCKETS * len(classes)}")
        if not 0.0 < temperature < 100.0:
            raise ArtifactError(f"bad temperature {temperature}")
        if not 0.0 <= ask_threshold <= accept_threshold <= 1.0:
            raise ArtifactError(f"bad thresholds ask={ask_threshold} accept={accept_threshold}")
        self.weights = weights
        self.bias = list(bias)
        self.classes = tuple(classes)
        self.k = len(self.classes)
        self.temperature = float(temperature)
        self.accept_threshold = float(accept_threshold)
        self.ask_threshold = float(ask_threshold)
        self.version = version
        self.meta = meta or {}

    # ------------------------------------------------------------ scores

    def logits_from_vector(self, vec: dict[int, float]) -> list[float]:
        k, w = self.k, self.weights
        z = list(self.bias)
        for b, v in vec.items():
            base = b * k
            for j in range(k):
                z[j] += v * w[base + j]
        return z

    def logits(self, text: str | None) -> list[float]:
        return self.logits_from_vector(embed(text))

    def proba_from_logits(self, z: list[float], temperature: float | None = None) -> list[float]:
        t = self.temperature if temperature is None else float(temperature)
        m = max(z)
        e = [math.exp((v - m) / t) for v in z]
        s = sum(e)
        return [v / s for v in e]

    def predict_proba(self, text: str | None, calibrated: bool = True) -> dict[str, float]:
        """{class: probability}; ``calibrated=False`` gives the raw softmax (temperature 1)."""
        p = self.proba_from_logits(self.logits(text), None if calibrated else 1.0)
        return dict(zip(self.classes, p))

    # ------------------------------------------------------------ #4

    def classify(self, text: str | None, language: str | None = None) -> dict[str, Any]:
        """INTERFACES.md #4 output for one (redacted) message. Never raises on any text."""
        if not isinstance(text, str):
            text = "" if text is None else str(text)
        vec = embed(text) if readable(text) else {}
        if not vec:
            return {"intent_class": EMPTY_CLASS, "p": round(1.0 / self.k, 3), "gate_action": "ask",
                    "runner_up": None, "model_version": self.version}
        p = self.proba_from_logits(self.logits_from_vector(vec))
        order = sorted(range(self.k), key=lambda j: (-p[j], j))
        top = round(p[order[0]], 3)
        return {
            "intent_class": self.classes[order[0]],
            "p": top,
            "gate_action": gate_for(top, self.accept_threshold, self.ask_threshold),
            "runner_up": self.classes[order[1]],
            "model_version": self.version,
        }


def load(version: str = MODEL_VERSION, artifacts_root: Path = ARTIFACTS_ROOT,
         thresholds_file: Path = THRESHOLDS_FILE, verify_sha256: bool = True) -> M2Model:
    """Read one artifact folder and the m2 thresholds. Raises ArtifactError on any mismatch."""
    import yaml  # lazy

    folder = Path(artifacts_root) / version
    meta_path, weights_path = folder / META_FILE, folder / WEIGHTS_FILE
    if not meta_path.exists() or not weights_path.exists():
        raise ArtifactError(f"artifact {folder} incomplete: run `python -m models.m2.train`")
    meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
    if meta.get("model_version") != version:
        raise ArtifactError(f"{meta_path}: model_version {meta.get('model_version')!r} != {version!r}")
    feats = meta.get("features") or {}
    if feats.get("version") != FEATURES_VERSION or int(feats.get("n_buckets", -1)) != N_BUCKETS:
        raise ArtifactError(f"{meta_path}: features {feats.get('version')}/{feats.get('n_buckets')} do not "
                            f"match this code ({FEATURES_VERSION}/{N_BUCKETS})")
    classes = tuple(meta.get("classes") or ())
    if set(classes) != set(CLASSES) or len(classes) != len(CLASSES):
        raise ArtifactError(f"{meta_path}: classes {classes} differ from {CLASSES}")
    raw = weights_path.read_bytes()
    wmeta = meta.get("weights") or {}
    if verify_sha256 and wmeta.get("sha256") and hashlib.sha256(raw).hexdigest() != wmeta["sha256"]:
        raise ArtifactError(f"{weights_path}: sha256 does not match meta.yaml")
    values = array("f")
    values.frombytes(raw)
    if sys.byteorder != "little":
        values.byteswap()
    k = len(classes)
    if len(values) != N_BUCKETS * k + k:
        raise ArtifactError(f"{weights_path}: {len(values)} floats, expected {N_BUCKETS * k + k}")
    bias = [float(v) for v in values[N_BUCKETS * k:]]
    weights = values[:N_BUCKETS * k]
    thr = read_thresholds(thresholds_file)
    if thr.get("model_version") not in (None, version):
        raise ArtifactError(f"thresholds.yaml m2 is for {thr.get('model_version')!r}, artifact is {version!r}")
    temperature = float((meta.get("calibration") or {}).get("temperature", 1.0))
    return M2Model(weights, bias, classes, temperature, float(thr["accept_threshold"]),
                   float(thr["ask_threshold"]), version, meta)


_lock = threading.Lock()
_model: M2Model | None = None


def get_model() -> M2Model:
    """The process-wide model, loaded on first use (thread-safe, double-checked)."""
    global _model
    m = _model
    if m is None:
        with _lock:
            if _model is None:
                _model = load()
            m = _model
    return m


def reset() -> None:
    """Forget the loaded model (tests; the next call loads it again)."""
    global _model
    with _lock:
        _model = None


def classify(text: str, language: str | None = None) -> dict[str, Any]:
    """INTERFACES.md #4: ``{intent_class, p, gate_action, runner_up, model_version}``.

    text: the REDACTED customer message. language: "es", "pt", "mixed" or None; accepted for the
        #4 signature but not used as a feature (customers mix languages; the character n-grams
        carry the language).
    p: calibrated probability of ``intent_class`` (temperature scaling), rounded to 3 decimals.
    gate_action: from p and thresholds.yaml (m2.accept_threshold, m2.ask_threshold).
    runner_up: the second most likely class (None only for a message with nothing to read).
    A message with nothing to read gives complaint_other, p = 0.111, gate "ask" (same as the
    keyword stand-in). Pure: no network, no I/O after the first load; never raises on any text.
    """
    return get_model().classify(text, language)


def predict_proba(text: str, calibrated: bool = True) -> dict[str, float]:
    """All nine class probabilities (for traces, evaluation and the clarifying question)."""
    return get_model().predict_proba(text, calibrated)
