import json
import unittest
from datetime import datetime, timezone

from app import _get_dashboard_response


class DashboardResponseTests(unittest.TestCase):
    def test_response_contains_all_kindle_dashboard_fields(self) -> None:
        latest = [
            {
                "source": "deepseek",
                "metric": "balance",
                "value": 12.77,
                "unit": "CNY",
                "detail": json.dumps({"currency": "CNY"}),
                "timestamp": "2026-06-19T10:03:47+08:00",
            },
            {
                "source": "deepseek",
                "metric": "monthly_tokens",
                "value": 129792403,
                "unit": "tokens",
                "detail": json.dumps(
                    {
                        "requests": 1002,
                        "cost": 11.51,
                        "model": "deepseek-v4-pro",
                        "updated": "2026-06-19T09:30:02+08:00",
                    }
                ),
                "timestamp": "2026-06-19T09:30:02+08:00",
            },
            {
                "source": "codex",
                "metric": "rate_limit",
                "value": 78,
                "unit": "%",
                "detail": json.dumps(
                    {
                        "plan_type": "plus",
                        "primary": {
                            "window_minutes": 300,
                            "used_percent": 22,
                            "remaining_percent": 78,
                            "resets_at": 1781869198,
                        },
                        "secondary": {
                            "window_minutes": 10080,
                            "used_percent": 4,
                            "remaining_percent": 96,
                            "resets_at": 1782357606,
                        },
                    }
                ),
                "timestamp": "2026-06-19T09:48:54+08:00",
            },
            {
                "source": "clash",
                "metric": "traffic",
                "value": 33.37,
                "unit": "GB",
                "detail": json.dumps(
                    {
                        "total_bandwidth": 97.31,
                        "used_bandwidth": 33.37,
                        "remaining_bandwidth": 63.94,
                        "expiry": "2026-10-22",
                    }
                ),
                "timestamp": "2026-06-19T09:48:54+08:00",
            },
        ]

        data = _get_dashboard_response(latest)

        self.assertEqual(data["deepseek"]["balance"], 12.77)
        self.assertEqual(data["deepseek"]["monthlyTokens"], 129792403)
        self.assertEqual(data["deepseek"]["monthlyRequests"], 1002)
        self.assertEqual(data["deepseek"]["monthlyCost"], 11.51)
        self.assertTrue(data["codex"]["available"])
        self.assertFalse(data["codex"]["stale"])
        self.assertEqual(data["codex"]["primary"]["remainingPercent"], 78)
        self.assertEqual(data["codex"]["secondary"]["remainingPercent"], 96)
        primary_reset = 1781869198
        secondary_reset = 1782357606
        self.assertEqual(data["codex"]["primary"]["resetsAt"], primary_reset)
        self.assertEqual(data["codex"]["secondary"]["resetsAt"], secondary_reset)
        self.assertEqual(
            data["codex"]["primary"]["resetTime"],
            datetime.fromtimestamp(primary_reset, tz=timezone.utc)
            .astimezone()
            .strftime("%Y-%m-%d %H:%M:%S"),
        )
        self.assertEqual(
            data["codex"]["secondary"]["resetTime"],
            datetime.fromtimestamp(secondary_reset, tz=timezone.utc)
            .astimezone()
            .strftime("%Y-%m-%d %H:%M:%S"),
        )
        self.assertEqual(data["vpn"]["remainingBandwidth"], 63.94)
        self.assertEqual(data["vpn"]["expiryDate"], "2026-10-22")

    def test_missing_codex_record_is_explicitly_unavailable_and_stale(self) -> None:
        data = _get_dashboard_response([])

        self.assertFalse(data["codex"]["available"])
        self.assertTrue(data["codex"]["stale"])


if __name__ == "__main__":
    unittest.main()
