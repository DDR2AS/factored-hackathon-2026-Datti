"""Shared runtime helpers for the models. Standard library only (they run inside the chat Lambda).

- ``LgbmModel``: evaluates a LightGBM model saved in its native text format (``model.txt``) in pure
  Python, so the Lambda needs neither lightgbm nor numpy. ``tests/test_m1.py`` checks it gives the
  same scores as ``lightgbm.Booster.predict``.
- ``Isotonic``: a fitted isotonic regression stored as breakpoints (piecewise linear).
- ``thresholds``: ``src/models/thresholds.yaml`` (one top-level key per model; each writer only
  replaces its own key).
"""

from __future__ import annotations

import math
import re
import threading
import unicodedata
from bisect import bisect_right
from pathlib import Path
from typing import Any

import yaml

MODELS_DIR = Path(__file__).resolve().parent
ARTIFACTS_DIR = MODELS_DIR / "artifacts"
THRESHOLDS_FILE = MODELS_DIR / "thresholds.yaml"
USD_RATES = {"USD": 1.0, "COP": 4000.0, "ARS": 350.0, "MXN": 17.0, "BRL": 5.0}

_thr_lock = threading.Lock()


def fold(text: str | None) -> str:
    """Lowercase, no accents, letters and digits separated by single spaces."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def to_usd(amount: float | None, currency: str | None) -> float | None:
    if amount is None:
        return None
    rate = USD_RATES.get((currency or "").upper())
    return None if not rate else float(amount) / rate


# ---------------------------------------------------------------- thresholds.yaml

def read_thresholds(path: Path = THRESHOLDS_FILE) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def write_threshold_section(key: str, section: dict, path: Path = THRESHOLDS_FILE) -> None:
    """Replace ONE top-level key, keeping the others (M1, M2 and M3 share the file)."""
    with _thr_lock:
        data = read_thresholds(path)
        data[key] = section
        header = ("# Thresholds of M1, M2 and M3 (INTERFACES.md #4). Written by each model's training script\n"
                  "# (make train); chosen on validation data, never by hand. Each writer replaces only its own key.\n")
        path.write_text(header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


# ---------------------------------------------------------------- isotonic regression (piecewise linear)

class Isotonic:
    def __init__(self, xs: list[float], ys: list[float]) -> None:
        if len(xs) != len(ys) or not xs:
            raise ValueError("isotonic: xs and ys must be non-empty and the same length")
        self.xs, self.ys = list(map(float, xs)), list(map(float, ys))

    def __call__(self, x: float) -> float:
        xs, ys = self.xs, self.ys
        if x <= xs[0]:
            return ys[0]
        if x >= xs[-1]:
            return ys[-1]
        i = bisect_right(xs, x)
        x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
        return y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)

    def to_dict(self) -> dict:
        return {"xs": [round(v, 6) for v in self.xs], "ys": [round(v, 6) for v in self.ys]}

    @classmethod
    def from_dict(cls, d: dict) -> "Isotonic":
        return cls(d["xs"], d["ys"])


# ---------------------------------------------------------------- LightGBM text model, pure Python

class _Tree:
    __slots__ = ("feat", "thr", "dtype", "left", "right", "leaf", "shrinkage")

    def __init__(self, block: dict[str, str]) -> None:
        n_leaves = int(block["num_leaves"])
        self.leaf = [float(v) for v in block["leaf_value"].split()]
        if n_leaves == 1:
            self.feat = self.thr = self.dtype = self.left = self.right = []
        else:
            self.feat = [int(v) for v in block["split_feature"].split()]
            self.thr = [float(v) for v in block["threshold"].split()]
            self.dtype = [int(v) for v in block["decision_type"].split()]
            self.left = [int(v) for v in block["left_child"].split()]
            self.right = [int(v) for v in block["right_child"].split()]

    def value(self, x: list[float]) -> float:
        if not self.feat:
            return self.leaf[0]
        node = 0
        while node >= 0:
            fval = x[self.feat[node]]
            dt = self.dtype[node]
            default_left = bool(dt & 2)
            missing_type = (dt >> 2) & 3  # 0 none, 1 zero, 2 nan
            if fval is None or (isinstance(fval, float) and math.isnan(fval)):
                if missing_type == 2:
                    node = self.left[node] if default_left else self.right[node]
                    continue
                fval = 0.0
            if missing_type == 1 and abs(fval) <= 1e-35:
                node = self.left[node] if default_left else self.right[node]
                continue
            node = self.left[node] if fval <= self.thr[node] else self.right[node]
        return self.leaf[~node]


class LgbmModel:
    """A LightGBM model read from its text dump (``Booster.save_model``)."""

    def __init__(self, text: str) -> None:
        self.trees: list[_Tree] = []
        self.feature_names: list[str] = []
        header, _, rest = text.partition("Tree=")
        for line in header.splitlines():
            if line.startswith("feature_names="):
                self.feature_names = line.split("=", 1)[1].split()
        blocks = ("Tree=" + rest).split("Tree=")[1:]
        for b in blocks:
            body = b.split("end of trees")[0]
            kv = {}
            for line in body.splitlines()[1:]:
                if "=" in line:
                    k, v = line.split("=", 1)
                    kv[k.strip()] = v.strip()
            if "leaf_value" in kv:
                self.trees.append(_Tree(kv))

    @classmethod
    def load(cls, path: Path) -> "LgbmModel":
        return cls(Path(path).read_text(encoding="utf-8"))

    def predict(self, x: list[float]) -> float:
        return sum(t.value(x) for t in self.trees)


def nan_if_none(v: Any) -> float:
    return float("nan") if v is None else float(v)
