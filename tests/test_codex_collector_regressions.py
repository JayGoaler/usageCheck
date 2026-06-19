import asyncio
import inspect
import unittest
from unittest.mock import AsyncMock, patch

from collectors.codex_collector import CodexQuotaCollector


class CodexCollectorRegressionTests(unittest.IsolatedAsyncioTestCase):
    def make_collector(self) -> CodexQuotaCollector:
        return CodexQuotaCollector(
            {
                "codex": {
                    "request_timeout_seconds": 15,
                    "initial_retry_count": 1,
                    "initial_retry_delay_seconds": 0,
                }
            }
        )

    def test_windows_subprocess_strategy_does_not_use_asyncio_subprocess(self) -> None:
        source = inspect.getsource(CodexQuotaCollector)
        self.assertIn("subprocess.Popen", source)
        self.assertNotIn("asyncio.create_subprocess_exec", source)
        self.assertIn("asyncio.get_running_loop()", source)
        self.assertIn("loop.call_soon_threadsafe", source)

    async def test_first_rate_limit_timeout_retries_once(self) -> None:
        collector = self.make_collector()
        collector._initialized = True
        response = {
            "rateLimits": [
                {
                    "limitId": "codex",
                    "primary": {"usedPercent": 22},
                    "secondary": {"usedPercent": 4},
                }
            ]
        }
        collector._rpc_call = AsyncMock(
            side_effect=[
                RuntimeError(
                    "RPC call 'account/rateLimits/read' timed out after 15s"
                ),
                response,
            ]
        )

        with patch("collectors.codex_collector.asyncio.sleep", new=AsyncMock()):
            results = await collector.collect()

        self.assertEqual(collector._rpc_call.await_count, 2)
        self.assertEqual(results[0].value, 78.0)

    async def test_non_timeout_error_is_not_retried(self) -> None:
        collector = self.make_collector()
        collector._initialized = True
        collector._rpc_call = AsyncMock(side_effect=RuntimeError("not logged in"))

        with self.assertRaisesRegex(RuntimeError, "not logged in"):
            await collector.collect()

        self.assertEqual(collector._rpc_call.await_count, 1)

    async def test_reset_rpc_state_rejects_pending_and_resets_sequence(self) -> None:
        collector = self.make_collector()
        collector._loop = asyncio.get_running_loop()
        pending = collector._loop.create_future()
        collector._pending[9] = pending
        collector._request_id = 9

        collector._reset_rpc_state("App Server restarted")
        await asyncio.sleep(0)

        self.assertEqual(collector._request_id, 0)
        self.assertEqual(collector._pending, {})
        with self.assertRaisesRegex(RuntimeError, "App Server restarted"):
            await pending

    def test_initialize_does_not_reserve_fixed_zero_id(self) -> None:
        source = inspect.getsource(CodexQuotaCollector._start)
        self.assertNotIn("rpc_id=0", source)


if __name__ == "__main__":
    unittest.main()
