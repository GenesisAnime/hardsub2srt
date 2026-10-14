# -*- coding: utf-8 -*-
"""ffprobe'un kısmi/bozuk çıktısı ham traceback üretmemeli.

Ölçüldü (13.10.2026, bu makinede ffmpeg ile üretilen dosyalarla):

* `ffmpeg -f h264 out.h264` çıktısında `format.duration` **yoktur** ve akış
  düzeyinde `duration`/`nb_frames` alanı da **yoktur** →
  `python hardsub2srt.py out.h264 -o x.srt` eski kodda
  `KeyError: 'duration'` traceback'i basıyordu.
* 900 bayta kırpılmış mp4 → ffprobe rc=1, stdout `{}`, stderr
  "moov atom not found" → eski kodda `CalledProcessError` traceback'i.
* `avg_frame_rate` "0/0" bildirilebilir → `float(num)/float(den)`
  `ZeroDivisionError` riski.

Testler ffprobe'u çalıştırmaz; ölçülen gerçek çıktılar sabit olarak verilir
(medya/model bağımlılığı olmasın, CI'da deterministik kalsın).

Ayrıca `main()` kullanıcıya traceback yerine tek satırlık hata verir.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import hardsub2srt

# Gerçek ffprobe çıktısı (ham H.264 ilköğretim akışı, ffmpeg ile üretildi):
# format.duration yok, akışta duration/nb_frames yok.
RAW_H264_STREAM = {
    "index": 0, "codec_name": "h264", "codec_type": "video",
    "width": 128, "height": 72, "pix_fmt": "yuv420p",
    "avg_frame_rate": "25/1", "r_frame_rate": "50/1", "time_base": "1/50",
}
RAW_H264_FORMAT = {
    "filename": "synthetic.h264", "format_name": "h264",
    "format_long_name": "raw H.264 video", "nb_streams": 1, "size": "6145",
}

# Gerçek kırpılmış mp4: rc=1, stdout "{}", stderr iki satır.
TRUNCATED_STDERR = (
    "[mov,mp4,m4a,3gp,3g2,mj2 @ 000001797a258700] moov atom not found\n"
    "synthetic-truncated.mp4: Invalid data found when processing input\n"
)


def _probe_stub(payload, returncode=0, stderr=""):
    """subprocess.run yerine geçen sahte ffprobe çağrısı."""
    def _run(_args, **_kwargs):
        return hardsub2srt.subprocess.CompletedProcess(
            "ffprobe", returncode, json.dumps(payload), stderr)
    return _run


class FfprobeMetadataTests(unittest.TestCase):
    def test_raw_elementary_stream_reports_missing_duration_cleanly(self):
        payload = {"streams": [RAW_H264_STREAM], "format": RAW_H264_FORMAT}
        with patch.object(hardsub2srt.subprocess, "run", _probe_stub(payload)):
            with self.assertRaises(hardsub2srt.VideoOkumaHatasi) as caught:
                hardsub2srt.run_ffprobe("synthetic.h264")
        self.assertIn("süre bilgisi yok", str(caught.exception))

    def test_truncated_container_reports_ffprobe_error_without_traceback(self):
        with patch.object(hardsub2srt.subprocess, "run",
                          _probe_stub({}, returncode=1, stderr=TRUNCATED_STDERR)):
            with self.assertRaises(hardsub2srt.VideoOkumaHatasi) as caught:
                hardsub2srt.run_ffprobe("synthetic-truncated.mp4")
        message = str(caught.exception)
        self.assertIn("okunamadı", message)
        self.assertIn("Invalid data found when processing input", message)

    def test_empty_json_object_is_reported_instead_of_key_error(self):
        with patch.object(hardsub2srt.subprocess, "run", _probe_stub({})):
            with self.assertRaises(hardsub2srt.VideoOkumaHatasi) as caught:
                hardsub2srt.run_ffprobe("synthetic.mp4")
        self.assertIn("akış listesi", str(caught.exception))

    def test_stream_duration_is_used_when_format_duration_is_absent(self):
        stream = dict(RAW_H264_STREAM, duration="2.000000")
        payload = {"streams": [stream], "format": {"format_name": "matroska"}}
        with patch.object(hardsub2srt.subprocess, "run", _probe_stub(payload)):
            info = hardsub2srt.run_ffprobe("synthetic.mkv")
        self.assertEqual(info["duration"], 2.0)
        self.assertEqual(info["fps"], 25.0)

    def test_duration_falls_back_to_frame_count_and_fps(self):
        stream = dict(RAW_H264_STREAM, nb_frames="50")
        payload = {"streams": [stream], "format": {"format_name": "matroska"}}
        with patch.object(hardsub2srt.subprocess, "run", _probe_stub(payload)):
            info = hardsub2srt.run_ffprobe("synthetic.mkv")
        self.assertEqual(info["duration"], 2.0)

    def test_degenerate_frame_rate_falls_back_to_r_frame_rate(self):
        stream = dict(RAW_H264_STREAM, avg_frame_rate="0/0",
                      duration="2.000000")
        payload = {"streams": [stream], "format": {"format_name": "matroska"}}
        with patch.object(hardsub2srt.subprocess, "run", _probe_stub(payload)):
            info = hardsub2srt.run_ffprobe("synthetic.mkv")
        self.assertEqual(info["fps"], 50.0)

    def test_unusable_frame_rate_is_reported(self):
        stream = dict(RAW_H264_STREAM, avg_frame_rate="0/0",
                      r_frame_rate="0/0", duration="2.000000")
        payload = {"streams": [stream], "format": {"format_name": "matroska"}}
        with patch.object(hardsub2srt.subprocess, "run", _probe_stub(payload)):
            with self.assertRaises(hardsub2srt.VideoOkumaHatasi) as caught:
                hardsub2srt.run_ffprobe("synthetic.mkv")
        self.assertIn("kare hızı", str(caught.exception))

    def test_audio_only_input_is_reported(self):
        payload = {"streams": [{"index": 0, "codec_type": "audio",
                                "codec_name": "aac"}],
                   "format": {"duration": "2.000000"}}
        with patch.object(hardsub2srt.subprocess, "run", _probe_stub(payload)):
            with self.assertRaises(hardsub2srt.VideoOkumaHatasi) as caught:
                hardsub2srt.run_ffprobe("synthetic.m4a")
        self.assertIn("video akışı yok", str(caught.exception))

    def test_incomplete_subtitle_stream_metadata_is_a_controlled_error(self):
        for missing in ("index", "codec_name"):
            with self.subTest(missing=missing):
                subtitle = {"index": 1, "codec_name": "subrip",
                            "codec_type": "subtitle"}
                del subtitle[missing]
                payload = {
                    "streams": [dict(RAW_H264_STREAM, duration="2.000000"), subtitle],
                    "format": {"duration": "2.000000"},
                }
                with patch.object(hardsub2srt.subprocess, "run", _probe_stub(payload)):
                    with self.assertRaises(hardsub2srt.VideoOkumaHatasi) as caught:
                        hardsub2srt.run_ffprobe("synthetic.mkv")
                self.assertIn("altyazı akışı eksik ffprobe bilgisi", str(caught.exception))

    def test_healthy_probe_result_is_unchanged(self):
        payload = {
            "streams": [dict(RAW_H264_STREAM, avg_frame_rate="24000/1001",
                             nb_frames="48"),
                        {"index": 1, "codec_type": "subtitle",
                         "codec_name": "subrip", "tags": {"language": "tur"}}],
            "format": {"duration": "2.002000", "format_name": "matroska"},
        }
        with patch.object(hardsub2srt.subprocess, "run", _probe_stub(payload)):
            info = hardsub2srt.run_ffprobe("synthetic.mkv")
        self.assertEqual(info["duration"], 2.002)
        self.assertEqual(info["nb_frames"], 48)
        self.assertEqual(info["width"], 128)
        self.assertEqual(info["height"], 72)
        self.assertEqual(info["subtitles"],
                         [{"index": 1, "codec": "subrip", "lang": "tur"}])


class MainErrorPathTests(unittest.TestCase):
    def test_main_exits_with_one_line_message_for_unreadable_video(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "synthetic-unreadable.mp4"
            video.write_bytes(b"synthetic placeholder; ffprobe is stubbed")

            def _fail(_path):
                raise hardsub2srt.VideoOkumaHatasi("sentetik ffprobe hatası")

            stderr, stdout = io.StringIO(), io.StringIO()
            with patch.object(hardsub2srt, "run_ffprobe", _fail), \
                    redirect_stderr(stderr), redirect_stdout(stdout):
                with self.assertRaises(SystemExit) as caught:
                    hardsub2srt.main([str(video), "-o", str(root / "out.srt")])

        self.assertIn("video okunamadı", str(caught.exception))
        self.assertIn("sentetik ffprobe hatası", str(caught.exception))
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_main_surfaces_partial_subtitle_metadata_without_traceback(self):
        for missing in ("index", "codec_name"):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                video = root / "synthetic-partial-subtitle.mkv"
                video.write_bytes(b"synthetic placeholder; ffprobe is stubbed")
                subtitle = {"index": 1, "codec_name": "subrip",
                            "codec_type": "subtitle"}
                del subtitle[missing]
                payload = {
                    "streams": [dict(RAW_H264_STREAM, duration="2.000000"), subtitle],
                    "format": {"duration": "2.000000"},
                }
                stderr, stdout = io.StringIO(), io.StringIO()
                with patch.object(hardsub2srt.subprocess, "run", _probe_stub(payload)), \
                        redirect_stderr(stderr), redirect_stdout(stdout):
                    with self.assertRaises(SystemExit) as caught:
                        hardsub2srt.main([str(video), "-o", str(root / "out.srt")])
                self.assertIn("video okunamadı", str(caught.exception))
                self.assertIn("altyazı akışı eksik ffprobe bilgisi", str(caught.exception))
                self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
