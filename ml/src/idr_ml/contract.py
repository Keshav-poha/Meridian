"""Load the language-neutral feature contract without duplicating constants."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def feature_spec_path() -> Path:
    return repository_root() / "shared" / "config" / "feature_spec.json"


def load_feature_spec() -> dict[str, Any]:
    with feature_spec_path().open(encoding="utf-8") as stream:
        return json.load(stream)


def expected_window_samples(spec: dict[str, Any]) -> int:
    time = spec["time"]
    return round(float(time["window_seconds"]) * int(time["sample_rate_hz"]))
