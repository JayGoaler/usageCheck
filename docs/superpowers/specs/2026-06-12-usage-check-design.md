# AI Usage Check - 设计文档

## 概述

一个常驻后台的 Web 服务，定时查询大模型订阅用量（OpenAI Codex 额度、DeepSeek 余额及 Token 用量）和科学上网工具剩余流量，通过浏览器仪表盘展示。支持桌面和 Kindle 浏览器访问。

## 技术栈

| 组件 | 选型 | 理由 |
|------|------|------|
| 语言 | Python 3.14 | 用户环境已安装 |
| Web 框架 | FastAPI | 原生 async，自动 API 文档 |
| 模板引擎 | Jinja2 | FastAPI 内置支持 |
| 定时调度 | APScheduler | 轻量，支持动态调整间隔 |
| 数据库 | SQLite (aiosqlite) | 单文件，零配置 |
| HTTP 客户端 | httpx | 原生 async |
| WebSocket | websockets | Clash 流量采集 |
| 前端 | 纯 HTML + 内联 CSS/JS | 无框架依赖，Kindle 兼容 |

## 项目结构

```
usageCheck/
├── app.py                    # FastAPI 应用入口
├── config.yaml               # 配置文件（API Keys 等）
├── requirements.txt          # 依赖声明
│
├── collectors/               # 数据采集模块
│   ├── __init__.py
│   ├── base.py              # 基类/接口定义
│   ├── openai_collector.py  # OpenAI Codex 额度查询
│   ├── deepseek_collector.py # DeepSeek 余额/Token 查询
│   └── clash_collector.py   # Clash WebSocket 流量查询
│
├── services/                 # 业务逻辑
│   ├── __init__.py
│   ├── scheduler.py         # APScheduler 定时调度
│   └── data_store.py        # SQLite 数据存储
│
├── web/                      # Web 前端
│   ├── templates/
│   │   └── index.html       # 单页仪表盘（内联 CSS/JS）
│   └── static/              # 可选静态资源
│
└── data/                     # 数据目录
    └── usage.db             # SQLite 数据库（运行时生成）
```

## 核心模块设计

### 数据采集 (collectors/)

所有 Collector 继承自 `BaseCollector`，统一接口：

```python
class BaseCollector(ABC):
    @abstractmethod
    async def collect(self) -> dict:
        """采集数据，返回标准化的字典"""

class OpenAICollector(BaseCollector):
    # 调用 OpenAI API 查询 Codex 额度
    # 端点: https://api.openai.com/v1/organization/usage
    # 返回: {credit_used, credit_remaining, credit_total}

class DeepSeekCollector(BaseCollector):
    # 调用 DeepSeek API 查询余额和 Token 用量
    # 返回: {balance, token_usage: {model: tokens}}

class ClashCollector(BaseCollector):
    # WebSocket 连接获取流量推送
    # 连接: ws://<host>:<port>/traffic
    # 返回: {upload, download, total, remaining}
```

### 定时调度 (services/scheduler.py)

- 使用 APScheduler 的 AsyncIOScheduler
- 默认每 5 分钟采集一次，通过 Web 界面可调整
- 每次采集并行执行所有 Collector
- 采集结果写入 SQLite 并通过 SSE 推送前端

### 数据存储 (services/data_store.py)

```sql
CREATE TABLE usage_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    source TEXT NOT NULL,       -- 'openai' / 'deepseek' / 'clash'
    metric TEXT NOT NULL,       -- 'credit' / 'balance' / 'token' / 'traffic'
    value REAL NOT NULL,
    unit TEXT NOT NULL,         -- 'USD' / 'tokens' / 'GB'
    detail TEXT                 -- JSON，存储额外信息（如模型级 Token 明细）
);

CREATE INDEX idx_history_source_ts ON usage_history(source, timestamp);
```

### Web 界面 (web/templates/index.html)

单页仪表盘，三列卡片布局：

```
┌─────────────────────────────────────────────────┐
│  📊 AI 使用量监控              🔄 最后更新: 10:30 │
├─────────────────────────────────────────────────┤
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐│
│  │  OpenAI     │ │  DeepSeek   │ │  科学上网    ││
│  │  Codex      │ │  余额       │ │  剩余流量    ││
│  │  ███████░░  │ │  ¥ 45.32   │ │  32.5 GB   ││
│  │  剩余 68%   │ │             │ │             ││
│  └─────────────┘ └─────────────┘ └─────────────┘│
│                                                 │
│  📈 Token 用量趋势（近 7 天折线图）              │
│                                                 │
│  ⚙️ 设置                                        │
│  刷新间隔: [5分钟 ▼]  [保存]                    │
│  API Key: [••••••••] [测试连接]                 │
└─────────────────────────────────────────────────┘
```

前端约束：
- 纯 HTML + 内联 CSS/JS，不依赖任何前端框架
- 通过 SSE (Server-Sent Events) 接收实时数据推送
- 定时轮询作为 SSE 的 fallback（兼容 Kindle 浏览器）
- 响应式布局，适配 Kindle Paperwhite 3；PW3 物理屏幕为 1072×1448，CSS 视口须实机测量，详见 `2026-06-19-kindle-paperwhite-3-dashboard-display-design.md`

### API 路由

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | / | 仪表盘页面 |
| GET | /api/status | 当前所有数据源最新状态 |
| GET | /api/history/{source} | 指定数据源历史记录 |
| GET | /api/events | SSE 实时推送端点 |
| POST | /api/config/interval | 更新采集间隔 |
| POST | /api/config/keys | 更新 API Keys |
| POST | /api/collect/now | 手动触发一次采集 |

## 配置管理

配置文件 `config.yaml`：

```yaml
openai:
  api_key: "${OPENAI_API_KEY}"   # 支持环境变量引用

deepseek:
  api_key: "${DEEPSEEK_API_KEY}"

clash:
  ws_url: "ws://localhost:9090/traffic"

scheduler:
  interval_minutes: 5

server:
  host: "0.0.0.0"
  port: 8080
```

- `${ENV_VAR}` 语法从环境变量读取，避免密钥明文存储
- API Key 也可通过 Web 界面配置，存入 SQLite 的 config 表

## 启动方式

```bash
# 安装依赖
pip install -r requirements.txt

# 启动服务
python app.py

# 浏览器访问
# 桌面: http://localhost:8080
# Kindle: http://<局域网IP>:8080
```

## 错误处理

- API 调用失败：记录错误日志，仪表盘显示"暂无数据"而非崩溃
- WebSocket 断开：自动重连（指数退避，最大间隔 5 分钟）
- 配置缺失：首次启动引导配置页面

## Kindle 兼容性

- 不使用 ES6+ 语法（Kindle 浏览器基于旧版 WebKit）
- 不使用 CSS Grid/Flexbox 高级特性
- SSE 不可用时 fallback 到定时轮询（每 60 秒）
- 页面采用流式单列布局，不将 PW3 固定为 600px；具体尺寸遵循 `2026-06-19-kindle-paperwhite-3-dashboard-display-design.md`
