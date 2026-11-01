"""M2: calibrated intent classifier, nine classes (INTERFACES.md #4; plan v2 section 5).

Public API (pure Python at runtime: standard library + PyYAML; no numpy/scikit-learn)::

    from models.m2 import classify
    classify("no reconozco un cargo de 450 en OXXO", "es")
    # {"intent_class": "dispute_charge", "p": 0.97, "gate_action": "accept",
    #  "runner_up": "dispute_fee", "model_version": "m2-hashlr-1"}

- ``classify(text, language=None)``: the #4 call (text already redacted).
- ``predict_proba(text, calibrated=True)``: all nine probabilities.
- ``gate_for(p, accept_threshold, ask_threshold)``: the gate rule.
- ``embed(text)``: the feature seam (hashed character/word n-grams; a multilingual embedding
  model would plug in here).
- ``get_model()`` / ``load(...)``: the loaded model (artifact + thresholds).

Training, calibration, thresholds and the evaluation report: ``python -m models.m2.train``
(see docs/modelos/m2.md). Everything M2 learns from is synthetic.
"""

from models.m2.features import embed
from models.m2.model import (
    CLASSES, MODEL_VERSION, ArtifactError, M2Model, classify, gate_for, get_model, load, predict_proba,
    read_thresholds, reset,
)

__all__ = [
    "CLASSES", "MODEL_VERSION", "ArtifactError", "M2Model", "classify", "embed", "gate_for", "get_model",
    "load", "predict_proba", "read_thresholds", "reset",
]
