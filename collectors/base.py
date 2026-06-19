from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class CollectResult:
    """采集结果标准化格式"""
    source: str          # 'openai' / 'deepseek' / 'clash'
    metric: str          # 'credit' / 'balance' / 'token' / 'traffic'
    value: float
    unit: str            # 'USD' / 'tokens' / 'GB'
    detail: dict | None = None  # 额外信息（如模型级 Token 明细）


class BaseCollector(ABC):
    """采集器基类"""

    @abstractmethod
    async def collect(self) -> list[CollectResult]:
        """采集数据，返回一组标准化结果"""
        ...
