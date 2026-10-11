import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

import ocr_review
import ui_server
from review_bundle import build_review_pack


class OcrReviewRawEndDisplayTests(unittest.TestCase):
    def setUp(self):
        self.digest = "a" * 64
        self.metadata = {"status": "matched", "generation_srt_sha256": self.digest,
                         "clamp_count": 1}
        self.cue = {"start_ms": 1000, "end_ms": 2000, "raw_end_ms": 2100,
                    "clamp_delta_ms": 100.0,
                    "raw_end_time_basis":
                    "frame_index / reported FPS; approximate; source PTS not preserved"}

    def test_valid_hash_bound_clamp_renders_information_only_turkish_note(self):
        note = ocr_review._raw_end_note(self.cue, self.metadata, self.digest)
        self.assertEqual(
            note,
            "SRT yazılan bitiş 2000 ms; ham OCR bitişi 2100 ms "
            "(yaklaşık; frame/FPS tabanlı, kaynak PTS korunmadı); "
            "yazıcı 100 ms kısalttı.")

    def test_stale_missing_and_no_clamp_metadata_render_nothing(self):
        stale = {**self.metadata, "generation_srt_sha256": "b" * 64}
        no_clamp = {**self.metadata, "clamp_count": 0}
        for metadata in (None, {}, stale, no_clamp):
            with self.subTest(metadata=metadata):
                self.assertIsNone(ocr_review._raw_end_note(self.cue, metadata, self.digest))

    def test_untrusted_field_types_are_not_interpolated(self):
        bad = {**self.cue, "raw_end_ms": "<img src=x onerror=alert(1)>"}
        self.assertIsNone(ocr_review._raw_end_note(bad, self.metadata, self.digest))

    def test_review_page_renders_only_escaped_note_and_does_not_change_timing_inputs(self):
        client = ui_server.app.test_client()
        page = client.get("/ocr-inceleme").get_data(as_text=True)
        self.assertIn("const rawTiming=c.raw_end_note?`<div class=\"notice raw-end-info\">${esc(c.raw_end_note)}</div>`:''", page)
        self.assertIn("SRT yazılan bitiş", ocr_review._raw_end_note(self.cue, self.metadata, self.digest))
        self.assertIn("c.end_ms", page)
        self.assertIn("${rawTiming}", page)

    def test_synthetic_pack_status_exposes_note_only_for_bound_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            srt = Path(temporary) / "synthetic.srt"
            srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nSynthetic\n\n", encoding="utf-8")
            digest = hashlib.sha256(srt.read_bytes()).hexdigest()
            stats = {
                "main_srt_sha256": digest,
                "writer_end_clamps": {
                    "time_basis": "frame_index / reported FPS; approximate; source PTS not preserved",
                    "main": [{"cue_index": 0, "start_frame": 10,
                              "end_frame_inclusive": 20, "raw_end": "00:00:02,100",
                              "emitted_end": "00:00:02,000", "clamp_delta_ms": 100.0}],
                },
            }
            pack, _manifest = build_review_pack(
                srt,
                [{"start_ms": 1000, "end_ms": 2000, "text": "Synthetic",
                  "frame_index": 15, "frame_ms": 1500, "start_frame": 10,
                  "end_frame_inclusive": 20, "band_y": 2, "band_height": 12}],
                {}, lambda *_args: np.full((10, 32, 3), 80, dtype=np.uint8),
                generation_stats=stats)
            _review_id, opened = ocr_review.open_pack(str(pack))
            self.assertNotIn("raw_timing_metadata", opened)
            self.assertNotIn("srt_sha256_local", opened)
            self.assertNotIn(digest, json.dumps(opened))
            self.assertIn("ham OCR bitişi 2100 ms", opened["cues"][0]["raw_end_note"])

            manifest_path = pack / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["raw_timing_metadata"]["generation_srt_sha256"] = "0" * 64
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            _review_id, reopened = ocr_review.open_pack(str(pack))
            self.assertNotIn("raw_timing_metadata", reopened)
            self.assertNotIn("srt_sha256_local", reopened)
            self.assertIsNone(reopened["cues"][0]["raw_end_note"])


if __name__ == "__main__":
    unittest.main()
