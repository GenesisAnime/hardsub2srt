"""Synthetic local-origin guards for OCR seed review routes."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ocr_review
import ocr_seed_import
import ui_server


class SeedReviewRouteOriginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.synthetic_root = Path(self.temp.name)
        self.client = ui_server.app.test_client()
        self.base_url = f"http://127.0.0.1:{ui_server.PORT}"
        self.synthetic_packs = [{
            "pack_id": "synthetic-seed-pack-id",
            "episode_key": "Synthetic S01E01",
            "candidate_count": 1,
            "reviewed_count": 1,
        }]

    def tearDown(self):
        self.temp.cleanup()

    def test_seed_pack_list_rejects_hostile_host_before_catalog_access(self):
        with patch.object(ui_server, "_seed_review_storage_catalog",
                          side_effect=AssertionError("catalog must not be read")):
            response = self.client.get(
                "/api/ocr-inceleme/seed-paketleri", base_url=self.base_url,
                headers={"Host": "attacker.synthetic.test:8765"})
        self.assertEqual(response.status_code, 403)

    def test_seed_pack_list_accepts_localhost_with_synthetic_catalog(self):
        with patch.object(ui_server, "_seed_review_storage_catalog",
                          return_value=(self.synthetic_root, self.synthetic_packs)) as catalog:
            response = self.client.get(
                "/api/ocr-inceleme/seed-paketleri", base_url=self.base_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"packs": self.synthetic_packs})
        catalog.assert_called_once_with()

    def test_seed_pack_open_rejects_hostile_origin_before_storage_access(self):
        with patch.object(ui_server, "_seed_review_storage_catalog",
                          side_effect=AssertionError("catalog must not be read")):
            response = self.client.post(
                "/api/ocr-inceleme/seed-paket-ac", base_url=self.base_url,
                headers={"Origin": "https://attacker.synthetic.test"},
                json={"pack_id": "synthetic-seed-pack-id"})
        self.assertEqual(response.status_code, 403)

    def test_seed_pack_open_rejects_hostile_host_before_storage_access(self):
        with patch.object(ui_server, "_seed_review_storage_catalog",
                          side_effect=AssertionError("catalog must not be read")):
            response = self.client.post(
                "/api/ocr-inceleme/seed-paket-ac", base_url=self.base_url,
                headers={"Host": "attacker.synthetic.test:8765",
                         "Origin": self.base_url},
                json={"pack_id": "synthetic-seed-pack-id"})
        self.assertEqual(response.status_code, 403)

    def test_seed_pack_open_accepts_matching_localhost_with_synthetic_pack(self):
        synthetic_pack_path = self.synthetic_root / "synthetic.review-pack"
        synthetic_public_pack = {
            "cues": [{"cue_id": "synthetic-cue", "text": "Synthetic only"}],
        }
        with patch.object(ui_server, "_seed_review_storage_catalog",
                          return_value=(self.synthetic_root, self.synthetic_packs)), \
             patch.object(ocr_seed_import, "open_seed_review_pack",
                          return_value=synthetic_pack_path) as open_seed, \
             patch.object(ocr_review, "open_pack",
                          return_value=("synthetic-review-id", synthetic_public_pack)) as open_review:
            response = self.client.post(
                "/api/ocr-inceleme/seed-paket-ac", base_url=self.base_url,
                headers={"Origin": self.base_url},
                json={"pack_id": "synthetic-seed-pack-id"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {
            **synthetic_public_pack,
            "review_id": "synthetic-review-id",
        })
        open_seed.assert_called_once_with(self.synthetic_root, "synthetic-seed-pack-id")
        open_review.assert_called_once_with(str(synthetic_pack_path))


if __name__ == "__main__":
    unittest.main()
