# 开发测试问题记录

## 1. Windows asyncio `create_subprocess_exec` 不支持 SelectorEventLoop

- **现象:** `NotImplementedError` at `asyncio.base_events.BaseEventLoop._make_subprocess_transport`
- **原因:** Windows 上 `SelectorEventLoop` 不支持子进程 PIPE 通信，需使用 `ProactorEventLoop`
- **解决:** 改用 `subprocess.Popen` + 独立线程读 stdout，通过 `loop.call_soon_threadsafe()` 桥接到 asyncio
- **文件:** `collectors/codex_collector.py`
- **回归保护:** 已由自动测试覆盖。
- **状态:** 已解决。

---

## 2. Python 3.14 非主线程无法获取事件循环

- **现象:** `RuntimeError: There is no current event loop in thread 'Thread-X'`
- **原因:** Python 3.12+ 中 `asyncio.get_event_loop()` 在非主线程中不再自动创建/返回事件循环
- **解决:** 主线程通过 `asyncio.get_running_loop()` 获取 loop，存储为 `self._loop`，传递给工作线程
- **文件:** `collectors/codex_collector.py:_read_stdout_sync()`
- **回归保护:** 已由自动测试覆盖。
- **状态:** 已解决。

---

## 3. Codex 首次采集偶发超时

- **现象:** `account/rateLimits/read` 在初始化后立即调用时超时（15s）
- **推测:** 初始化 RPC 刚完成，App Server 内部状态未就绪
- **解决:** 首次额度超时进行一次有界重试（`initial_retry_count=1`，可配置），仅对超时错误重试
- **回归保护:** `tests/test_codex_collector_regressions.py`
- **状态:** 已解决。

---

## 4. .bat 文件使用 LF 行尾导致启动失败

- **现象:** 双击 `start.bat` 后 cmd.exe 无法正确解析命令，所有变量为空
- **原因:** Windows cmd.exe 只能解析 CRLF (`\r\n`) 行尾的 .bat 文件
- **解决:** `sed -i 's/$/\r/' start.bat stop.bat` 转换为 CRLF
- **附加修复:** `stop.bat` 中 `findstr ":8080"` 改为 `findstr /R /C:":8080 "` 防止误匹配 `:80800` 等端口
- **回归保护:** 已由自动测试覆盖。
- **状态:** 已解决。

---

## 5. uvicorn reload 未触发

- **现象:** 代码修改后 API 返回仍是旧格式
- **原因:** WatchFiles 有时未检测到变更，或未完全重启 worker 进程
- **处理原则:** 热重载仅用于开发便利，不作为验证依据；重要改动后执行 `stop.bat` + `start.bat` 完整重启。
- **验证:** 重启后重新请求 `/api/dashboard` 并检查当前响应。
- **状态:** 已规避。

---

## 6. Codex RPC 请求固定 id 的潜在冲突

- **风险:** `initialize` 使用固定 `rpc_id=0`，如果子进程残留旧的 pending future 可能冲突
- **解决:** 新 App Server 启动和关闭时通过 `_reset_rpc_state()` 清理 pending futures 并重置请求序列；initialize 不再使用固定 id。
- **回归保护:** `tests/test_codex_collector_regressions.py`
- **状态:** 已解决。
