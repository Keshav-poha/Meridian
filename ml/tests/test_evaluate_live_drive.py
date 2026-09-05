from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_live_drive.py"
SPEC = importlib.util.spec_from_file_location("evaluate_live_drive", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
EVALUATOR = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = EVALUATOR
SPEC.loader.exec_module(EVALUATOR)


def _sample(second: int, mode: str) -> dict[str, object]:
    latitude = 28.6139 + second * 0.00001
    return {
        "record_type": "sample",
        "schema_version": 1,
        "timestamp_s": 1000.0 + second,
        "mode": mode,
        "position": {
            "latitude_deg": latitude,
            "longitude_deg": 77.209,
            "east_m": None,
            "north_m": None,
            "heading_deg": 0.0,
            "reference_gnss": {
                "latitude_deg": latitude,
                "longitude_deg": 77.209,
            },
        },
        "motion": {},
        "gnss": {},
        "metrics": {"prediction_confidence": 0.8},
    }


class EvaluateLiveDriveTest(unittest.TestCase):
    def test_scores_manual_blackout_and_transition_events(self) -> None:
        records: list[dict[str, object]] = [
            {
                "record_type": "event",
                "event": "gnss_aiding_changed",
                "timestamp_s": 1000.0,
                "enabled": False,
            }
        ]
        records.extend(_sample(second, "dead_reckoning") for second in range(60))
        records.append(
            {
                "record_type": "event",
                "event": "gnss_aiding_changed",
                "timestamp_s": 1060.0,
                "enabled": True,
            }
        )
        records.append(_sample(60, "reacquiring"))

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            log = root / "drive.jsonl"
            log.write_text(
                "\n".join(json.dumps(record) for record in records) + "\n",
                encoding="utf-8",
            )
            samples, events = EVALUATOR.load_trip(log)
            intervals = EVALUATOR.blackout_intervals(samples, events)
            self.assertEqual(len(intervals), 1)
            metric = EVALUATOR.evaluate_window(samples, intervals[0], 10.0)
            self.assertEqual(metric["status"], "ok")
            self.assertEqual(metric["samples"], 11)
            self.assertAlmostEqual(metric["endpoint_error_m"], 0.0, places=5)
            latency = EVALUATOR.transition_latency_ms(samples, events)
            self.assertEqual(latency, {"loss": [0.0], "reacquisition": [0.0]})
            EVALUATOR.write_outputs(root / "report", [metric], latency)
            self.assertTrue((root / "report" / "live_drive_metrics.json").exists())
            self.assertTrue((root / "report" / "live_drive_windows.csv").exists())


if __name__ == "__main__":
    unittest.main()
