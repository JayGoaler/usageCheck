"""数据存储 - SQLite"""
import json
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from collectors.base import CollectResult


class DataStore:
    """管理 SQLite 数据存储"""

    def __init__(self, db_path: str = "data/usage.db") -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        """初始化数据库，创建表"""
        # 确保目录存在
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = await aiosqlite.connect(self._db_path)
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS usage_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                source TEXT NOT NULL,
                metric TEXT NOT NULL,
                value REAL NOT NULL,
                unit TEXT NOT NULL,
                detail TEXT
            )
        """)
        await self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_history_source_ts
            ON usage_history(source, timestamp)
        """)
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        await self._conn.commit()

    async def save_results(self, results: list[CollectResult]) -> None:
        """保存采集结果"""
        if not self._conn:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()
        for r in results:
            await self._conn.execute(
                "INSERT INTO usage_history (timestamp, source, metric, value, unit, detail) VALUES (?, ?, ?, ?, ?, ?)",
                (now, r.source, r.metric, r.value, r.unit, json.dumps(r.detail) if r.detail else None),
            )
        await self._conn.commit()

    async def get_latest(self, source: str | None = None) -> list[dict[str, Any]]:
        """获取每个数据源的最新记录"""
        if not self._conn:
            raise RuntimeError("Database not initialized")

        if source:
            rows = await self._conn.execute_fetchall(
                "SELECT * FROM usage_history WHERE source = ? ORDER BY id DESC LIMIT 10",
                (source,),
            )
        else:
            rows = await self._conn.execute_fetchall(
                """
                SELECT h.* FROM usage_history h
                INNER JOIN (
                    SELECT source, metric, MAX(id) AS max_id
                    FROM usage_history GROUP BY source, metric
                ) latest ON h.id = latest.max_id
                """
            )

        columns = ["id", "timestamp", "source", "metric", "value", "unit", "detail"]
        return [dict(zip(columns, row)) for row in rows]

    async def get_history(self, source: str, limit: int = 50) -> list[dict[str, Any]]:
        """获取指定数据源的历史记录"""
        if not self._conn:
            raise RuntimeError("Database not initialized")

        rows = await self._conn.execute_fetchall(
            "SELECT * FROM usage_history WHERE source = ? ORDER BY id DESC LIMIT ?",
            (source, limit),
        )

        columns = ["id", "timestamp", "source", "metric", "value", "unit", "detail"]
        return [dict(zip(columns, row)) for row in rows]

    async def get_config(self, key: str) -> str | None:
        """获取配置项"""
        if not self._conn:
            return None
        row = await self._conn.execute_fetchone(
            "SELECT value FROM config WHERE key = ?", (key,)
        )
        return row[0] if row else None

    async def set_config(self, key: str, value: str) -> None:
        """设置配置项"""
        if not self._conn:
            raise RuntimeError("Database not initialized")
        await self._conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
            (key, value),
        )
        await self._conn.commit()

    async def close(self) -> None:
        """关闭数据库连接"""
        if self._conn:
            await self._conn.close()
            self._conn = None
