# 后端代码整改方案

## P0 — 立即修复

### 1. 提取共享工具函数

**问题**：`_content_str`/`_extract_text` 在4个文件中重复
**方案**：新建 `src/core/utils.py`，提取公共函数并替换所有引用
- `src/agent/memory.py:311`
- `src/agent/memory_extractor.py:209`（如存在）
- `src/agent/rewoo.py:59`
- `src/store/knowledge.py:284`

### 2. SQLite 连接管理器

**问题**：4个 store 文件重复 `sqlite3.connect + try/finally close`
**方案**：新建 `src/store/db.py`，提供 `get_connection` 上下文管理器

---

## P1 — 核心重构

### 3. 清理死代码

**具体项**：
- `builder.py:17` 移除 `from typing import Required`
- `rewoo.py:17` 移除 `import textwrap`
- `tracing/store.py:5` 移除 `import json`
- `builder.py:426-472` 移除 `build_supervisor_agent` 空壳
- `delegate_task.py:33-34` 清理未使用参数

### 4. 拆分 bridge.py 的 Tracing 职责

**方案**：将 TracingStreamHandler（~150行）移到 `src/tracing/stream_handler.py`
将序列化函数群移到 `src/core/serialize.py`

### 5. 拆分 bridge.py 的 Api 上帝类

**方案**：拆分为 `SessionService`、`ChatService`、`SettingsService`、`TracingService`

### 6. install_skill.py 分支重构

**方案**：按来源类型拆分为 `_install_from_git`、`_install_from_npm` 等子函数

---

## P2 — 代码规范

### 7. 魔法数字常量化

- `rewoo_intent.py:14-29` 权重值 → 命名常量
- `knowledge.py:29-38` 阈值 → 命名常量

### 8. namedtuple → dataclass

- `builder.py:112-123` AgentBundle/SupervisorBundle 改用 `@dataclass`

### 9. 异常处理规范化

- `memory.py` 中6处 try/except: pass 改为至少 logging
- `bridge.py:979-981` finally 块避免覆盖原始异常
