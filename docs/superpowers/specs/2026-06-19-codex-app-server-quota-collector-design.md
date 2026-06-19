# Codex App Server 订阅额度采集方案

**日期:** 2026-06-19  
**状态:** 已批准  
**适用环境:** Windows、Codex Desktop、Python 3.14

---

## 1. 背景与目标

AI Usage Check 需要采集当前 ChatGPT 账号的 Codex 订阅额度，并通过 FastAPI 仪表盘提供给桌面和 Kindle Paperwhite 3 浏览器。

ChatGPT 套餐中的 Codex 使用额度与 OpenAI API 组织账单是两个不同的数据域：

- OpenAI API Key 可查询 API Token、费用等组织级数据。
- Codex Desktop 使用 ChatGPT 登录，额度由 Codex App Server 提供。
- API Key、`/organization/costs` 和旧版 Dashboard Billing 接口不能可靠表示 ChatGPT Codex 订阅余量。

本方案的目标是让 Python 服务在同一台 Windows 电脑上自动复用 Codex Desktop 的登录状态，定时采集 5 小时和 7 天额度窗口。Kindle 仅作为局域网仪表盘终端，不运行 Python、Codex CLI 或 App Server。

---

## 2. 已验证前提

以下项目已于 2026-06-19 在目标电脑上实际验证：

| 检查项 | 结果 |
|------|------|
| Windows 用户 | Codex Desktop 与 Python 均运行在 `jaygoaler` 用户下 |
| `CODEX_HOME` | 进程、用户和系统级均未覆盖，使用默认目录 |
| Codex CLI | 已安装，版本 `0.139.0` |
| 登录状态 | `codex login status` 返回 `Logged in using ChatGPT` |
| 桌面端 App Server | Codex Desktop 已启动自己的 `codex.exe app-server` 进程 |
| Python 独立启动 App Server | 成功 |
| `account/read` | 成功，账号类型为 `chatgpt`，套餐为 `plus` |
| `account/rateLimits/read` | 成功，返回 Codex 5 小时和 7 天窗口 |
| `account/usage/read` | 请求超时，不作为额度采集依赖 |

验证期间没有直接读取或输出令牌、Cookie 或 `auth.json` 内容。

这些结果证明 Python 可以通过新启动的 App Server 复用同一 Windows 用户的 Codex 登录状态。Codex Desktop 不需要保持前台打开；只要本机凭据仍有效即可。

---

## 3. 方案比较与决策

### 3.1 方案 A：独立 stdio App Server 子进程（采用）

Python 启动 `codex app-server --stdio`，通过标准输入输出交换 JSON-RPC 消息。

优点：

- 使用 Codex 官方本机协议。
- 自动复用已有 ChatGPT 登录状态。
- 不读取、不复制认证文件。
- 不开放网络监听端口。
- 容易控制进程生命周期、超时和重启。

缺点：

- App Server 当前属于实验性接口，升级后可能需要适配字段。
- Windows 下需要正确解析 `codex` 命令包装器。

### 3.2 方案 B：连接 Codex Desktop 已有 App Server（不采用）

尝试连接桌面端内部已经运行的 App Server。

不采用原因：

- 桌面端内部传输端点和生命周期由应用管理。
- 会与桌面端实现细节耦合。
- 桌面端升级可能改变控制套接字或认证方式。

### 3.3 方案 C：读取认证文件并直接请求内部 HTTP 接口（禁止）

不采用原因：

- 需要处理敏感令牌和刷新逻辑。
- 内部 HTTP 接口不是稳定的公共集成面。
- 增加凭据泄漏、失效和账号安全风险。

---

## 4. 总体架构

```text
ChatGPT / Codex 服务
          ↑
          │ Codex 内部认证与请求
          │
Python usageCheck
  └─ CodexQuotaCollector
       └─ codex app-server --stdio
          ↑
          │ JSON-RPC
          ↓
       额度标准化
          ↓
       SQLite usage_history
          ↓
       FastAPI /api/dashboard
          ↓ 局域网 HTTP
Kindle Paperwhite 3 浏览器
```

职责边界：

- `CodexQuotaCollector`：管理 App Server、发送 RPC、校验和标准化响应。
- `DataStore`：持久化采集结果，不处理 Codex 协议。
- `app.py`：调度采集器、组合仪表盘响应。
- Kindle：只轮询 HTTP 数据并展示，不接触任何 OpenAI 凭据。

---

## 5. App Server 通信协议

### 5.1 启动方式

优先配置一个可执行命令列表，避免通过 shell 拼接字符串：

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

目标环境中，Python 直接执行名为 `codex` 的命令曾触发 Windows `PermissionError: [WinError 5]`。经验证，通过 `cmd.exe /c C:\nvm4w\nodejs\codex.cmd` 可以正常启动。

实现时不应硬编码 WindowsApps 中桌面端捆绑的 `codex.exe` 路径。该目录带版本号且可能受 WindowsApps 权限限制。

命令解析优先级：

1. `config.yaml` 中显式配置的 `codex.command`。
2. `shutil.which("codex.cmd")`。
3. `shutil.which("codex")`。
4. 均不存在时返回可诊断错误。

### 5.2 初始化

每个新进程先发送：

```json
{
  "method": "initialize",
  "id": 0,
  "params": {
    "clientInfo": {
      "name": "usage_check",
      "title": "AI Usage Check",
      "version": "1.0.0"
    }
  }
}
```

收到成功响应后发送通知：

```json
{"method":"initialized","params":{}}
```

未完成初始化时不得发送额度请求。

### 5.3 账号确认

首次启动以及认证错误后调用：

```json
{
  "method": "account/read",
  "id": 1,
  "params": {"refreshToken": false}
}
```

使用以下字段判断状态：

- `result.account.type` 应为 `chatgpt`。
- `result.account.planType` 用于诊断和展示套餐。
- 账号不存在或返回认证错误时，提示用户在相同 Windows 用户下登录 Codex。

不得将邮箱、访问令牌或其他身份信息保存到 SQLite 或日志中。

### 5.4 额度读取

定时采集调用：

```json
{"method":"account/rateLimits/read","id":2}
```

兼容两种返回形态：

1. `result.rateLimitsByLimitId`：按额度 ID 返回多个桶。
2. `result.rateLimits`：单个兼容桶。

每个额度桶可能包含：

```json
{
  "limitId": "codex",
  "primary": {
    "usedPercent": 14,
    "windowDurationMins": 300,
    "resetsAt": 1781869199
  },
  "secondary": {
    "usedPercent": 2,
    "windowDurationMins": 10080,
    "resetsAt": 1782357607
  }
}
```

计算规则：

```python
remaining_percent = max(0, min(100, 100 - used_percent))
```

`resetsAt` 按 Unix 秒时间戳保存，同时在 API 层转换为服务器本地时区显示。数据库中不要只保存格式化后的时间字符串。

### 5.5 不依赖 `account/usage/read`

`account/usage/read` 在目标环境的实测结果为 `token usage profile fetch timed out`。该接口可在未来作为可选增强，但不能影响额度采集成功状态。

---

## 6. Collector 设计

现有 `collectors/openai_collector.py` 查询的是 OpenAI API 组织费用，却被仪表盘当成 Codex 订阅额度。实现时应拆分语义：

```text
collectors/
├── codex_collector.py        # 新增：ChatGPT Codex 额度
└── openai_collector.py       # 可选保留：OpenAI API 组织账单
```

建议类名：

```python
class CodexQuotaCollector(BaseCollector):
    async def collect(self) -> list[CollectResult]:
        ...

    async def close(self) -> None:
        ...
```

采集器内部组件：

- 子进程启动与关闭。
- stdout 单独读取任务。
- 按 RPC `id` 匹配响应的 pending future 映射。
- 写入锁，防止并发 JSON 行交叉。
- 初始化状态和账号确认缓存。
- 请求超时、进程退出检测和一次受控重启。

同一 FastAPI 进程只维护一个 `CodexQuotaCollector` 实例。不要每次调度都创建新实例，否则会重复创建子进程并增加登录服务压力。

---

## 7. 标准化数据与存储

额度采集产生一条记录：

| 字段 | 值 |
|------|----|
| `source` | `codex` |
| `metric` | `rate_limit` |
| `value` | 主窗口剩余百分比 |
| `unit` | `%` |

`detail` 示例：

```json
{
  "account_type": "chatgpt",
  "plan_type": "plus",
  "limit_id": "codex",
  "primary": {
    "used_percent": 14,
    "remaining_percent": 86,
    "window_minutes": 300,
    "resets_at": 1781869199
  },
  "secondary": {
    "used_percent": 2,
    "remaining_percent": 98,
    "window_minutes": 10080,
    "resets_at": 1782357607
  },
  "collector": "app_server"
}
```

如果返回多个额度桶，每个 `limit_id` 生成独立记录，或在一个记录的 `detail.limits` 中完整保存。首版采用单条 `codex:rate_limit` 记录并完整保存 `limits`，仪表盘优先选择 `limit_id == "codex"`，避免数据库唯一语义依赖当前只有一个桶。

旧的 `openai:credit` 历史数据不迁移、不删除，但不再用于 Codex 卡片。

---

## 8. 生命周期与调度

FastAPI 启动：

1. 加载配置。
2. 初始化 SQLite。
3. 创建单例 `CodexQuotaCollector`。
4. 启动 App Server 并完成初始化。
5. 执行一次账号确认和首次额度采集。
6. 启动 APScheduler。

定时采集：

1. 调用 `account/rateLimits/read`。
2. 标准化响应。
3. 写入 SQLite。
4. 更新 SSE 事件。
5. 单个采集器失败不影响 DeepSeek 和代理流量采集。

FastAPI 关闭：

1. 停止调度器。
2. 取消 stdout 读取任务。
3. 关闭 App Server stdin。
4. 等待子进程短暂退出。
5. 超时后终止子进程。
6. 关闭 SQLite。

调度任务必须设置 `max_instances=1` 或由采集器加锁，避免前一次采集未完成时重复请求。

---

## 9. 错误处理

| 场景 | 处理 |
|------|------|
| 找不到 Codex 命令 | 返回配置错误，日志记录已检查的命令来源 |
| 未登录 | 返回明确错误：需在相同 Windows 用户下登录 Codex |
| `CODEX_HOME` 不一致 | 日志输出目录是否覆盖，不输出其中内容 |
| 初始化失败 | 关闭子进程，下次调度重新启动 |
| 请求超时 | 取消 pending 请求；连续失败后重启一次 App Server |
| App Server 异常退出 | 使全部 pending 请求失败并记录退出码 |
| JSON 行无法解析 | 忽略非 JSON 诊断行；记录截断后的安全摘要 |
| 字段缺失 | 保留原始非敏感响应结构摘要，返回协议不兼容错误 |
| `account/usage/read` 超时 | 忽略，不影响额度状态 |
| 当前采集失败 | 数据库保留上一条成功数据，API 标记数据过期 |

日志禁止包含：

- 访问令牌和刷新令牌。
- Cookie。
- `auth.json` 内容。
- 完整邮箱。
- 完整原始认证错误响应。

---

## 10. API 与前端映射

`GET /api/dashboard` 的 Codex 部分改为：

```json
{
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
  }
}
```

兼容性要求：

- 前端根据 `windowMinutes` 显示“5 小时”和“7 天”，不要硬编码额度数量。
- `available == false` 时显示错误状态，不显示虚假的 `0%`。
- Kindle 继续通过 HTTP 轮询读取数据，不连接 App Server。

---

## 11. 安全与网络边界

- App Server 只使用 stdio，不配置 `ws://0.0.0.0:*`。
- FastAPI 可以监听局域网地址，但只暴露处理后的额度数据。
- 不提供下载认证文件、查看原始 RPC 响应或远程执行 Codex 命令的 API。
- `POST /api/collect/now` 只能触发预定义采集，不能接受客户端传入命令。
- 若未来需要公网访问仪表盘，必须增加认证和 HTTPS；本规格只覆盖受信任局域网。

---

## 12. 测试策略

### 12.1 单元测试

- 解析单个 `rateLimits`。
- 解析多个 `rateLimitsByLimitId`。
- 百分比限制在 `0..100`。
- `primary` 或 `secondary` 缺失。
- `resetsAt` Unix 时间戳转换。
- RPC 响应按乱序 `id` 正确匹配。
- 超时后 pending future 被清理。
- 日志脱敏。

### 12.2 进程集成测试

使用伪 App Server 子进程验证：

- 初始化顺序。
- 正常额度响应。
- 非 JSON stdout 行。
- 进程提前退出。
- 首次超时后重启。
- 关闭时不遗留子进程。

### 12.3 本机验收

在 Codex Desktop 已登录的同一 Windows 用户下：

1. `account/read` 返回 `type=chatgpt`。
2. `account/rateLimits/read` 返回至少一个有效窗口。
3. Python 采集结果与 Codex Desktop 显示的额度大致一致。
4. 关闭 Codex Desktop 后再次采集仍成功。
5. 重启 usageCheck 后无需再次登录。
6. Kindle 通过 `http://<电脑局域网IP>:8080` 正常显示额度。

---

## 13. 文件变更范围

| 操作 | 文件 |
|------|------|
| 新增 | `collectors/codex_collector.py` |
| 新增 | `tests/test_codex_collector.py` |
| 新增 | `tests/fixtures/fake_codex_app_server.py` |
| 修改 | `app.py`：注册单例采集器、生命周期和 dashboard 映射 |
| 修改 | `config.yaml`：增加 `codex` 配置 |
| 修改 | `services/scheduler.py`：阻止采集任务重叠 |
| 修改 | `web/static/index.html`：按真实窗口展示额度 |
| 修改 | `collectors/openai_collector.py`：明确其仅代表 API 组织账单，或停止注册 |

本方案不修改 SQLite 表结构，继续使用 `usage_history.detail` 保存窗口明细。

---

## 14. 验收标准

- Python 服务能够在不读取认证文件、不要求 API Key 的情况下采集 Codex 订阅额度。
- Codex Desktop 已登录且 Python 使用同一 Windows 用户时，不需要额外登录操作。
- 返回主、次额度窗口的已用比例、剩余比例、窗口长度和重置时间。
- 采集失败不会影响 DeepSeek 和代理流量采集。
- App Server 不监听局域网端口。
- 服务退出后不遗留由 usageCheck 启动的 App Server 进程。
- Kindle 页面只访问 FastAPI，不接触 Codex 凭据或协议。
- App Server 协议变化时产生明确错误，而不是展示错误的零值。

---

## 15. 官方参考

- [Codex App Server](https://developers.openai.com/codex/app-server/)
- [Codex Authentication](https://developers.openai.com/codex/auth/)

