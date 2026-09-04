"""Continuous GNSS-aided INS ↔ DR state transitions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import time

import numpy as np


class NavigationMode(StrEnum):
    GNSS_AIDED_INS = "gnss_aided_ins"
    DEAD_RECKONING = "dead_reckoning"
    REACQUIRING = "reacquiring"


@dataclass(frozen=True)
class SwitchSample:
    mode: NavigationMode
    east_m: float
    north_m: float
    transition_latency_ms: float


class SeamlessModeSwitcher:
    """Blend a recovered GNSS position into the current DR state."""

    def __init__(self, *, reacquisition_seconds: float = 0.5) -> None:
        self.reacquisition_seconds = reacquisition_seconds
        self.mode = NavigationMode.GNSS_AIDED_INS
        self._reacquisition_started_s: float | None = None
        self._blend_origin: np.ndarray | None = None
        self._last_position = np.zeros(2, dtype=float)

    def update(self, timestamp_s: float, dr_position: np.ndarray, gnss_position: np.ndarray | None) -> SwitchSample:
        started = time.perf_counter_ns()
        dr = np.asarray(dr_position, dtype=float)
        if gnss_position is None:
            self.mode = NavigationMode.DEAD_RECKONING
            self._reacquisition_started_s, self._blend_origin = None, None
            output = dr
        else:
            gnss = np.asarray(gnss_position, dtype=float)
            if self.mode == NavigationMode.DEAD_RECKONING:
                self.mode = NavigationMode.REACQUIRING
                self._reacquisition_started_s, self._blend_origin = timestamp_s, dr.copy()
            if self.mode == NavigationMode.REACQUIRING:
                alpha = min(1.0, (timestamp_s - float(self._reacquisition_started_s)) / self.reacquisition_seconds)
                # Blend the *current* DR state toward GNSS. Anchoring to the
                # old loss point creates a delayed jump when alpha reaches 1.
                output = dr + alpha * (gnss - dr)
                if alpha >= 1.0:
                    self.mode = NavigationMode.GNSS_AIDED_INS
            else:
                output = gnss
        self._last_position = output
        return SwitchSample(self.mode, float(output[0]), float(output[1]), (time.perf_counter_ns() - started) / 1e6)
