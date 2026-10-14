from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHERS = ("cikar.bat", "toplu.bat")


class BatchLauncherExitStatusTests(unittest.TestCase):
    def test_launchers_report_failure_when_any_video_fails(self):
        for name in LAUNCHERS:
            with self.subTest(launcher=name):
                raw = (ROOT / name).read_bytes()
                self.assertIn(b"\r\n", raw)
                self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
                script = raw.decode("ascii")
                summary = script.split(":OZET", 1)[1].split(
                    "rem ---- yardimci", 1)[0]

                self.assertIn("if !HATALI! GTR 0 exit /b 1\r\n", summary)
                self.assertIn("exit /b 0\r\n", summary)
                self.assertLess(
                    summary.index("if !HATALI! GTR 0 exit /b 1"),
                    summary.index("exit /b 0"),
                )
                self.assertIn("set /a HATALI+=1", script)
                self.assertIn("if errorlevel 1 (", script)


if __name__ == "__main__":
    unittest.main()
