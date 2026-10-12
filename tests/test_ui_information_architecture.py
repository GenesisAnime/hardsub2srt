"""Synthetic checks for navigation, workflow hierarchy, and responsive UI rules."""

import unittest

import ui_server


class UiInformationArchitectureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = ui_server.app.test_client()

    def test_production_home_keeps_queue_primary_and_hides_debug_log_by_default(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)

        for label in ("Üretim", "İnceleme", "Kalite ve öğrenme", "İsteğe bağlı katkı"):
            self.assertIn(label, html)
        self.assertIn('id="video-kart"', html)
        self.assertIn('id="kuyruk-kart"', html)
        self.assertIn('href="/ocr-inceleme"', html)
        self.assertIn('href="/katki"', html)
        self.assertIn('id="opt-gpu" checked', html)
        self.assertIn('id="opt-cpu"', html)
        self.assertIn('id="opt-review-pack"', html)

        log_details = html.split('<details id="log-details"', 1)[1].split(">", 1)[0]
        self.assertNotIn(" open", log_details)
        results_details = html.split('<details id="sonuc-details"', 1)[1].split(">", 1)[0]
        self.assertNotIn(" open", results_details)

    def test_quality_view_keeps_queue_visible_and_has_a_return_to_production(self):
        response = self.client.get("/?view=kalite")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="kuyruk-kart"', html)
        self.assertIn('id="pano-kart"', html)
        self.assertIn('if (new URLSearchParams(location.search).get("view") === "kalite")', html)
        self.assertIn('href="/" id="nav-uretim"', html)
        self.assertIn('id="nav-kalite"', html)

    def test_review_page_guides_existing_steps_without_removing_safety_controls(self):
        response = self.client.get("/ocr-inceleme")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)

        step_ids = ["step-human", "step-ai-ocr", "step-ai-translation", "step-memory", "step-dataset"]
        positions = [html.index(f'id="{step_id}"') for step_id in step_ids]
        self.assertEqual(positions, sorted(positions))
        for element_id in (
            "path", "browse", "open", "cues", "ai-ocr-export", "ai-ocr-import",
            "ai-chat-export", "ai-import", "tm-add-approved", "tm-add-term",
            "srt-lint-run", "export", "metadata-template", "queue-status",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn("AI yanıtı yalnızca öneridir", html)
        self.assertIn("Kaynak belirsizse çeviri önerisi kabul edilemez", html)
        self.assertIn("/api/durum", html)
        self.assertIn('aria-label="İnceleme adımları"', html)

    def test_contribution_keeps_opt_in_and_shared_navigation(self):
        response = self.client.get("/katki")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        for label in ("Üretim", "İnceleme", "Kalite ve öğrenme", "İsteğe bağlı katkı"):
            self.assertIn(label, html)
        self.assertIn('id="queue-status"', html)
        self.assertIn('id="consent"', html)
        self.assertIn('id="send" disabled', html)
        self.assertIn("Önizleme oluşturmak ağ isteği göndermez", html)

    def test_each_page_defines_mobile_and_keyboard_focus_rules(self):
        for path in ("/", "/ocr-inceleme", "/katki"):
            with self.subTest(path=path):
                html = self.client.get(path).get_data(as_text=True)
                self.assertIn("@media", html)
                self.assertIn(":focus-visible", html)


if __name__ == "__main__":
    unittest.main()
