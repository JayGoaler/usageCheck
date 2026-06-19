import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WindowsScriptTests(unittest.TestCase):
    def assert_crlf_only(self, name: str) -> None:
        data = (ROOT / name).read_bytes()
        self.assertIn(b"\r\n", data)
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))

    def test_start_bat_uses_crlf(self) -> None:
        self.assert_crlf_only("start.bat")

    def test_stop_bat_uses_crlf_and_exact_port_pattern(self) -> None:
        self.assert_crlf_only("stop.bat")
        text = (ROOT / "stop.bat").read_text(encoding="utf-8")
        self.assertIn('findstr /R /C:":8080 "', text)
        self.assertNotIn('findstr ":8080"', text)


if __name__ == "__main__":
    unittest.main()
