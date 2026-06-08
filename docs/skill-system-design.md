# Skill 能力适配系统 — 设计文档

## 1. 背景与目标

当前 Agent 的能力是**静态编译的**：所有工具硬编码在 `src/tools/__init__.py`，系统提示词全部堆在 `SYSTEM_PROMPT` 一个字符串里。要增加能力（如画图、数据查询、自动化测试），需要改代码重启，无法按需加载或用户自配置。

**目标**：引入 **Skill 系统**，让能力可以：

- 以独立包形式**声明式定义**（元信息 + 工具 + 上下文提示 + 中间件）
- 按**会话级别动态加载/卸载**（用户或 Agent 自主启用/禁用）
- 通过 LLM **意图识别自动触发**（Supervisor 检测到用户需求时自动激活相关 Skill）
- **向后兼容** — 已有代码不感知 Skill 系统的存在也能正常工作

## 2. 行业参考分析

| 方案 | 核心机制 | 对本设计的启发 |
|------|---------|---------------|
| **OpenAI Skills API** | 三段式：Inline（请求内嵌入）/ Local（本地路径）/ Reference（ID 引用），支持版本管理 | Skill 定义与加载解耦，`name` + `description` 是关键元信息 |
| **Claude Code Skills** | 文件头 frontmatter + 独立目录，可通过 `/skill-name` 或触发条件调用 | 声明式 frontmatter + 触发条件（keywords/patterns）值得借鉴 |
| **LangChain Tools** | 函数装饰器 + 输入 schema，Agent 根据 description 自主选择 | Skill 底层的工具复用了现成的 `@tool` 机制，不引入新范式 |
| **AutoGPT Plugins** | 完整的插件生命周期：加载→初始化→执行→卸载 | Plugin 的 `enabled/disabled` 状态管理可简化后用于 Skill |
| **Cursor Rules** | 分层规则：项目级 → 目录级 → 文件级，按上下文自动匹配 | Skill 的激活策略除了手动，也可以基于消息内容自动匹配 |

### 关键洞察

1. **Skill 本质 = 命名空间化的能力包**，一个 Skill = 元信息 + 工具 + 系统提示注入
2. **声明优于代码** —— Skill 元信息用 YAML/frontmatter 定义，工具用既有 `@tool` 装饰器
3. **分层激活** —— 系统级（始终启用）→ 会话级（手动开关）→ 任务级（意图自动匹配）
4. **零侵入** —— 无 Skill 时系统行为完全不变

## 3. 架构设计

### 3.1 Skill 定义模型

```python
@dataclass
class SkillDefinition:
    """Skill 的运行时表示。"""
    name: str                         # 全局唯一标识符
    description: str                  # LLM 可读的描述（用于意图匹配）
    version: str                      # 语义版本
    author: str                       # 创建者
    tools: list[Callable]             # LangChain @tool 函数列表
    prompt_injection: str             # 注入系统提示的文本
    middleware: list[AgentMiddleware]  # 自定义中间件（可选）
    triggers: SkillTriggers           # 触发条件配置
    enabled_by_default: bool          # 是否默认启用
    categories: list[str]             # 分类标签
```

### 3.2 触发条件模型

```python
@dataclass
class SkillTriggers:
    """定义 Skill 在何种条件下被自动激活。"""
    keywords: list[str] = field(default_factory=list)      # 关键词匹配
    patterns: list[str] = field(default_factory=list)       # 正则模式
    auto_load: bool = False                                 # 是否允许 LLM 自动加载
    required_permission: str = "user"                       # user / admin
```

### 3.3 Skill 文件结构

```
src/skills/
├── __init__.py                        # 导出注册表
├── registry.py                        # SkillRegistry（扫描、加载、查询）
├── schema.py                          # SkillDefinition + SkillTriggers 数据模型
├── middleware.py                       # SkillContextMiddleware
├── data_analysis/                     # 示例 Skill：数据分析
│   ├── skill.yaml                     # 元信息声明
│   ├── __init__.py
│   └── tools.py                       # @tool 装饰函数
├── web_automation/                    # 示例 Skill：网页自动化
│   ├── skill.yaml
│   ├── __init__.py
│   └── tools.py
└── weather_advanced/                  # 示例 Skill：增强天气
    ├── skill.yaml
    ├── __init__.py
    └── tools.py
```

### 3.4 skill.yaml 格式

```yaml
name: data_analysis
description: 数据分析和可视化能力，支持 CSV/Excel 读取、统计计算、图表绘制
version: 1.0.0
author: system
enabled_by_default: false
categories:
  - data
  - analysis

triggers:
  keywords:
    - 分析数据
    - 画图
    - 图表
    - 统计
    - 可视化
    - correlation
  patterns:
    - "(?i)plot\\s+\\w+"
    - "(?i)(scatter|bar|line|histogram)\\s+(chart|plot|graph)"
  auto_load: true

prompt_injection: |
  你拥有数据分析和可视化能力。可以：
  1. 读取 CSV/Excel/JSON 数据文件
  2. 计算基本统计量（均值、中位数、标准差、相关性）
  3. 使用 matplotlib/seaborn 绘制图表（散点图、折线图、柱状图、直方图、箱线图）
  4. 数据清洗与格式转换
  
  绘图时使用中文字体（SimHei 或 Microsoft YaHei），
  避免图表中出现乱码。
```

## 4. 详细实现

### 4.1 SkillRegistry（`src/skills/registry.py`）

```python
class SkillRegistry:
    """Skill 注册中心：负责扫描、加载、查询 Skill 定义。"""

    def __init__(self, skills_dir: str | Path = "src/skills"):
        self._skills_dir = Path(skills_dir)
        self._skills: dict[str, SkillDefinition] = {}
        self._initialized = False

    def discover(self) -> dict[str, SkillDefinition]:
        """扫描 src/skills/ 下所有含 skill.yaml 的目录，加载定义和工具。
        
        幂等执行：首次调用建立完整注册表，之后只检测新增目录。
        """
        ...

    def get(self, name: str) -> SkillDefinition | None:
        ...

    def list_all(self) -> list[SkillDefinition]:
        """返回所有已注册的 Skill 定义。"""
        ...

    def get_by_category(self, category: str) -> list[SkillDefinition]:
        ...

    def match_by_message(self, message: str) -> list[SkillDefinition]:
        """根据用户消息的文本内容，返回 top-k 匹配的 Skill 列表。
        
        实现思路（两阶段）：
        1. 快速筛选：keywords/patterns 命中
        2. LLM 精排（可选）：对候选用 LLM 判断相关性
        """
        ...
```

### 4.2 SkillSessionStore（`src/store/skills.py`）

```python
class SkillSessionStore:
    """会话级 Skill 状态存储。
    
    记录每个会话的启用/禁用 Skill 列表。
    以后可扩展为每个 Skill 的配置（如 API Key）。
    """

    def __init__(self, path: str | Path = "data/.skills.json"):
        self.path = Path(path)

    def get_enabled(self, session_id: str) -> set[str]:
        """返回会话已启用的 Skill 名称集合。"""

    def enable(self, session_id: str, skill_name: str) -> None:
        """启用 Skill。"""

    def disable(self, session_id: str, skill_name: str) -> None:
        """禁用 Skill。"""
```

### 4.3 SkillContextMiddleware（`src/skills/middleware.py`）

```python
class SkillContextMiddleware(AgentMiddleware):
    """在 LLM 调用前注入已启用 Skill 的上下文信息。
    
    注入位置：SystemMessage 列表尾部，紧挨着主 system prompt。
    格式：
    
    [已启用的技能]
    - data_analysis: 数据分析和可视化能力...
    
    [data_analysis 使用说明]
    你拥有数据分析和可视化能力...
    """

    def __init__(self, registry: SkillRegistry, session_id: str, 
                 store: SkillSessionStore | None = None):
        self.registry = registry
        self.session_id = session_id
        self._store = store or SkillSessionStore()
        self._last_active: tuple[str, ...] = ()

    async def awrap_model_call(self, request, handler):
        enabled = self._store.get_enabled(self.session_id)
        if not enabled:
            return await handler(request)

        # 准备注入文本（来自 skill.prompt_injection）
        context_parts = []
        for name in sorted(enabled):
            skill = self.registry.get(name)
            if skill and skill.prompt_injection:
                context_parts.append(
                    f"[{skill.name}]\n{skill.prompt_injection}"
                )

        if not context_parts:
            return await handler(request)

        skill_text = "已启用的技能：\n" + "\n\n".join(context_parts)
        skill_msg = SystemMessage(content=skill_text)
        request = request.override(
            messages=[*request.messages, skill_msg]
        )
        return await handler(request)
```

### 4.4 Builder 集成（`src/agent/builder.py`）

```python
def build_agent(
    llm: BaseChatModel,
    *,
    checkpointer: MemorySaver | None = None,
    profile: dict | None = None,
    memory_store: MemoryStore | None = None,
    session_id: str | None = None,             # ← NEW
    skill_registry: SkillRegistry | None = None, # ← NEW
) -> AgentBundle:
    """构造一个配置好工具的 agent graph。

    Args:
        ...
        session_id: 会话 ID，用于加载会话级别的 Skill 配置。
        skill_registry: Skill 注册表，None 则跳过 Skill 加载。
    """
    # ... 现有逻辑 ...

    # ── Skill 集成点 ──
    active_tools = [
        calculator, current_time, weather, web_search, search, web_fetch,
        list_files, read_file, grep_search, create_folder, write_file, 
        run_python, shell, delegate_tool,
    ]
    middleware_list = [SummaryMiddleware(), profile_middleware, memory_middleware]

    if skill_registry and session_id:
        skill_registry.discover()
        enabled = skill_registry.get_session_enabled_skills(session_id)
        for skill_name in enabled:
            skill = skill_registry.get(skill_name)
            if skill:
                active_tools.extend(skill.tools)      # 注入 Skill 的工具
                if skill.middleware:
                    middleware_list.extend(skill.middleware)  # 注入中间件
        
        # 始终注入 SkillContextMiddleware（即使没有启用 Skill，也为动态激活做准备）
        skill_middleware = SkillContextMiddleware(
            registry=skill_registry,
            session_id=session_id,
        )
        middleware_list.append(skill_middleware)

    agent = create_agent(
        model=llm,
        tools=active_tools,
        system_prompt=system_prompt,
        state_schema=AgentState,
        middleware=middleware_list,
        name="single-agent",
        checkpointer=checkpointer,
    )

    return AgentBundle(...)
```

### 4.5 Supervisor 集成（`src/agent/supervisor.py`）

Supervisor 的 `intent_router` 结合 Skill 系统：

```
用户消息 → intent_router
  │
  ├─ "direct" → 已有 agent 处理
  │   └─ agent 配置了 SkillContextMiddleware → 已启用 Skill 自动生效
  │
  └─ "plan_and_execute" → planner → step_dispatcher
      └─ step_dispatcher 检测到子任务需要某 Skill
          └─ 「自动激活 Skill」→ 创建 sub-agent 时包含该 Skill 的工具
```

**Sub-agent 的 Skill 支持**（`sub_agent.py`）：

```python
def create_sub_agent(
    llm: BaseChatModel,
    *,
    tools: list[Any] | None = None,
    prompt: str | None = None,
    skills: list[SkillDefinition] | None = None,  # ← NEW
):
    """创建一个隔离的 sub-agent。

    如果传入了 skills，将 skills 的工具合并到 tools 中，
    并将 prompt_injection 追加到 sub-agent 的系统提示词后。
    """
    base_tools = tools or REGULAR_TOOLS
    base_prompt = prompt or SUB_AGENT_PROMPT

    if skills:
        for skill in skills:
            base_tools = [*base_tools, *skill.tools]
            if skill.prompt_injection:
                base_prompt += f"\n\n[{skill.name}]\n{skill.prompt_injection}"

    return create_agent(
        model=llm,
        tools=base_tools,
        system_prompt=base_prompt,
        checkpointer=MemorySaver(),
        name="sub-agent",
    )
```

### 4.6 动态 Skill 激活（Supervisor 意图路由增强）

在 `intent_router` 中增加 Skill 匹配逻辑：

```python
async def intent_router(state: AgentState, config) -> str:
    """意图路由：识别任务复杂度，同时检测是否需要加载 Skill。"""
    last_msg = state["messages"][-1].content if state["messages"] else ""
    
    # ── Skill 自动匹配 ──
    if skill_registry and config.get("configurable", {}).get("session_id"):
        session_id = config["configurable"]["session_id"]
        matched = skill_registry.match_by_message(last_msg)
        if matched:
            store = SkillSessionStore()
            for skill in matched:
                if skill.triggers.auto_load:
                    store.enable(session_id, skill.name)
    
    # ── 原有路由逻辑 ──
    ...
```

### 4.7 API 层（`frontend/bridge.py`）

```python
class Api:
    def __init__(self, ...):
        ...
        self._skill_registry = SkillRegistry()
        self._skill_store = SkillSessionStore()

    # ── Skill 管理 ──

    def get_skills(self) -> list[dict]:
        """返回所有可用 Skill 的元信息（不含工具实现细节）。"""
        return [
            {
                "name": s.name,
                "description": s.description,
                "version": s.version,
                "author": s.author,
                "enabled_by_default": s.enabled_by_default,
                "categories": s.categories,
                "triggers": asdict(s.triggers),
            }
            for s in self._skill_registry.list_all()
        ]

    def get_enabled_skills(self, session_id: str) -> list[str]:
        return sorted(self._skill_store.get_enabled(session_id))

    def enable_skill(self, session_id: str, skill_name: str) -> dict:
        skill = self._skill_registry.get(skill_name)
        if not skill:
            return {"success": False, "error": f"Skill '{skill_name}' 不存在"}
        self._skill_store.enable(session_id, skill_name)
        return {"success": True}

    def disable_skill(self, session_id: str, skill_name: str) -> dict:
        self._skill_store.disable(session_id, skill_name)
        return {"success": True}

    def get_skill_detail(self, skill_name: str) -> dict | None:
        skill = self._skill_registry.get(skill_name)
        if not skill:
            return None
        return {
            "name": skill.name,
            "description": skill.description,
            "version": skill.version,
            "author": skill.author,
            "enabled_by_default": skill.enabled_by_default,
            "categories": skill.categories,
            "triggers": asdict(skill.triggers),
            "prompt_injection": skill.prompt_injection,
            "tools": [t.name for t in skill.tools],
        }
```

### 4.8 HTTP 路由（`frontend/http_server.py`）

```python
def setup_routes(app: web.Application) -> None:
    ...
    # Skill 管理
    app.router.add_route("GET",  "/api/skills", get_skills)
    app.router.add_route("GET",  "/api/skills/{name}", get_skill_detail)
    app.router.add_route("GET",  "/api/sessions/{thread_id}/skills/enabled", get_enabled_skills)
    app.router.add_route("POST", "/api/sessions/{thread_id}/skills/enable", post_enable_skill)
    app.router.add_route("POST", "/api/sessions/{thread_id}/skills/disable", post_disable_skill)
```

## 5. 与现有系统的关系

```
┌─────────────────────────────────────────────────────┐
│  Agent 运行时                                          │
│  ┌─────────┐  ┌──────────┐  ┌──────────────────┐    │
│  │ Built-in │  │ Skill    │  │ SkillContext     │    │
│  │ Tools    │ + │ Tools    │  │ Middleware       │    │
│  └─────────┘  └──────────┘  └────────┬─────────┘    │
│                                       │               │
│                         ┌─────────────▼────────┐     │
│                         │ System Prompt        │     │
│                         │ (SYSTEM_PROMPT +     │     │
│                         │  Soul +              │     │
│                         │  Skill Injections)   │     │
│                         └──────────────────────┘     │
│  ┌─────────┐  ┌──────────┐  ┌──────────────────┐    │
│  │ Summary │  │ Profile  │  │ Memory           │    │
│  │ Middle.  │  │ Middle.  │  │ Middleware       │    │
│  └─────────┘  └──────────┘  └──────────────────┘    │
└─────────────────────────────────────────────────────┘

注入顺序（从上到下，越晚优先级越高）：
  1. SummaryMiddleware（[对话历史摘要]）
  2. ProfileMiddleware（[用户画像]）
  3. MemoryMiddleware（[关于用户]）
  4. SkillContextMiddleware（[已启用的技能]）  ← NEW
  5. System Prompt（主提示词 + Soul）
```

**Skill 不修改（只扩展）现有层次**：
- Tools：Skill 增加新工具到工具列表
- Middleware：SkillContextMiddleware 是新的一层，不修改已有中间件行为
- System Prompt：Skill 的 `prompt_injection` 附加在已有系统提示词附近，不覆盖

## 6. 示例：完整的 Skill 实现

### `src/skills/code_review/skill.yaml`

```yaml
name: code_review
description: 代码审查能力，可以审查 Python/JavaScript/Go/Rust 等语言的代码质量
version: 1.0.0
author: system
enabled_by_default: false
categories:
  - development
  - code

triggers:
  keywords:
    - 审查代码
    - 代码审查
    - code review
    - 检查代码
    - 看下这段代码
  auto_load: false

prompt_injection: |
  你拥有专业的代码审查能力，可以：
  1. 识别代码中的潜在 bug、安全漏洞、性能问题
  2. 提供具体、可操作的重构建议
  3. 检查代码风格一致性
  4. 评估测试覆盖是否充分
  
  输出格式：
  - 【严重性: CRITICAL/HIGH/MEDIUM/LOW】问题描述 → 建议修复方案
  - 最终给出总体评价和改进优先级。
```

### `src/skills/code_review/tools.py`

```python
"""Code Review Skill - 仅提供提示注入，不新增工具。

使用 LLM 本身代码理解 + prompt_injection 实现审查能力。
"""

# 此 Skill 不需要额外工具，所有能力通过 prompt 注入实现
# 未来可添加 run_linter, run_type_checker 等工具
__tool_list__: list = []
```

### `src/skills/data_analysis/tools.py`

```python
"""Data Analysis Skill - 数据分析与可视化工具。"""

from langchain_core.tools import tool
import io, csv, json, statistics
from pathlib import Path
from typing import Any

@tool
def analyze_csv(file_path: str) -> str:
    """读取 CSV 文件并返回基本统计分析结果（行数、列名、每列类型与统计量）。
    
    Args:
        file_path: CSV 文件路径。
    """
    ...

@tool
def plot_chart(data: str, chart_type: str, title: str = "", 
               x_label: str = "", y_label: str = "") -> str:
    """使用 matplotlib 绘制图表并保存为图片，返回图片路径。
    
    Args:
        data: JSON 格式的数据数组 [{x: ..., y: ...}, ...]
        chart_type: 图表类型 (scatter/bar/line/histogram/box)
        title: 图表标题
        x_label: X 轴标签
        y_label: Y 轴标签
    """
    ...

__tool_list__ = [analyze_csv, plot_chart]
```

## 7. 实现路线图

### Phase 1：核心框架（预计 2-3 天）
- `src/skills/schema.py` — 数据模型
- `src/skills/registry.py` — 目录扫描 + YAML 加载 + 工具导入
- `src/skills/middleware.py` — SkillContextMiddleware
- `src/store/skills.py` — SkillSessionStore（JSON 文件持久化）
- `src/agent/builder.py` 改造 — 接受 session_id + skill_registry
- 单元测试覆盖

### Phase 2：API 与前端集成（1-2 天）
- `frontend/bridge.py` — 新增 Skill 相关方法
- `frontend/http_server.py` — 新增路由
- 前端 UI 的 Skills 管理面板（启用/禁用按钮）
- 集成测试

### Phase 3：Supervisor 深度集成（1 天）
- `intent_router` 自动 Skill 匹配激活
- Sub-agent 继承主会话的已启用 Skill
- 动态 Skill 加载的安全边界

### Phase 4：示例 Skill 开发（持续）
- `data_analysis` — 数据分析（CSV 读取 + matplotlib）
- `code_review` — 代码审查（纯提示注入）
- 更多社区贡献的 Skill

## 8. 设计决策记录

| 决策 | 选项 | 选择 | 理由 |
|------|------|------|------|
| Skill 定义格式 | YAML vs TOML vs JSON | **YAML** | 可读性最好，支持注释，Claude Code 使用相同格式 |
| Skill 发现机制 | 显式注册 vs 文件系统扫描 | **文件系统扫描** | 零配置，新增 Skill 目录即可自动发现 |
| 工具加载时机 | 启动时全量加载 vs 懒加载 | **启动时全量** | Skill 数量有限（<100），全量加载简单可靠 |
| 状态持久化 | 独立文件 vs 合并到 sessions.json | **独立文件** | 职责单一，不侵入 SessionRegistry |
| 默认激活 | enabled_by_default 控制 | **部分内置 Skill 开启** | code_review 关闭，weather_advanced 可开启 |
| Session ID 传递 | 通过 config 参数 vs 隐式全局 | **config 参数** | 与 LangGraph 既有模式一致，线程安全 |
| Sub-agent 继承 | 同步继承 vs 独立 | **同步继承** | Supervisor 创建 sub-agent 时传入当前启用的 Skill |

## 9. 向后兼容与迁移

- `build_agent()` 的新参数 `session_id` 和 `skill_registry` 均为可选，不传时行为完全不变
- 已有测试不需要修改
- 已有 `src/tools/` 中的工具不受影响
- Skill 目录只在 `src/skills/` 下，不会修改现有文件
- `data/.skills.json` 不存在时等同于空列表
