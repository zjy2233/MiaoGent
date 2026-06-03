# Shell 命令执行工具设计

## 概述

为 Agent 新增 `shell` 工具，支持执行任意 shell/bash 命令，返回 stdout/stderr 输出。通过命令模式匹配实现三级危险检测（安全/需确认/高危），需确认操作通过 `ConfirmationError` 异常触发 REPL 层确认交互。

---

## 架构

```
Agent (ReAct)
    │
    ▼
┌──────────────────────────────────────┐
│  shell(command, timeout=30)          │
│  ├─ 危险检测（命令 + 参数模式匹配）  │
│  ├─ 安全命令 → subprocess 执行        │
│  ├─ 需确认 → 抛 ConfirmationError    │
│  └─ 高危   → 返回拒绝信息给 Agent    │
└──────────────────────────────────────┘
    │ ConfirmationError
    ▼
REPL 事件循环（main.py）
    │ try-except 捕获
    ▼
打印确认提示 [y/N] → 用户输入
    │
    ├─ y → 重新执行
    └─ n → 返回"已取消"给 Agent
```

---

## 危险命令三级分级

### 安全命令（直接执行）

| 模式 | 示例 |
|------|------|
| 目录列表 | `ls`, `ls -la`, `dir` |
| 文件读取 | `cat`, `type`, `head`, `tail`, `grep`, `find` |
| 信息查询 | `pwd`, `whoami`, `uname`, `df`, `du` |
| 网络检查 | `ping`, `curl -I`, `wget --spider` |
| 其他 | `echo`, `env`, `export`, `history` |

### 需确认命令（抛 ConfirmationError → REPL 确认）

| 模式 | 说明 |
|------|------|
| `rm` 单独 | 删除文件（非递归） |
| `rm -r` / `rm -rf` | 删除目录（递归） |
| `mv` | 移动/重命名（目标存在时危险） |
| `cp` | 复制（目标存在时危险） |
| `>` | 重定向覆盖（危险） |
| `>>` | 重定向追加（安全，不拦截） |
| `touch` | 目标文件已存在时覆盖元数据 |
| `chmod` | 权限变更 |
| `chown` | 所有权变更 |
| `kill` | 终止进程 |
| `shutdown` / `reboot` | 系统级命令 |

### 高危命令（直接拒绝，返回错误信息给 Agent）

| 模式 | 说明 |
|------|------|
| `rm -rf /` | 根目录递归删除 |
| `dd` | 直接磁盘操作 |
| `> /dev/sd*` | 写入设备文件 |
| `curl ... \| sh` | 远程代码执行 |
| `wget ... \| sh` | 远程代码执行 |
| `shutdown -h` / `reboot` | 系统关机/重启 |
| `:(){:|:&};:` | Fork 炸弹 |
| `mkfs` | 格式化分区 |
| `dd if=... of=/dev/sd*` | 磁盘覆写 |

---

## ConfirmationError

```python
class ConfirmationError(Exception):
    def __init__(self, command: str, reason: str, danger_level: str):
        self.command = command      # 原始命令
        self.reason = reason       # 原因描述，如 "删除文件"
        self.danger_level = danger_level  # "confirm" 或 "high_risk"
```

- `danger_level="confirm"` → REPL 层弹出 `[y/N]` 确认提示
- `danger_level="high_risk"` → 不弹确认，直接返回错误信息给 Agent

---

## 确认流程

```
Agent 调用 shell("rm -rf /tmp/test")
        │
        ▼
危险检测 → 需确认 → 抛 ConfirmationError
        │
        ▼
REPL try-except 捕获异常
        │
        ▼
打印：
「⚠️ 此操作危险：rm -rf /tmp/test
   原因：递归删除目录
   确认执行？[y/N]」
        │
   用户输入 y
        │
        ▼
重新执行 shell("rm -rf /tmp/test")
        │
        ▼
返回执行结果给 Agent
```

用户拒绝时：打印 `"已取消"`，Agent 收到错误信息。

---

## 工具签名

```python
@tool
def shell(command: str, timeout: int = 30) -> str:
    """执行 shell 命令并返回 stdout/stderr 输出。

    Args:
        command: 要执行的 shell 命令。
        timeout: 超时秒数，默认 30 秒。

    Returns:
        命令的标准输出/错误输出。
        危险命令被拒绝时返回错误信息，Agent 应据此回复用户。
    """
```

---

## 配置项（config.py）

```python
shell_auto_confirm: bool = False    # true = 安全命令免确认直接执行（默认行为）
shell_high_risk_block: bool = True  # true = 高危命令直接拒绝
shell_allowed_patterns: list[str] = []  # 自定义白名单（免检测命令）
shell_blocked_patterns: list[str] = []  # 黑名单（强制高危）
```

---

## 实现文件

| 文件 | 说明 |
|------|------|
| `src/tools/dangerous.py` | `ConfirmationError` + 危险检测逻辑入口 |
| `src/tools/shell_patterns.py` | 命令分级模式定义（安全/需确认/高危） |
| `src/tools/shell.py` | `shell` 工具实现 |
| `src/tools/__init__.py` | 修改：导出 `shell` |
| `src/agent.py` | 修改：注册 `shell` 工具 |
| `src/main.py` | 修改：捕获 `ConfirmationError`，处理确认流 |
| `src/config.py` | 修改：新增 shell 配置项 |

---

## 错误处理

- 命令执行超时 → 返回 `"错误：命令超时（{timeout}秒）"`
- 命令非零退出码 → 返回 `"错误（退出码 {code}）：{stderr}"`
- 危险检测异常 → 降级为安全命令（不阻断执行）
- ConfirmationError 确认取消 → 返回 `"已取消"`

---

## 测试策略

- `test_shell_patterns.py`：验证命令分级正确性（安全/需确认/高危各覆盖）
- `test_shell_tool.py`：验证执行、超时、错误返回
- `test_confirmation_flow.py`：验证 REPL 层确认交互流程（mock input）
