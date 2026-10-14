# -*- coding: utf-8 -*-
"""Geçersiz arayüz gövdesi 500 değil 400 üretmeli (kuyruk ve editör API'leri).

Ölçüldü (13.10.2026, Flask test istemcisi):

- ``POST /api/ekle`` gövdesinde ``qa_sayi: "abc"`` → ``int()`` ValueError ile
  **HTTP 500**; ``limit_saniye: "soon"`` aynı şekilde 500.
- JSON *dizesi* gövdesi (``"abc"``) ``or {}`` deyimini atlar (truthy) ve
  ``/api/ekle``, ``/api/klasortara``, ``/api/ac``, ``/api/ogret`` rotalarında
  ``AttributeError: 'str' object has no attribute 'get'`` ile **500** üretir.
- Boş gövdeyle ``/api/ac`` sessizce ``Path("")`` → sunucunun kendi klasörünü
  Explorer'da açar.

Bu testler arayüz kullanıcısının gördüğü sözleşmeyi sabitler: bozuk istek
**400**, hiçbir iş kuyruğa girmez ve hiçbir alt süreç (Explorer) başlamaz.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ui_server

BASE = f"http://127.0.0.1:{ui_server.PORT}"
NON_OBJECT_PATHS = ("/api/ekle", "/api/klasortara", "/api/ac", "/api/ogret")


class QueueOptionValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.video = self.root / "synthetic-video.mp4"
        self.video.write_bytes(b"synthetic placeholder; no decoder is run")
        self.client = ui_server.app.test_client()

    def _post(self, path, body):
        data = body if isinstance(body, str) else json.dumps(body)
        headers = {"Host": f"127.0.0.1:{ui_server.PORT}", "Origin": BASE}
        return self.client.post(path, base_url=BASE, headers=headers, data=data,
                                content_type="application/json")

    def _ekle(self, body):
        """İş kuyruğunu izole ederek /api/ekle çağrısı yapar."""
        with patch.object(ui_server, "ISLER", []) as isler:
            response = self._post("/api/ekle", body)
        return response, isler

    def _ekle_body(self, **overrides):
        body = {"yollar": [str(self.video)], "cikti_klasor": str(self.root / "out")}
        body.update(overrides)
        return body

    def test_non_numeric_integer_option_is_rejected_without_queueing(self):
        for value in ("abc", True, -1, ui_server.MAX_QA_SAYISI + 1, 1.5, [4]):
            with self.subTest(qa_sayi=value):
                response, isler = self._ekle(self._ekle_body(qa_sayi=value))
                self.assertEqual(response.status_code, 400)
                self.assertIn("qa_sayi", response.get_json()["hata"])
                self.assertEqual(list(isler), [])

    def test_non_finite_or_negative_limit_is_rejected_without_queueing(self):
        # 1e999 ham JSON'da geçerli bir sayıdır ve Python'da float('inf') olur.
        # Yol ters bölü içerdiği için JSON olarak kodlanır (ham f-string bozuk JSON
        # üretir ve gövde ayrıştırılamaz → yanlış hata mesajı).
        for value in ('"soon"', "-1", "1e999"):
            with self.subTest(limit_saniye=value):
                body = ('{"yollar": [%s], "limit_saniye": %s}'
                        % (json.dumps(str(self.video)), value))
                response, isler = self._ekle(body)
                self.assertEqual(response.status_code, 400)
                self.assertIn("limit_saniye", response.get_json()["hata"])
                self.assertEqual(list(isler), [])

    def test_valid_options_are_accepted_with_defaults_preserved(self):
        # Sayısal metin de kabul edilir (eski davranış korunuyor: arayüz
        # gövdesi metin gönderirse değer ayrıştırılır).
        response, _isler = self._ekle(
            self._ekle_body(qa_sayi="12", limit_saniye="90.5"))
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["sec"]["qa_sayi"], 12)
        self.assertEqual(payload["sec"]["limit_saniye"], 90.5)
        self.assertEqual(len(payload["eklendi"]), 1)

        # Alanlar verilmezse eski varsayılanlar korunur (qa 4, limit 0).
        defaults, _isler = self._ekle(self._ekle_body())
        self.assertEqual(defaults.status_code, 200)
        sec = defaults.get_json()["sec"]
        self.assertEqual(sec["qa_sayi"], 4)
        self.assertEqual(sec["limit_saniye"], 0.0)

    def test_non_object_json_body_is_rejected_instead_of_raising(self):
        for path in NON_OBJECT_PATHS:
            for body in ('"abc"', "[]", "0"):
                with self.subTest(path=path, body=body):
                    with patch.object(ui_server.subprocess, "Popen") as popen:
                        response = self._post(path, body)
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response.get_json()["hata"],
                                     "JSON nesnesi gerekli")
                    popen.assert_not_called()

    def test_empty_open_path_is_rejected_before_starting_explorer(self):
        with patch.object(ui_server.subprocess, "Popen") as popen:
            response = self._post("/api/ac", {})
        self.assertEqual(response.status_code, 400)
        self.assertIn("yol", response.get_json()["hata"])
        popen.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows seçicisi gerektirir")
    def test_picker_rejects_non_object_body_before_starting_helpers(self):
        with patch.object(ui_server.subprocess, "Popen") as popen:
            response = self._post("/api/secim", '"abc"')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["hata"], "JSON nesnesi gerekli")
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
