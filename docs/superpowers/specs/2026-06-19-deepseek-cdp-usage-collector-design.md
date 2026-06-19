# DeepSeek CDP 月度用量 + Codex App Server 额度采集 & 前端重写

**日期:** 2026-06-19
**状态:** 已批准

参考：
- DeepSeek CDP：`deepseek-balance-monitor/update_data.py`
- Codex App Server：`docs/superpowers/specs/2026-06-19-codex-app-server-quota-collector-design.md`

---

## 1. 背景

### 1.1 DeepSeek 月度用量

DeepSeek API 仅提供余额查询 (`GET /user/balance`) 和单次请求的 `usage` 字段，**不提供**月度聚合用量 API。需通过 Chrome DevTools Protocol (CDP) 抓取 `platform.deepseek.com/usage` 网页。

### 1.2 Codex 订阅额度

ChatGPT Codex 订阅额度与 OpenAI API 组织账单是两个不同数据域。当前 `openai_collector.py` 查询的是 OpenAI API 组织费用，不等同于 Codex 桌面端订阅余量。需通过 `codex app-server --stdio` JSON-RPC 复用本机 Codex Desktop 登录态获取。

### 1.3 已验证前提（Codex）

| 检查项 | 结果 |
|------|------|
| Codex CLI | 已安装，版本 `0.139.0` |
| 登录状态 | `codex login status` → `Logged in using ChatGPT` |
| `account/read` | 成功，账号类型 `chatgpt`，套餐 `plus` |
| `account/rateLimits/read` | 成功，返回 5h / 7d 窗口 |
| 启动方式 | 通过 `cmd.exe /c C:\nvm4w\nodejs\codex.cmd app-server --stdio` 启动 |

---

## 2. 新增 Collector

```
collectors/
├── base.py
├── openai_collector.py              # 已有：OpenAI API 组织费用（保留但不用于 Codex 卡片）
├── codex_collector.py               # 新增：Codex App Server 额度
├── deepseek_collector.py            # 已有：余额查询
├── deepseek_usage_collector.py      # 新增：CDP 月度用量抓取
└── clash_collector.py               # 已有：VPN 流量
```

---

## 3. CodexQuotaCollector（Codex App Server）

### 3.1 方案

独立 stdio App Server 子进程，Python 启动 `codex app-server --stdio`，通过 stdin/stdout 交换 JSON-RPC 消息。

优点：复用本机 ChatGPT 登录态、不读认证文件、不开放网络端口、容易控制生命周期。

### 3.2 通信协议

**启动命令（config.yaml）：**

```yaml
codex:
  enabled: true
  command:
    - "C:\\Windows\\System32\\cmd.exe"
    - "/d"
    - "/s"
    - "/c"
    - "C:\\nvm4w\\nodejs\\codex.cmd"
    - "app-server"
    - "--stdio"
  request_timeout_seconds: 15
```

命令解析优先级：`config.yaml` → `shutil.which("codex.cmd")` → `shutil.which("codex")`。

**初始化：**

```json
{"method":"initialize","id":0,"params":{"clientInfo":{"name":"usage_check","title":"AI Usage Check","version":"1.0.0"}}}
```

响应成功后发送 `{"method":"initialized","params":{}}`。

**账号确认（启动时 + 认证错误后）：**

```json
{"method":"account/read","id":1,"params":{"refreshToken":false}}
```

验证 `result.account.type == "chatgpt"`，记录 `planType`。

**额度读取（定时采集）：**

```json
{"method":"account/rateLimits/read","id":2}
```

兼容两种返回形态：`rateLimitsByLimitId`（多个桶）和 `rateLimits`（单个桶）。

每个额度桶包含：
```json
{
  "limitId": "codex",
  "primary":   { "usedPercent": 14, "windowDurationMins": 300,   "resetsAt": 1781869199 },
  "secondary": { "usedPercent": 2,  "windowDurationMins": 10080, "resetsAt": 1782357607 }
}
```

`remainingPercent = max(0, min(100, 100 - usedPercent))`。

**不依赖 `account/usage/read`**（已验证超时）。

### 3.3 生命周期

- FastAPI 启动时创建 **单例** `CodexQuotaCollector`，启动 App Server 并完成初始化
- 定时采集调用 `account/rateLimits/read`
- FastAPI 关闭时终止子进程
- `max_instances=1` 防止采集重叠

### 3.4 CollectResult

| source | metric | value | detail |
|--------|--------|-------|--------|
| `codex` | `rate_limit` | primary 剩余百分比 | `{account_type, plan_type, limit_id, primary, secondary}` |

### 3.5 错误处理

| 场景 | 处理 |
|------|------|
| 找不到 Codex 命令 | 返回配置错误 |
| 未登录 | 提示在相同 Windows 用户下登录 Codex |
| 请求超时 | 取消 pending 请求；连续失败重启一次 |
| 子进程异常退出 | 使全部 pending 请求失败，记录退出码 |
| 采集失败 | 保留上一条成功数据 |

---

## 4. DeepSeekUsageCollector（CDP 抓取）

### 4.1 行为

```
collect() 被调用
  → 查询 SQLite 中今日是否已有月度用量记录
  → 有 → 返回缓存数据（不启动 Chrome）
  → 无 → headless Chrome → CDP 抓取 → 解析 → 落库 → 返回
```

手动触发跳过当日缓存检查。

### 4.2 CDP 抓取流程

1. 检查当日缓存（手动模式跳过）
2. 启动 Chrome headless + `--remote-debugging-port=<random>`
3. WebSocket 连接 CDP endpoint
4. `Page.navigate` → `https://platform.deepseek.com/usage`
5. 等待 7s 渲染
6. `Runtime.evaluate` → `document.body.innerText`
7. 正则解析：按模型分段 → 提取 Token/请求数/费用
8. 关闭 Chrome，返回结果

### 4.3 Chrome 策略

- **自动模式**（headless）：检查已有 debugger（端口 9222），有则复用；无则新建
- **登录模式**（可见窗口）：打开 Chrome 让用户手动登录，profile 持久化

### 4.4 配置

```yaml
deepseek_usage:
  chrome_path: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
  profile_path: "C:\\Temp\\chrome_headless"
  model: "deepseek-v4-pro"
```

### 4.5 CollectResult

| source | metric | value | detail |
|--------|--------|-------|--------|
| `deepseek` | `monthly_tokens` | token 总数 | `{model, requests, cost, updated}` |

---

## 5. 后端 API

### 新增端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/collect/usage` | 手动触发 DeepSeek 月度用量抓取 |
| POST | `/api/collect/usage/login` | 打开 Chrome 登录窗口 |

### `GET /api/dashboard` 返回格式

```json
{
  "deepseek": {
    "balance": 20.50,
    "currency": "CNY",
    "lastUpdated": "2026-06-19 14:30",
    "monthlyTokens": 1234567,
    "monthlyRequests": 89,
    "monthlyCost": 3.45,
    "monthlyModel": "deepseek-v4-pro",
    "monthlyUpdated": "06-19 14:30"
  },
  "codex": {
    "available": true,
    "planType": "plus",
    "primary": {
      "windowMinutes": 300,
      "usedPercent": 14,
      "remainingPercent": 86,
      "resetsAt": 1781869199,
      "resetTime": "2026-06-19 19:39"
    },
    "secondary": {
      "windowMinutes": 10080,
      "usedPercent": 2,
      "remainingPercent": 98,
      "resetsAt": 1782357607,
      "resetTime": "2026-06-25 11:20"
    },
    "lastUpdated": "2026-06-19 18:10",
    "stale": false
  },
  "vpn": {
    "totalBandwidth": 500,
    "usedBandwidth": 23.5,
    "remainingBandwidth": 476.5,
    "unit": "GB",
    "expiryDate": "2026-07-15"
  }
}
```

移除：`deepseek.todayTokens`、`deepseek.todayCost`、`deepseek.totalSpent`、`codex.hourly`、`codex.weekly`（旧格式）。

---

## 6. 前端重写

### 6.1 技术方案

- 单文件 `web/static/index.html`，内联 CSS + 原生 JS
- 零依赖，Kindle 浏览器兼容
- 深色主题（与当前 Tailwind 蓝紫灰色调一致）

### 6.2 布局

```
┌──────────────────────────────────────────────────┐
│  AI 用量仪表盘                        [手动刷新]   │
├────────────────┬─────────────────┬───────────────┤
│  DeepSeek      │  Codex          │  VPN 流量     │
│                │  ChatGPT Plus   │               │
│  余额 ¥20.50   │  5h: 86% 剩余   │  已用 23.5 GB │
│  CNY           │  7d: 98% 剩余   │  剩余 476.5 GB│
│                │                 │  到期 07-15   │
│  ──────────────────────────────  │               │
│  本月 v4-pro   [刷新]             │               │
│  Token 1.23M · 89次              │               │
│  费用 ¥3.45                      │               │
│  更新 06-19 14:30                │               │
└────────────────┴─────────────────┴───────────────┘
```

### 6.3 交互

- 余额/额度和 VPN 数据每 60s 自动轮询 `/api/dashboard`
- DeepSeek 月度用量区域有独立 `[刷新]` 按钮 → `POST /api/collect/usage`
- 首次使用需点击登录按钮 → `POST /api/collect/usage/login`
- `codex.available == false` 时显示错误状态，不显示虚假 `0%`

---

## 7. 调度与生命周期

### FastAPI 启动

1. 加载配置
2. 初始化 SQLite
3. 创建单例 `CodexQuotaCollector`，启动 App Server，完成初始化
4. 执行首次全量采集（Codex + DeepSeek 余额 + CDP 月度用量 + VPN）
5. 启动 APScheduler（每 5 分钟增量采集）

### 定时采集

- Codex：`account/rateLimits/read` → 落库
- DeepSeek 余额：`GET /user/balance` → 落库
- DeepSeek 月度用量：检查当日缓存 → 有则跳过，无则 CDP 抓取
- VPN：Clash API → 落库
- 单个采集器失败不影响其他

### FastAPI 关闭

1. 停止调度器
2. 关闭 Codex App Server 子进程
3. 关闭 SQLite

---

## 8. 错误处理汇总

| 场景 | 处理 |
|------|------|
| Codex 未登录 | 仪表盘显示"需登录"，不显示 0% |
| Codex 命令缺失 | 返回配置错误 |
| Codex 子进程崩溃 | 下次调度自动重启一次 |
| Chrome 未安装 | DeepSeek 月度用量不可用，日志警告 |
| Chrome Profile 无登录态 | 提示调用 login 接口 |
| DeepSeek 页面结构变化 | 返回解析错误 detail，不崩溃 |
| 当日已抓取（月度用量） | 返回 SQLite 缓存 |
| 单个采集器失败 | 其他采集器不受影响 |

---

## 9. 文件变更范围

| 操作 | 文件 |
|------|------|
| 新增 | `collectors/codex_collector.py` |
| 新增 | `collectors/deepseek_usage_collector.py` |
| 重写 | `web/static/index.html` |
| 修改 | `app.py` — 注册新 Collector + 新增路由 + 修改 dashboard 映射 + 生命周期管理 |
| 修改 | `config.yaml` — 新增 `codex`、`deepseek_usage` 配置段 |
| 修改 | `requirements.txt` — 确认 `websocket-client` 依赖 |
| 修改 | `services/scheduler.py` — 阻止任务重叠 |
| 删除 | `web/static/assets/index-xnJp_0UT.js` |
| 删除 | `web/static/assets/index-DmCWwFP8.css` |
| 保留 | `collectors/openai_collector.py` — 不再用于仪表盘 Codex 卡片 |

---

## 10. 验收标准

- Codex 额度通过 App Server stdio 采集，不读认证文件、不需要 API Key
- DeepSeek 月度用量通过 CDP 抓取网页获取，当日只抓一次
- 仪表盘展示：DeepSeek 余额 + 月度用量、Codex 双窗口额度、VPN 流量
- 任一采集器失败不影响其他
- App Server 不监听网络端口
- 服务退出后不遗留子进程
- Kindle 浏览器可正常访问仪表盘
