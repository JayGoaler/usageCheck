import json
import unittest
from datetime import datetime, time, timezone

from collectors.deepseek_usage_collector import DeepSeekUsageCollector


class FakeDataStore:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    async def get_latest(self, source: str | None = None) -> list[dict]:
        return self.rows


class DeepSeekUsageCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_utc_timestamp_is_cached_by_local_calendar_day(self) -> None:
        local_timestamp = datetime.combine(
            datetime.now().astimezone().date(),
            time(hour=0, minute=30),
        ).astimezone()
        utc_timestamp = local_timestamp.astimezone(timezone.utc).isoformat()
        detail = {
            "model": "deepseek-v4-pro",
            "requests": 1194,
            "cost": 12.85,
            "updated": utc_timestamp,
        }
        store = FakeDataStore(
            [
                {
                    "source": "deepseek",
                    "metric": "monthly_tokens",
                    "value": 147_700_000,
                    "unit": "tokens",
                    "detail": json.dumps(detail),
                    "timestamp": utc_timestamp,
                }
            ]
        )
        collector = DeepSeekUsageCollector({}, store)

        cached = await collector._is_today_cached()

        self.assertIsNotNone(cached)
        self.assertEqual(cached[0]["detail"], json.dumps(detail))


if __name__ == "__main__":
    unittest.main()
