import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hardsub2srt import _atomic_write_json, _run_timing_snapshot


class RunTimingTests(unittest.TestCase):
    def test_snapshot_is_json_serializable_and_rounds_monotonic_durations(self):
        timing = _run_timing_snapshot(
            100.0,
            {
                "first_extraction": 12.34567,
                "threshold_retry": 0,
                "not_run": None,
            },
            {
                "first_extraction": "completed",
                "threshold_retry": "not_triggered",
                "low_confidence_recovery": "no_candidates",
                "not_run": "not_requested",
            },
            now=125.6789,
        )

        serialized = json.dumps(timing, allow_nan=False)
        restored = json.loads(serialized)
        self.assertEqual(restored["clock"], "monotonic")
        self.assertEqual(restored["run_elapsed_seconds"], 25.679)
        self.assertEqual(restored["stage_durations_seconds"], {
            "first_extraction": 12.346,
            "threshold_retry": 0.0,
            "not_run": None,
        })
        self.assertEqual(restored["stage_status"], {
            "first_extraction": "completed",
            "threshold_retry": "not_triggered",
            "low_confidence_recovery": "no_candidates",
            "not_run": "not_requested",
        })
        self.assertTrue(restored["stage_durations_may_overlap"])

    def test_snapshot_clamps_negative_measurements_and_elapsed(self):
        timing = _run_timing_snapshot(
            5.0, {"clock_adjusted_stage": -2.0},
            {"clock_adjusted_stage": "completed"}, now=4.0)

        self.assertEqual(timing["run_elapsed_seconds"], 0.0)
        self.assertEqual(timing["stage_durations_seconds"], {
            "clock_adjusted_stage": 0.0,
        })

    def test_atomic_json_write_replaces_file_and_uses_no_leftover_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "run.stats.json"
            target.write_text('{"old": true}', encoding="utf-8")

            _atomic_write_json(target, {"timing": {"run_elapsed_seconds": 1.2}})

            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {
                "timing": {"run_elapsed_seconds": 1.2},
            })
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_atomic_json_replace_failure_preserves_prior_stats_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "run.stats.json"
            original = b'{"old": true}'
            target.write_bytes(original)

            with patch("hardsub2srt.os.replace", side_effect=OSError("blocked")):
                with self.assertRaisesRegex(OSError, "blocked"):
                    _atomic_write_json(target, {"timing": {"run_elapsed_seconds": 1.2}})

            self.assertEqual(target.read_bytes(), original)
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_atomic_temp_names_are_unique_across_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "run.stats.json"
            replaced_sources = []
            real_replace = os.replace

            def record_replace(source, destination):
                replaced_sources.append(Path(source).name)
                real_replace(source, destination)

            with patch("hardsub2srt.os.replace", side_effect=record_replace):
                _atomic_write_json(target, {"run": 1})
                _atomic_write_json(target, {"run": 2})

            self.assertEqual(len(replaced_sources), 2)
            self.assertNotEqual(replaced_sources[0], replaced_sources[1])


if __name__ == "__main__":
    unittest.main()
