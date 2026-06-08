# Phase 2: Shell 执行器重构

> 预估工时：2 天
> 依赖：Phase 1（EventBridge 用于确认转发）
> 目标：异步化、沙箱化、审计化

---

## 任务清单

### Task 2.1: SandboxExecutor

**文件**：`src/tools/shell_executor.py`（新建）

```python
SandboxExecutor
├── execute(command, *, timeout, cwd, env) → ShellResult
├── TIMEOUT_MAP  # 按命令类型的差异化超时
└── MAX_OUTPUT_CHARS = 50_000
```

**设计要点**：
- 使用 `asyncio.create_subprocess_shell` 而非 `subprocess.run`
- 进程级超时用 `asyncio.wait_for`
- 超时时 `proc.kill()` + `await proc.wait()` 确保无僵尸进程
- stdout/stderr 用 `MAX_OUTPUT_CHARS` 截断

**差异化超时**：

| 命令类型 | 默认超时 |
|----------|----------|
| ls/dir/pwd/echo | 5s |
| cat/grep/find | 15s |
| git/curl | 30s |
| python/node/npm | 60s |
| pip/uv install | 120s |
| 其他 | 30s |

**验收标准**：
- `execute("echo hello")` 返回 `ShellResult(stdout="hello\n", returncode=0, duration<1)`
- `execute("sleep 100", timeout=1)` 返回 `ShellResult(stderr="超时", returncode=-1)`
- 输出 `>` 50KB 的命令被截断

### Task 2.2: AuditLogger

**文件**：`src/audit.py`（新建）

```python
AuditLogger(db_path="audit.db")
├── log(command, returncode, duration, stdout_size)
├── query(limit=50) → list[dict]
└── MAX_RECORDS = 10_000
```

**Schema**：
```sql
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    session_id TEXT,
    command TEXT NOT NULL,
    returncode INTEGER NOT NULL,
    duration REAL NOT NULL,
    stdout_size INTEGER DEFAULT 0,
    approved INTEGER DEFAULT 1
);
```

**验收标准**：
- 执行 `echo hello` 后，`query()` 返回包含该命令的记录
- `MAX_RECORDS` 触发时自动清理老数据
- 多线程调用 `log()` 不抛异常

### Task 2.3: 重构 shell 工具

**文件**：`src/tools/shell.py`（改写）

**改动**：
- 保持 `@tool` 装饰器和接口不变
- 内部使用 `SandboxExecutor.execute()` 替代 `subprocess.run`
- `ConfirmationError` 处理不变（仍由上层处理）
- 增加异步支持

**验收标准**：
- `shell("ls")` 返回目录列表
- `shell("sleep 100")` 5 秒返回超时错误（而非 30 秒）
- `shell("rm -rf /")` 被 Layer 2 拦截

### Task 2.4: LLM 辅助分类

**文件**：`src/tools/shell_patterns.py`（增强）

在 `CommandClassifier.classify()` 中，Layer 4 兜底后新增 Layer 5：

```python
def _classify_with_llm(self, command: str, llm) -> DangerLevel:
    """LLM 辅助分类，降低误判率。"""
```

**触发条件**：仅在 `cmd_head not in _SAFE_COMMANDS` 时调用

**验收标准**：
- 常见安全命令不触发 LLM 调用（Layer 1-4 已覆盖）
- `kubectl drain node` 这类白名单未覆盖的 kubectl 子命令被 LLM 正确分类为 CONFIRM
- LLM 调用失败时降级为 CONFIRM（保守策略）

---

## 文件变更

```
新建: src/tools/shell_executor.py   (~120 行)
新建: src/audit.py                  (~80 行)
改写: src/tools/shell.py            (~60 行 → ~80 行)
增强: src/tools/shell_patterns.py   (+50 行)
```

---

## 测试策略

| 测试 | 方法 | 覆盖 |
|------|------|------|
| SandboxExecutor 正常路径 | `pytest -k test_execute_ok` | 命令正确执行 |
| SandboxExecutor 超时 | `pytest -k test_execute_timeout` | 超时处理 |
| SandboxExecutor 截断 | 生成 >50KB 输出 | 输出截断 |
| AuditLogger 读写 | `pytest -k test_audit_rw` | SQLite CRUD |
| AuditLogger 轮转 | 插入 10001 条 | 自动清理 |
| shell 工具兼容 | `pytest -k test_shell_tool` | 旧接口不破坏 |
| LLM 分类 | mock LLM 调用 | 分类正确 |
