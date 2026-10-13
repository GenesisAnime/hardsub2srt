# -*- coding: utf-8 -*-
"""Yerel arayüz: gövde üst sınırı ve yanıt sertleştirme başlıkları.

Rotaların çoğu kendi boyut sınırını koyar, ama tek tek eksik kalan yollarda
istek gövdesi sınırsız belleğe alınırdı. Küresel `MAX_CONTENT_LENGTH` bunu tek
yerde kapatır; bu testler hem sınırın **bağlı** olduğunu hem de en büyük meşru
gövdenin (AI yanıtı) hâlâ geçtiğini doğrular.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ui_server

BASE = f"http://127.0.0.1:{ui_server.PORT}"


class RequestSizeLimitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.video = self.root / "synthetic-video.mp4"
        self.video.write_bytes(b"synthetic placeholder; no decoder is run")
        self.client = ui_server.app.test_client()

    def test_global_body_limit_is_configured_for_the_largest_legitimate_body(self):
        self.assertEqual(ui_server.app.config["MAX_CONTENT_LENGTH"],
                         ui_server.MAX_ISTEK_BAYT)
        self.assertEqual(ui_server.MAX_ISTEK_BAYT, 8 * 1024 * 1024)
        # Meşru en büyük gövde: AI yanıtı (4 MiB) + istek kabuğu.
        self.assertGreater(ui_server.MAX_ISTEK_BAYT,
                           ui_server.ai_ocr_review.MAX_RESPONSE_BYTES + 4096)

    def test_oversized_body_is_rejected_with_413_before_the_view_runs(self):
        body = b"x" * (ui_server.MAX_ISTEK_BAYT + 1)
        response = self.client.post("/api/ekle", base_url=BASE, data=body,
                                    content_type="application/json")
        self.assertEqual(response.status_code, 413)

    def test_body_exactly_at_the_limit_is_still_accepted(self):
        payload = json.dumps({
            "yollar": [str(self.video)],
            "cikti_klasor": str(self.root / "out"),
        }).encode("utf-8")
        body = payload + b" " * (ui_server.MAX_ISTEK_BAYT - len(payload))
        self.assertEqual(len(body), ui_server.MAX_ISTEK_BAYT)

        with patch.object(ui_server, "ISLER", []):
            response = self.client.post("/api/ekle", base_url=BASE, data=body,
                                        content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.get_json()["eklendi"]), 1)


class ResponseHardeningHeaderTests(unittest.TestCase):
    def setUp(self):
        self.client = ui_server.app.test_client()

    def test_html_and_api_responses_are_not_sniffable(self):
        for path in ("/", "/api/durum"):
            with self.subTest(path=path):
                response = self.client.get(path, base_url=BASE)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers.get("X-Content-Type-Options"),
                                 "nosniff")
                self.assertEqual(response.headers.get("Referrer-Policy"),
                                 "no-referrer")

    def test_api_responses_are_not_cached(self):
        response = self.client.get("/api/durum", base_url=BASE)
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")

    def test_static_page_cache_header_is_not_forced(self):
        response = self.client.get("/", base_url=BASE)
        self.assertNotEqual(response.headers.get("Cache-Control"), "no-store")


if __name__ == "__main__":
    unittest.main()
