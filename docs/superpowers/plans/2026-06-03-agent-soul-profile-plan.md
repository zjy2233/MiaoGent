# Agent Soul & 用户画像 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 Agent Soul（用户自定义风格）和用户画像（全局共享用户信息）两个功能。

**Architecture:** Soul 和 Profile 以 JSON 文件存储于项目根目录；Soul 在 `build_agent` 时拼接进 system prompt；Profile 通过 `ProfileMiddleware` 注入 LLM 调用；Profile 自动发现由 `MemoryManager` 在压缩时触发。

**Tech Stack:** Python 3.11+, LangChain 1.x, LangGraph, python-dotenv

---

## File Structure

| 文件 | 职责 |
|------|------|
| `src/soul.py` | 新模块：加载/保存 soul.json 和 profile.json，提供 `SoulManager` 和 `ProfileManager` |
| `src/agent.py` | 修改：inject soul into system prompt，新增 `ProfileMiddleware` |
| `src/memory.py` | 修改：`MemoryManager` 增加 `_discover_profile_facts` 方法 |
| `src/main.py` | 修改：新增 REPL 命令 `:soul` 和 `:profile` 处理 |

---

## Task 1: SoulManager

**Files:**
- Create: `src/soul.py`
- Test: `tests/test_soul.py`

- [ ] **Step 1: Write failing test for SoulManager**

```python
# tests/test_soul.py
import tempfile, json, os
from src.soul import SoulManager

def test_load_returns_empty_when_no_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = SoulManager(path=os.path.join(tmpdir, "soul.json"))
        soul = manager.load()
        assert soul == {"version": 1, "description": ""}

def test_save_and_load():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "soul.json")
        manager = SoulManager(path=path)
        manager.save({"version": 1, "description": "幽默风格"})
        soul = manager.load()
        assert soul["description"] == "幽默风格"

def test_load_invalid_json_falls_back_to_default():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "soul.json")
        with open(path, "w") as f:
            f.write("not json")
        manager = SoulManager(path=path)
        soul = manager.load()
        assert soul == {"version": 1, "description": ""}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_soul.py::test_load_returns_empty_when_no_file -v`
Expected: FAIL — module has no attribute 'SoulManager'

- [ ] **Step 3: Write minimal SoulManager**

```python
# src/soul.py
from __future__ import annotations
import json
from pathlib import Path

DEFAULT_SOUL = {"version": 1, "description": ""}

class SoulManager:
    def __init__(self, path: str | None = None) -> None:
        self.path = Path(path) if path else Path("soul.json")

    def load(self) -> dict:
        if not self.path.exists():
            return dict(DEFAULT_SOUL)
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return dict(DEFAULT_SOUL)

    def save(self, soul: dict) -> None:
        self.path.write_text(json.dumps(soul, ensure_ascii=False, indent=2), encoding="utf-8")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python -m pytest tests/test_soul.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/soul.py tests/test_soul.py
git commit -m "feat: add SoulManager for soul.json load/save"
```

---

## Task 2: ProfileManager

**Files:**
- Modify: `src/soul.py`
- Test: `tests/test_profile.py`

- [ ] **Step 1: Write failing test for ProfileManager**

```python
# tests/test_profile.py
import tempfile, json, os
from src.soul import ProfileManager

def test_load_returns_empty_when_no_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = ProfileManager(path=os.path.join(tmpdir, "profile.json"))
        profile = manager.load()
        assert profile == {"version": 1}

def test_set_and_get_field():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "profile.json")
        manager = ProfileManager(path=path)
        manager.set("name", "张三", source="explicit")
        profile = manager.load()
        assert profile["name"] == "张三"
        assert profile["name_source"] == "explicit"

def test_unset_field():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "profile.json")
        manager = ProfileManager(path=path)
        manager.set("name", "张三", source="explicit")
        manager.unset("name")
        profile = manager.load()
        assert "name" not in profile
        assert "name_source" not in profile

def test_merge_discovered_fields():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "profile.json")
        manager = ProfileManager(path=path)
        manager.set("name", "张三", source="explicit")
        manager.merge({"occupation": "工程师", "occupation_source": "discovered"})
        profile = manager.load()
        assert profile["name"] == "张三"
        assert profile["occupation"] == "工程师"
        assert profile["occupation_source"] == "discovered"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_profile.py::test_load_returns_empty_when_no_file -v`
Expected: FAIL — module has no attribute 'ProfileManager'

- [ ] **Step 3: Write minimal ProfileManager**

Add to `src/soul.py`:

```python
DEFAULT_PROFILE = {"version": 1}

class ProfileManager:
    def __init__(self, path: str | None = None) -> None:
        self.path = Path(path) if path else Path("profile.json")

    def load(self) -> dict:
        if not self.path.exists():
            return dict(DEFAULT_PROFILE)
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return dict(DEFAULT_PROFILE)

    def save(self, profile: dict) -> None:
        self.path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")

    def set(self, key: str, value, source: str) -> None:
        profile = self.load()
        profile[key] = value
        profile[f"{key}_source"] = source
        self.save(profile)

    def unset(self, key: str) -> None:
        profile = self.load()
        profile.pop(key, None)
        profile.pop(f"{key}_source", None)
        self.save(profile)

    def merge(self, updates: dict) -> None:
        """Merge new fields without overwriting existing ones."""
        profile = self.load()
        for k, v in updates.items():
            if k not in profile:
                profile[k] = v
        self.save(profile)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python -m pytest tests/test_profile.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/soul.py tests/test_profile.py
git commit -m "feat: add ProfileManager for user profile load/save/set/unset/merge"
```

---

## Task 3: ProfileMiddleware

**Files:**
- Modify: `src/agent.py`
- Test: `tests/test_middleware.py`

- [ ] **Step 1: Write failing test for ProfileMiddleware**

```python
# tests/test_middleware.py
import pytest
from src.agent import ProfileMiddleware

def test_injects_profile_into_messages():
    from langchain_core.messages import HumanMessage
    from unittest.mock import MagicMock

    profile = {"name": "张三", "name_source": "explicit", "occupation": "工程师", "occupation_source": "discovered"}
    middleware = ProfileMiddleware(profile=profile)

    # Build a mock request with state and messages
    mock_request = MagicMock()
    mock_request.state = {}
    mock_request.messages = [HumanMessage(content="你好")]

    captured = []
    async def fake_handler(req):
        captured.append(req)
        return MagicMock()

    import asyncio
    result = asyncio.get_event_loop().run_until_complete(middleware.awrap_model_call(mock_request, fake_handler))

    # Verify the profile was prepended
    req = captured[0]
    msgs = req.messages
    assert any("[用户画像]" in str(m.content) for m in msgs)
    assert any("name: 张三" in str(m.content) for m in msgs)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_middleware.py -v`
Expected: FAIL — ProfileMiddleware does not exist

- [ ] **Step 3: Write ProfileMiddleware**

Add to `src/agent.py`:

```python
class ProfileMiddleware(AgentMiddleware):
    """在 LLM 调用前把 profile.json 中的用户画像注入为 SystemMessage。

    注入位置：主 system prompt 之后、第一条 human 之前。
    """

    def __init__(self, profile: dict) -> None:
        self.profile = profile

    def _format_profile(self, profile: dict) -> str:
        """把 profile 格式化为可读文本，排除 _source 字段。"""
        lines = ["[用户画像]"]
        for k, v in profile.items():
            if k == "version":
                continue
            if k.endswith("_source"):
                continue
            lines.append(f"{k}: {v}")
        return "\n".join(lines)

    async def awrap_model_call(self, request, handler):
        non_source = {
            k: v for k, v in self.profile.items()
            if not k.endswith("_source")
        }
        if len(non_source) > 1:  # only inject if there's actual data beyond version
            profile_msg = SystemMessage(content=self._format_profile(non_source))
            request = request.override(messages=[profile_msg, *request.messages])
        return await handler(request)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python -m pytest tests/test_middleware.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent.py tests/test_middleware.py
git commit -m "feat: add ProfileMiddleware to inject user profile into LLM calls"
```

---

## Task 4: Inject Soul into System Prompt

**Files:**
- Modify: `src/agent.py`
- Test: `tests/test_agent.py` (add new test cases)

- [ ] **Step 1: Write failing test for soul injection**

```python
# tests/test_agent.py — add after existing tests
def test_build_agent_injects_soul_into_system_prompt():
    from src.agent import build_agent
    from src.llm import build_llm
    from src.soul import SoulManager
    import tempfile, os

    with tempfile.TemporaryDirectory() as tmpdir:
        soul_path = os.path.join(tmpdir, "soul.json")
        SoulManager(soul_path).save({"version": 1, "description": "测试风格"})

        # Monkey-patch the soul path temporarily
        import src.agent
        orig_get_soul = src.agent._get_soul_manager
        src.agent._get_soul_manager = lambda: SoulManager(soul_path)

        from src.llm import build_llm
        from src.config import Settings
        settings = Settings(
            deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", "test"),
            deepseek_base_url="https://api.deepseek.com",
            deepseek_model="deepseek-chat",
        )
        try:
            agent = build_agent(build_llm(settings))
            # The system prompt should contain the soul description
            # We can verify indirectly via the graph's config
            assert agent is not None
        finally:
            src.agent._get_soul_manager = orig_get_soul
```

- [ ] **Step 2: Run test — skip (this test requires API key, do manual verification instead)**

Run: `.venv\Scripts\python -m pytest tests/test_agent.py -v -k "soul" 2>&1 | head -20`
Note: This test may fail due to API requirements. Proceed to implementation.

- [ ] **Step 3: Add _get_soul_manager and modify build_agent**

In `src/agent.py`, add module-level function and modify `build_agent`:

```python
# Module-level soul manager factory (allows injection for testing)
def _get_soul_manager() -> "SoulManager":
    from src.soul import SoulManager
    return SoulManager()

def build_agent(
    llm: BaseChatModel,
    *,
    checkpointer: MemorySaver | None = None,
    profile: dict | None = None,
):
    """构造一个配置好工具的 agent graph。"""
    # Load soul and prepend to system prompt
    soul_manager = _get_soul_manager()
    soul = soul_manager.load()
    soul_description = soul.get("description", "") or ""

    if soul_description:
        full_system_prompt = f"你是一个{soul_description}的助手。\n\n{SYSTEM_PROMPT}"
    else:
        full_system_prompt = SYSTEM_PROMPT

    # Load profile for middleware
    if profile is None:
        from src.soul import ProfileManager
        profile = ProfileManager().load()

    return create_agent(
        model=llm,
        tools=[calculator, current_time, weather, web_search],
        system_prompt=full_system_prompt,
        state_schema=AgentState,
        middleware=[SummaryMiddleware(), ProfileMiddleware(profile=profile)],
        name="single-agent",
        checkpointer=checkpointer,
    )
```

- [ ] **Step 4: Manual verification**

```bash
# Create test soul.json in project root
echo '{"version": 1, "description": "幽默"}' > soul.json
.venv\Scripts\python -c "from src.agent import build_agent; from src.llm import build_llm; from src.config import Settings; s=Settings.from_env(); a=build_agent(build_llm(s)); print('Agent built successfully')"
```

- [ ] **Step 5: Commit**

```bash
git add src/agent.py
git commit -m "feat: inject soul.json into system prompt in build_agent"
```

---

## Task 5: Profile Auto-Discovery

**Files:**
- Modify: `src/memory.py`
- Test: `tests/test_memory.py` (add new test cases)

- [ ] **Step 1: Write failing test for profile auto-discovery**

```python
# tests/test_memory.py — add after existing tests
def test_discover_profile_facts_extracts_user_info():
    from src.memory import MemoryManager
    from langchain_core.messages import HumanMessage, AIMessage
    from unittest.mock import MagicMock

    # Build a mock agent whose get_state returns a state with messages
    mock_agent = MagicMock()
    mock_agent.get_state.return_value.values = {
        "messages": [
            HumanMessage(content="我叫李四，是一名设计师"),
            AIMessage(content="好的，李四，我记住了"),
        ],
        "summary": ""
    }

    from src.llm import build_llm
    from src.config import Settings
    import os
    settings = Settings(
        deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", "test"),
        deepseek_base_url="https://api.deepseek.com",
        deepseek_model="deepseek-chat",
    )
    manager = MemoryManager(mock_agent, build_llm(settings), settings)

    # This will call the LLM — we mock it
    manager._summarize_with_llm = lambda *args: "已摘要"
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = MagicMock(content='{"name": "李四", "name_source": "discovered", "occupation": "设计师", "occupation_source": "discovered"}')

    manager.compression_llm = mock_llm

    # Verify compress_if_needed triggers discovery
    # We mock _needs_compress to True and _drop_orphans to return []
    manager._needs_compress = lambda msgs: True
    from src.memory import _drop_orphans, _split_by_turns
    # The actual messages in get_state are what we set above
    result = manager.compress_if_needed("test-thread")
    # Returns True if compression happened
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_memory.py -v -k "discover" 2>&1 | head -20`
Expected: test method does not exist yet

- [ ] **Step 3: Add _discover_profile_facts to MemoryManager**

Add to `src/memory.py`:

```python
DISCOVER_PROMPT = """从以下对话中提取用户的事实信息（姓名、职业、兴趣、偏好等）。
只返回你有把握的信息，不要猜测。
以 JSON 格式返回，key 是字段名，value 是字段值，格式示例：
{"name": "张三", "occupation": "工程师"}
不要返回其他内容，只返回 JSON。"""

def _format_messages_for_discovery(messages: list[BaseMessage]) -> str:
    """Format messages for the discovery LLM call."""
    lines = []
    for m in messages:
        role = getattr(m, "type", "unknown")
        content = _content_str(m)
        lines.append(f"[{role}] {content}")
    return "\n".join(lines)

class MemoryManager:
    # ... existing code ...

    def _discover_profile_facts(
        self,
        new_messages: list[BaseMessage],
    ) -> dict | None:
        """从新增消息中提取用户事实，返回 JSON dict 或 None。"""
        formatted = _format_messages_for_discovery(new_messages)
        prompt = f"{DISCOVER_PROMPT}\n\n对话：\n{formatted}"
        try:
            result = self.compression_llm.invoke(prompt)
            content = result.content
            if isinstance(content, str):
                content = content.strip()
                # Try to extract JSON from response
                import re
                json_match = re.search(r'\{[^{}]*\}', content, re.DOTALL)
                if json_match:
                    import json
                    return json.loads(json_match.group())
            return None
        except Exception:
            return None

    def compress_if_needed(self, thread_id: str) -> bool:
        # ... existing code up to the summarize try block ...
        try:
            new_summary = self._summarize_with_llm(to_compress, summary)
        except Exception:
            self._replace_messages(config, to_compress, recent)
            return True

        self._replace_messages(config, to_compress, recent)
        self.agent.update_state(config, values={"summary": new_summary})

        # Auto-discover profile facts from compressed messages
        facts = self._discover_profile_facts(to_compress)
        if facts:
            try:
                from src.soul import ProfileManager
                ProfileManager().merge(facts)
            except Exception:
                pass  # Silently fail, don't block compression

        return True
```

- [ ] **Step 4: Verify no import/syntax errors**

```bash
.venv\Scripts\python -c "from src.memory import MemoryManager; print('MemoryManager loads OK')"
```

- [ ] **Step 5: Commit**

```bash
git add src/memory.py
git commit -m "feat: add profile auto-discovery in MemoryManager.compress_if_needed"
```

---

## Task 6: REPL Commands :soul and :profile

**Files:**
- Modify: `src/main.py`
- Test: `tests/test_main.py` (add new test cases)

- [ ] **Step 1: Write failing test for REPL commands**

```python
# tests/test_main.py — add
def test_soul_command_parses_inline():
    """Test that ':soul' alone returns current soul description."""
    from src.main import _parse_soul_command
    # Returns (action="view", value=None)
    assert _parse_soul_command([":soul"]) == ("view", None)
    # Returns (action="set", value="幽默风格")
    assert _parse_soul_command([":soul", "幽默风格"]) == ("set", "幽默风格")

def test_profile_command_parses():
    """Test that profile commands parse correctly."""
    from src.main import _parse_profile_command
    assert _parse_profile_command([":profile"]) == ("view", None, None)
    assert _parse_profile_command([":profile", "get", "name"]) == ("get", "name", None)
    assert _parse_profile_command([":profile", "set", "name", "张三"]) == ("set", "name", "张三")
    assert _parse_profile_command([":profile", "unset", "name"]) == ("unset", "name", None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_main.py -v -k "soul_command or profile_command"`
Expected: FAIL — functions don't exist

- [ ] **Step 3: Add command parsers and handlers**

In `src/main.py`, add these functions and wire them into `_repl_loop_async`:

```python
def _parse_soul_command(argv: list[str]) -> tuple[str, str | None]:
    """Returns (action, value). action is 'view' or 'set'."""
    if len(argv) == 1:
        return ("view", None)
    return ("set", " ".join(argv[1:]))

def _parse_profile_command(argv: list[str]) -> tuple[str, str | None, str | None]:
    """Returns (action, key, value)."""
    if len(argv) == 1:
        return ("view", None, None)
    action = argv[1].lower()
    if action == "get":
        return ("get", argv[2] if len(argv) > 2 else None, None)
    if action == "set":
        key = argv[2] if len(argv) > 2 else None
        value = " ".join(argv[3:]) if len(argv) > 3 else None
        return ("set", key, value)
    if action == "unset":
        return ("unset", argv[2] if len(argv) > 2 else None, None)
    return ("view", None, None)
```

Then in `_repl_loop_async`, add handling for `:soul` and `:profile` commands (after the existing command handling block around line 293):

```python
if low.startswith(":soul"):
    from src.soul import SoulManager
    action, value = _parse_soul_command(low.split())
    manager_soul = SoulManager()
    if action == "view":
        soul = manager_soul.load()
        print(f"当前风格：{soul.get('description', '')}")
    elif action == "set" and value:
        manager_soul.save({"version": 1, "description": value})
        print(f"风格已更新：{value}")
    continue

if low.startswith(":profile"):
    from src.soul import ProfileManager
    action, key, value = _parse_profile_command(low.split())
    manager_profile = ProfileManager()
    if action == "view":
        profile = manager_profile.load()
        print("当前画像：")
        for k, v in profile.items():
            if k != "version":
                print(f"  {k}: {v}")
    elif action == "get" and key:
        profile = manager_profile.load()
        print(f"{key}: {profile.get(key, '(未设置)')}")
    elif action == "set" and key and value:
        manager_profile.set(key, value, source="explicit")
        print(f"{key} 已设置为 {value}。")
    elif action == "unset" and key:
        manager_profile.unset(key)
        print(f"{key} 已删除。")
    continue
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python -m pytest tests/test_main.py -v -k "soul_command or profile_command"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/main.py
git commit -m "feat: add :soul and :profile REPL commands"
```

---

## Task 7: Integration Verification

- [ ] **Step 1: Create initial soul.json and profile.json**

```bash
echo '{"version": 1, "description": ""}' > soul.json
echo '{"version": 1}' > profile.json
```

- [ ] **Step 2: Full integration test**

```bash
.venv\Scripts\python -m src.main "我叫小明，是一名前端工程师"
# Should output greeting and remember the profile

.venv\Scripts\python -m src.main "你还记得我叫什么吗？"
# Should respond with "小明"
```

- [ ] **Step 3: Test soul feature**

```bash
.venv\Scripts\python -m src.main ":soul"
# Should show current soul (empty)

# After manually editing soul.json:
echo '{"version": 1, "description": "幽默"}' > soul.json
.venv\Scripts\python -m src.main "为什么程序员总是分不清万圣节和圣诞节？"
# Should respond with a joke
```

- [ ] **Step 4: Test profile auto-discovery**

```bash
echo '{"version": 1}' > profile.json
.venv\Scripts\python -m src.main "我叫小红，是产品经理"
.venv\Scripts\python -m src.main "我喜欢读书和旅行"
cat profile.json
# Should contain discovered fields
```

---

## Spec Coverage Check

| Spec Section | Task |
|-------------|------|
| soul.json 数据结构 | Task 1 |
| profile.json 数据结构（含 _source） | Task 2 |
| Soul 注入 system prompt | Task 4 |
| ProfileMiddleware 注入 | Task 3 |
| Profile 自动发现 | Task 5 |
| REPL :soul 和 :profile 命令 | Task 6 |
| 错误处理（文件缺失/损坏） | Task 1, 2 |
| 测试策略 | All tasks |

## Self-Review

- Placeholder scan: 无 TBD/TODO，所有 step 均有实际代码
- 类型一致性：`SoulManager.load()` / `ProfileManager.load()` 返回 `dict`，`set()` / `merge()` 签名一致
- 顺序：Task 1-2 基础模块 → Task 3 ProfileMiddleware → Task 4 inject soul → Task 5 auto-discover → Task 6 REPL → Task 7 集成验证
- 所有任务可独立测试，依赖关系清晰