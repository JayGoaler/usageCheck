"""DeepSeek 余额及 Token 用量查询"""
from collections.abc import MutableMapping, Sequence
from typing import Any

import httpx

from .base import BaseCollector, CollectResult


class DeepSeekCollector(BaseCollector):
    """通过 DeepSeek API 查询账户信息"""

    BASE_URL = "https://api.deepseek.com"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._headers: dict[str, str] = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    async def collect(self) -> list[CollectResult]:
        """查询 DeepSeek 余额和 Token 用量"""
        results: list[CollectResult] = []

        async with httpx.AsyncClient(base_url=self.BASE_URL, headers=self._headers, timeout=30) as client:
            # 余额查询 — GET /user/balance
            balance_resp = await client.get("/user/balance")
            balance_resp.raise_for_status()
            balance_data: MutableMapping[str, Any] = balance_resp.json()

            # balance_infos 是数组，取第一个
            balance_infos: list[MutableMapping[str, Any]] | None = balance_data.get("balance_infos")
            if balance_infos:
                info = balance_infos[0]
                balance = float(info.get("total_balance", 0))
                currency = info.get("currency", "CNY")
                granted = float(info.get("granted_balance", 0))
                topped_up = float(info.get("topped_up_balance", 0))

                results.append(
                    CollectResult(
                        source="deepseek",
                        metric="balance",
                        value=balance,
                        unit=currency,
                        detail={
                            "currency": currency,
                            "granted_balance": granted,
                            "topped_up_balance": topped_up,
                        },
                    )
                )
            else:
                results.append(
                    CollectResult(
                        source="deepseek",
                        metric="balance",
                        value=0,
                        unit="CNY",
                    )
                )

        return results