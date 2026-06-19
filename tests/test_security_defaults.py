import unittest
from pathlib import Path

from app import app


ROOT = Path(__file__).resolve().parents[1]


class SecurityDefaultsTests(unittest.TestCase):
    def test_server_defaults_to_loopback(self) -> None:
        source = (ROOT / "app.py").read_text(encoding="utf-8")

        self.assertIn(
            'host=config.get("server", {}).get("host", "127.0.0.1")',
            source,
        )

    def test_api_keys_cannot_be_written_through_http(self) -> None:
        routes = {
            (route.path, method)
            for route in app.routes
            for method in getattr(route, "methods", set())
        }

        self.assertNotIn(("/api/config/keys", "POST"), routes)


if __name__ == "__main__":
    unittest.main()
