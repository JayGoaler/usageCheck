# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## 项目简介

AI Usage Check - 大模型订阅用量监控工具。常驻后台 Web 服务，定时查询 OpenAI Codex 额度、DeepSeek 余额/Token 用量和科学上网工具剩余流量，通过浏览器仪表盘展示（支持桌面和 Kindle 浏览器访问）。

## 技术栈

- **后端**: Python 3.14, FastAPI, APScheduler, aiosqlite
- **前端**: 纯 HTML + 内联 CSS/JS（无框架，Kindle 兼容）
- **数据存储**: SQLite
- **API 对接**: httpx (HTTP), websockets (WebSocket)

## 常用命令

```bash
# 安装依赖
pip install -r requirements.txt

# 启动开发服务器（热重载）
python app.py

# 单独运行检查
python -c "import app; import asyncio; asyncio.run(app.run_collect())"
```

## 项目结构

```
├── app.py                    # FastAPI 入口 + 路由
├── config.yaml               # 配置（API Keys 等）
├── requirements.txt          # 依赖声明
├── collectors/               # 数据采集
│   ├── base.py              # AbstractCollector 基类
│   ├── openai_collector.py  # OpenAI Codex 额度
│   ├── deepseek_collector.py # DeepSeek 余额/Token
│   └── clash_collector.py   # Clash 流量
├── services/                 # 业务服务
│   ├── scheduler.py         # APScheduler 定时调度
│   └── data_store.py        # SQLite 读写
├── web/                      # 前端
│   ├── templates/index.html  # 仪表盘（单页）
│   └── static/               # 静态资源
└── data/                     # 运行时数据
    └── usage.db             # SQLite 数据库（自动生成）
```

## 架构要点

- **采集器模式**: 每个数据源实现 `BaseCollector` 接口，`collect()` 返回 `list[CollectResult]`
- **定时调度**: APScheduler AsyncIOScheduler，运行期间可动态调整间隔
- **数据流**: Collector → DataStore(SQLite) → SSE/API → 前端
- **配置**: config.yaml（支持 `${ENV_VAR}` 引用环境变量）
- **错误隔离**: 单个采集器失败不影响其他采集

## API 路由

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | / | 仪表盘页面 |
| GET | /api/status | 最新状态 |
| GET | /api/history/{source} | 历史记录 |
| GET | /api/events | SSE 推送 |
| POST | /api/config/interval | 更新间隔 |
| POST | /api/config/keys | 更新 API Keys |
| POST | /api/collect/now | 手动采集 |
