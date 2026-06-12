# MiaoGent — AI 助手框架项目讲解

> 本文档面向面试讲解，全面梳理 MiaoGent 的架构设计、核心模块、请求链路、各功能深度解析，以及 LangChain/LangGraph 框架的具体使用方式。

---

## 目录

1. [项目概览](#1-项目概览)
2. [整体架构](#2-整体架构)
3. [完整请求链路](#3-完整请求链路)
4. [核心模块详解](#4-核心模块详解)
   - [4.1 配置与 LLM 工厂](#41-配置与-llm-工厂)
   - [4.2 Agent 构建与中间件链](#42-agent-构建与中间件链)
   - [4.3 上下文管理](#43-上下文管理)
   - [4.4 工具系统](#44-工具系统)
   - [4.5 Shell 子系统（四层语义安全门）](#45-shell-子系统四层语义安全门)
   - [4.6 记忆系统](#46-记忆系统)
   - [4.7 Skill 系统](#47-skill-系统)
   - [4.8 ReWOO 规划-执行模式](#48-rewoo-规划-执行模式)
   - [4.9 Sub-Agent 委派与隔离](#49-sub-agent-委派与隔离)
   - [4.10 链路追踪系统](#410-链路追踪系统)
   - [4.11 持久化与存储](#411-持久化与存储)
5. [LangChain/LangGraph 框架使用详解](#5-langchainlanggraph-框架使用详解)
   - [5.1 整体定位：LangChain/LangGraph 在本项目中扮演的角色](#51-整体定位langchainlanggraph-在本项目中扮演的角色)
   - [5.2 create_agent：Agent 构建入口](#52-create_agentagent-构建入口)
   - [5.3 AgentMiddleware 中间件链](#53-agentmiddleware-中间件链)
   - [5.4 BaseCallbackHandler 回调系统](#54-basecallbackhandler-回调系统)
   - [5.5 BaseChatModel 多态 LLM 接口](#55-basechatmodel-多态-llm-接口)
   - [5.6 @tool 装饰器](#56-tool-装饰器)
   - [5.7 LangGraph 状态管理与持久化](#57-langgraph-状态管理与持久化)
   - [5.8 astream_events 流式事件](#58-astream_events-流式事件)
   - [5.9 Message 类型体系](#59-message-类型体系)
   - [5.10 举一反三：LangChain/LangGraph 设计思想](#510-举一反三langchainlanggraph-设计思想)
6. [关键设计亮点](#6-关键设计亮点)

---

## 1. 项目概览

MiaoGent 是一个 **基于 LangChain/LangGraph 的 AI 助手框架**，采用 **Python 后端 + Electron 桌面端** 架构。它不是一个简单的 LLM 封装，而是一个**完整的多 Agent 编排、工具执行、记忆管理和可观测性平台**。

### 核心能力矩阵

| 能力 | 说明 |
|------|------|
| **多 Provider 支持** | DeepSeek / OpenAI / Anthropic 三引擎切换，支持 streaming、prompt caching |
| **工具系统** | 19+ 内置工具：搜索（多引擎 fallback）、计算、文件操作、Python 沙箱、Shell 安全执行 |
| **多 Agent 编排** | Supervisor + Sub-Agent 模式，支持任务委派、隔离执行、防递归 |
| **ReWOO 模式** | 规划-并行执行-合成，将 N 次 LLM 调用降到 2 次 |
| **记忆系统** | 增量摘要 + 画像发现 + 结构化事实提取 + 知识归并 |
| **Skill 系统** | 可安装的提示注入包，支持 npm/pip/git/url 四种来源 |
| **链路追踪** | 零侵入式 span 采集，SQLite 持久化，前端瀑布图可视化 |
| **安全体系** | Shell 四层语义安全门（解析/高危/白名单/确认层） |
| **前端监控面板** | Token 统计、延迟分析、缓存命中率、Top N 追踪 |

---

## 2. 整体架构

### 分层架构图

```
┌──────────────────────────────────────────────────────────────────────┐
│                       前端层 (Electron/浏览器)                        │
│  ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐              │
│  │ Ball 模式 │ │ Chat 面板│ │ Tools 面板│ │ Monitoring │              │
│  │ (桌面宠) │ │ (流式聊天)│ │ (工具浏览)│ │ 仪表盘     │              │
│  └─────────┘ └──────────┘ └──────────┘ └────────────┘              │
│                     window.api (Electron preload)                   │
├──────────────────────────────────────────────────────────────────────┤
│                       HTTP API 层 (aiohttp)                         │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  frontend/http_server.py  — 路由注册 + 请求分发               │   │
│  │  frontend/bridge.py       — Api 类：封装所有后端操作           │   │
│  │    → 会话管理 / 设置 / Soul/Profile / 聊天 / Trace / 工具枚举  │   │
│  └──────────────────────────────────────────────────────────────┘   │
├──────────────────────────────────────────────────────────────────────┤
│                       Agent 核心层                                   │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────────────────┐     │
│  │ builder.py   │ │ sub_agent.py │ │ memory.py +              │     │
│  │ Agent 构造工厂│ │ 隔离执行单元  │ │ memory_extractor.py     │     │
│  │ 中间件装配    │ │ 防递归设计    │ │ 增量压缩 / 画像发现     │     │
│  └──────────────┘ └──────────────┘ │ 结构化提取 / 知识归并    │     │
│  ┌──────────────┐ ┌──────────────┘ └──────────────────────────┘     │
│  │ rewoo.py     │ │                ┌──────────────────────────┐     │
│  │ 规划-执行模式 │ │                │ tracing/                 │     │
│  └──────────────┘ │                │ handler / tracer / store │     │
│                   │                │ context / api / models   │     │
│                   │                └──────────────────────────┘     │
├──────────────────────────────────────────────────────────────────────┤
│                       工具与能力层                                    │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ src/tools/    — 19+ 内置 @tool 工具                          │   │
│  │   ├─ search/  — 多适配器搜索（Tavily/DDGS/Bing）+ 渐进式引擎  │   │
│  │   ├─ shell/   — 四层安全闸门 + 沙箱执行 + 审计日志            │   │
│  │   ├─ delegate_task — Sub-Agent 委派                         │   │
│  │   └─ install_skill — 技能市场安装引擎                        │   │
│  └──────────────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ src/skills/   — Skill 注册/发现/注入系统                      │   │
│  │   ├─ registry.py   — YAML 扫描 + 多格式加载                  │   │
│  │   ├─ middleware.py  — 运行时 prompt_injection 注入           │   │
│  │   └─ schema.py      — 数据模型                               │   │
│  └──────────────────────────────────────────────────────────────┘   │
├──────────────────────────────────────────────────────────────────────┤
│                      基础设施层                                       │
│  ┌──────────┐ ┌───────────┐ ┌──────────┐ ┌──────────────────┐     │
│  │ config.py│ │ llm.py    │ │ miaogent_│ │ store/   持久化   │     │
│  │ Settings  │ │ LLM 工厂  │ │ home.py  │ │ sessions / soul  │     │
│  │ 数据类    │ │ 多 Provider│ │ 目录管理  │ │ profile / audit  │     │
│  └──────────┘ └───────────┘ └──────────┘ │ knowledge        │     │
│                                          └──────────────────┘     │
└──────────────────────────────────────────────────────────────────────┘
```

### 关键设计哲学

1. **零侵入可观测性**：Trace 通过 LangChain 回调机制采集，业务代码无感知
2. **层级化安全**：Shell 子系统从解析到执行有四层独立的安全检查
3. **组装式 Agent**：通过中间件链（MergedContext → ReWOO → Skill）分层注入能力
4. **隔离执行**：Sub-Agent 有独立 MemorySaver 和受限工具集，天然防递归

---

## 3. 完整请求链路

以下是一条用户消息从发送到回复的完整生命周期。

### 3.1 流式聊天链路（最完整的链路）

```
用户输入消息 → 点击发送
  │
  ▼
frontend/app.js: sendChatMessage()
  → window.api.chatStream(threadId, message)
  → Electron preload → HTTP POST /api/chat/stream
  │
  ▼
frontend/http_server.py: post_chat_stream()
  → 获取 thread_id 对应的 asyncio.Lock（防并发）
  → 调用 Api.chat_stream(thread_id, message)
  │
  ▼
frontend/bridge.py: Api.chat_stream()
  │
  ├─ 1. _cleanup_orphan_tool_calls(config)
  │    → 清理孤立的 tool_calls 状态（防止 LLM 400 错误）
  │
  ├─ 2. agent.astream_events(input, config, version="v2")
  │    → 进入 LangGraph CompiledStateGraph
  │    │
  │    ├─ [MergedContextMiddleware.awrap_model_call]
  │    │   → Layer 1: 读取 state.summary 作为对话历史摘要
  │    │   → Layer 2: 从 MemoryStore 加载用户画像 + 结构化记忆
  │    │   → Layer 3: SkillContextMiddleware 稍后注入
  │    │   → Layer 4: 冻结会话级当前时间
  │    │   → 合并为 SystemMessage 插入消息列表头部
  │    │   → Anthropic 模式：stable 部分加 cache_control
  │    │
  │    ├─ [ReWOORoutingMiddleware.awrap_model_call]
  │    │   → 检查是否启用且为首轮人类消息
  │    │   → 调用 should_use_rewoo() 判断是否复杂任务
  │    │   → 如果是 → ReWOOExecutor.execute()
  │    │     ├─ Phase 1: LLM 生成 JSON 计划
  │    │     ├─ Phase 2: asyncio.gather 并行执行工具
  │    │     └─ Phase 3: LLM 合成最终答案
  │    │   → 如果失败/非 ReWOO → 交给下一个中间件
  │    │
  │    ├─ [SkillContextMiddleware.awrap_model_call]
  │    │   → 扫描消息历史中的 load_skill 调用
  │    │   → 获取 SkillDefinition.prompt_injection
  │    │   → 追加 SystemMessage（已激活 Skill 的指令）
  │    │
  │    └─ [LLM 调用（ReAct Loop）]
  │        → LLM 生成响应，可能含 tool_calls
  │        → 工具执行（search / calculator / shell ...）
  │        → TraceCallbackHandler 采集 span
  │          ├─ on_chain_start → session_turn span
  │          ├─ on_llm_start   → llm_call span (含 token_usage)
  │          ├─ on_tool_start  → tool_call span (含 input/output)
  │          ├─ on_llm_end/on_tool_end → 结束 span → 写入 TraceStore
  │          └─ on_chain_end  → 结束 root span
  │
  ├─ 3. TracingStreamHandler 处理 astream_events
  │    → 构建 span 树（session_turn → llm_call / tool_call / delegate_task）
  │    → 通过 SSE 事件逐块发送给前端
  │      ├─ token: 流式文本块
  │      ├─ tool_start / tool_end / tool_error: 工具执行卡片
  │      ├─ interrupt: Shell 确认弹窗
  │      ├─ error: 错误信息
  │      └─ done: 完成信号
  │
  └─ 4. 释放 asyncio.Lock
  │
  ▼
MemoryManager.compress_if_needed(thread_id) [后台异步]
  │
  ├─ Phase 1: 增量摘要
  │   → _split_by_turns() 按 max_turns 切分新旧消息
  │   → _needs_compress() 检查是否超限
  │   → _summarize_with_llm_async() → LLM 合并旧摘要 + 新消息
  │   → RemoveMessage 替换压缩消息 + aupdate_state(summary)
  │
  ├─ Phase 2: 画像发现
  │   → _discover_profile_facts_async() → LLM 提取 JSON 事实
  │   → ProfileManager.merge() 归入 profile.json
  │
  ├─ Phase 3: 结构化记忆提取
  │   → MemoryExtractor.extract_from_messages_async()
  │     → _classify_gate() 启发式预过滤（跳过问候语）
  │     → LLM 提取 5 类事实（identity/environment/preferences/projects/facts）
  │     → MemoryStore.update_core_category() / merge_working_memory()
  │
  ├─ Phase 4: 知识归并（raw_facts > 30 时触发）
  │   → KnowledgeConsolidator.consolidate()
  │   → LLM 聚类 + 冲突检测 + 归并写入
  │
  └─ merged_middleware.invalidate_cache() → 下次请求刷新上下文
```

### 3.2 Sub-Agent 委派链路

```
Agent 在 ReAct 循环中决定调用 delegate_task("搜索 X 并总结")
  │
  ▼
delegate_task tool 被调用
  │
  ├─ 检查递归深度（contextvars，最大 3 层）
  ├─ 设置 TraceContext（tracer + parent_span_id）
  ├─ asyncio.wait_for(run_sub_agent(task), timeout=60)
  │
  ▼
run_sub_agent()
  ├─ create_sub_agent()
  │   → create_agent(model, tools=REGULAR_TOOLS, system_prompt=...)
  │   → 独立 MemorySaver() + uuid thread_id
  │
  ├─ 检测到 TraceContext → 创建 TraceCallbackHandler(store, tracer=shared_tracer)
  │   → 共享模式下不创建 root chain span，LLM/tool span 直接挂在 delegate_task 下
  │
  ├─ agent.ainvoke({"messages": [HumanMessage(task)]}, config)
  │   → Sub-agent 使用受限工具集执行
  │   → REGULAR_TOOLS 不含 delegate_task → 无法递归
  │
  └─ 返回 {"result": str, "agent_id": str}
  │
  ▼
主 Agent 继续 ReAct 循环，使用 sub-agent 的结果
```

---

## 4. 核心模块详解

### 4.1 配置与 LLM 工厂

#### `src/core/config.py` — 全局配置数据类

```python
@dataclass(frozen=True)
class Settings:
    llm_provider: str = "deepseek"      # deepseek | openai | anthropic
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    request_timeout: float = 10.0
    shell_timeout: int = 30
    max_turns: int = 10                 # 触发记忆压缩的轮数阈值
    max_message_chars: int = 12000      # 触发压缩的字符数阈值
    rewoo_enabled: bool = False         # ReWOO 模式开关
    ...
```

**设计要点**：
- `frozen=True` 不可变，防止运行时被意外修改
- `from_env()` 类方法从 `.env` + 环境变量加载，支持 `LLM_*` 和旧 `DEEPSEEK_*` 回退
- 所有模块都依赖此配置，是项目的单一配置源

#### `src/core/llm.py` — LLM 工厂

```python
def build_llm(settings=None, *, temperature=0.0) -> BaseChatModel:
    if provider == "anthropic":
        return ChatAnthropic(model="claude-sonnet-4-20250514", ...)
    elif provider == "openai":
        return CacheAwareChatOpenAI(model="gpt-4o", ...)
    else:  # deepseek
        return CacheAwareChatOpenAI(model="deepseek-chat", ...)
```

**设计要点**：
- 返回 `BaseChatModel` 接口，下游代码与具体 Provider 解耦
- `CacheAwareChatOpenAI` 子类保留 DeepSeek 流式模式下的 `prompt_cache_hit/miss_tokens` 字段，对可观测性至关重要
- 这是典型的**工厂方法模式**

---

### 4.2 Agent 构建与中间件链

#### `src/agent/builder.py` — Agent 工厂

`build_agent()` 是系统的核心组装方法：

```python
def build_agent(llm, *, checkpointer=None, profile=None,
                memory_store=None, session_id=None, skill_registry=None) -> AgentBundle:
```

**组装流程**：

```
1. 加载 Soul（角色设定）
2. 加载 Profile（用户画像）
3. 创建 MergedContextMiddleware（上下文注入）
4. 初始化 SkillRegistry + load_skill/list_skills 工具
5. 构建 delegate_task 工具（Sub-Agent 委派）
6. 组装工具列表（19+ 个）
7. 从各工具模块自动收集 _TOOL_GUIDE → 生成 system prompt
8. 创建 ReWOORoutingMiddleware
9. 创建 SkillContextMiddleware
10. 调用 LangGraph 的 create_agent() → CompiledStateGraph
```

**中间件执行顺序**：

```
MergedContextMiddleware → ReWOORoutingMiddleware → SkillContextMiddleware
│                        │                        │
├─ 注入上下文 SystemMessage  ├─ 可选拦截走 ReWOO    ├─ 注入 Skill 指令
├─ 最稳定的在最前            ├─ 读取已注入的上下文   └─ 追加 SystemMessage
└─ Anthropic 加 cache_control └─ 失败回退 ReAct
```

#### `AgentBundle`

```python
AgentBundle = namedtuple("AgentBundle", [
    "agent",                    # CompiledStateGraph
    "profile_middleware",       # MergedContextMiddleware
    "memory_middleware",        # MergedContextMiddleware（同一实例）
    "memory_store",             # MemoryStore 实例
    "skill_middleware",         # SkillContextMiddleware
    "skill_registry",           # SkillRegistry
    "tools",                   # 完整工具列表
])
```

---

### 4.3 上下文管理

上下文管理是 `MergedContextMiddleware` 的核心职责。按**稳定性分层注入**以最大化 LLM 的提示缓存命中率：

| 层级 | 内容 | 稳定性 | 更新频率 |
|------|------|--------|---------|
| Layer 1 | 对话历史摘要（summary） | 最稳定 | 仅压缩时 |
| Layer 2 | 用户画像 + 结构化记忆 | 半稳定 | 偶尔 |
| Layer 3 | Skill 上下文 | 半稳定 | load_skill 时 |
| Layer 4 | 当前时间 | 易变 | 每次会话 |

```python
async def awrap_model_call(self, request, handler):
    context_parts = []

    # Layer 1: 对话历史摘要
    summary = request.state.get("summary", "")
    if summary:
        context_parts.append(f"[对话历史摘要]\n{summary}")

    # Layer 2: 用户画像 + 结构化记忆
    memory_text = self._build_memory_text()
    if memory_text:
        context_parts.append(f"[关于用户]\n{memory_text}")

    # Layer 4: 当前时间
    if self._session_time is None:
        self._session_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    context_parts.append(f"[当前时间]\n{self._session_time}")

    # Anthropic 模型：stable 内容加 cache_control 头
    if self._is_anthropic(request):
        content_blocks = [
            {"type": "text", "text": stable_text,
             "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": dynamic_text},
        ]
        context_msg = SystemMessage(content=content_blocks)
    else:
        context_msg = SystemMessage(content="\n\n".join(context_parts))

    # 将 SystemMessage 插入消息列表头部（最前面）
    request = request.override(messages=[context_msg, *request.messages])
    return await handler(request)
```

**关键设计**：
- **缓存失效机制**：`invalidate_cache()` 增加 `_cache_version`，`_build_memory_text()` 在版本变化时重建
- **Anthropic 专用优化**：通过 `cache_control` 块实现 prompt caching，大幅降低延迟和费用
- **延迟绑定**：`_session_time` 在第一次调用时冻结，同一会话内时间不变

---

### 4.4 工具系统

工具系统位于 `src/tools/`，全部使用 LangChain 的 `@tool` 装饰器声明。

#### 工具列表（19+ 个）

| 工具 | 分类 | 说明 |
|------|------|------|
| `calculator` | computation | AST 白名单安全数学求值 |
| `current_time` | computation | 本地/UTC 时间 |
| `weather` | web | wttr.in 天气查询 |
| `search` | web | 多引擎搜索（Tavily/DDGS/Bing fallback）|
| `web_fetch` | web | HTML 正文提取 |
| `list_files` | file_system | 目录列表 |
| `read_file` | file_system | 文件读取（行范围、编码检测）|
| `grep_search` | file_system | 文件内容搜索 |
| `create_folder` | file_system | 创建目录 |
| `write_file` | file_system | 文件写入（temp 模式 / 中断确认）|
| `run_python` | code_execution | 隔离子进程 Python 执行 |
| `shell` | code_execution | 四层安全闸门 Shell 执行 |
| `delegate_task` | agent | Sub-Agent 委派 |
| `install_skill` | system | 技能安装（npm/pip/git/url）|
| `uninstall_skill` | system | 技能卸载 |
| `list_registry` | system | 技能市场浏览 |
| `list_skills` | system | 可用技能列表 |
| `load_skill` | system | 激活技能 |

#### 搜索系统（`src/tools/search/`）— 分层架构

```
search(query)
  │
  ├─ topic="news" → fetch_hot_search() 百度热搜
  │
  └─ 正常搜索
      ├─ QueryClassifier → SIMPLE / COMPLEX
      │
      ├─ SIMPLE:
      │   → SearchCache → TavilyAdapter / DuckDuckGoAdapter / BingAdapter
      │   → 自动 fallback（Tavily 失败 → DDGS → Bing → 报错）
      │
      └─ COMPLEX:
          → ProgressiveSearchEngine
            → 迭代 1: 搜索 → LLM 评估 → 不够 → 提炼查询
            → 迭代 2: 搜索 → LLM 评估 → 不够 → 再提炼
            → 迭代 3: 搜索 → LLM 综合（兜底）
```

**适配器模式**：
```python
class SearchAdapter(ABC):
    @abstractmethod
    async def search(self, query: str, max_results: int = 5) -> SearchResponse: ...

class TavilyAdapter(SearchAdapter): ...
class DuckDuckGoAdapter(SearchAdapter): ...
class BingAdapter(SearchAdapter): ...
```

#### AST 工具发现

```python
_TOOL_GUIDE_MODULES: dict[str, str] = {
    "calculator": "src.tools.calculator",
    "current_time": "src.tools.current_time",
    ...
}

def _build_tool_guide(tools):
    """通过模块映射自动收集 _TOOL_GUIDE 字符串 → 拼入 system prompt"""
```

每个工具模块通过 `_TOOL_GUIDE` 字符串自声明使用指南，`_build_tool_guide()` 自动聚合到 system prompt 的 `## 工具使用指南` 部分。新增工具只需定义该字符串并更新映射。

---

### 4.5 Shell 子系统（四层语义安全门）

`src/tools/shell/` 是项目中**安全设计最完备**的子系统。

#### 四层架构

```
命令输入
  │
  ├─ Layer 1: 命令解析（patterns.py）
  │   shlex.split(command)
  │   解析失败 → HIGH_RISK "命令格式无法解析"
  │
  ├─ Layer 2: 高危短路（patterns.py）
  │   固定拒绝模式：
  │   - 密钥访问: /.ssh/, .git/config
  │   - RCE: curl|bash, wget|sh
  │   - 破坏: dd 写 /dev/, git push --force
  │   - 系统: shutdown -h, reboot, mkfs, fork 炸弹
  │   匹配 → HIGH_RISK（直接拒绝 + 给出替代方案）
  │
  ├─ Layer 3: 白名单安全管理（patterns.py）
  │   _SAFE_COMMANDS 白名单
  │   git/kubectl/docker 子命令白名单
  │   npm/pip 全局安装检测
  │   匹配安全 → SAFE（自动执行）
  │   未知命令族 → CONFIRM（降级到 Layer 4）
  │
  └─ Layer 4: 用户确认层（tool.py）
      rm/mv/cp/del/kill/chmod...
      → interrupt() 触发前端弹窗，60 秒倒计时
      → 用户批准才执行
      → 拒绝或超时 → 不执行
```

#### 执行器（`executor.py`）

```python
class SandboxExecutor:
    _semaphore = asyncio.Semaphore(4)  # 最多 4 个并发

    async def execute(self, command, *, timeout=None) -> ShellResult:
        # 自适应超时（ls: 3s, git: 15s, pip: 60s）
        # Windows: cmd.exe /c; Unix: /bin/bash -c
        # 输出截断: <50K 内联截断（头 3K + 尾 1.5K）
        #            >50K 写入文件外置
```

**设计要点**：
- `CommandClassifier.classify()` 返回 `(DangerLevel, reason, alternatives)`
- `AuditLogger` 记录所有执行到 `audit.db`，自动保留 10000 条
- Layer 2 的高危模式**硬编码**，不可通过配置绕过
- 命令的**安全分类与执行分离**：`patterns.py` 只做分类，`executor.py` 只做执行

---

### 4.6 记忆系统

记忆系统是项目中**最复杂**的子系统，包含四阶段流水线。

#### 整体架构

```
MemoryManager.compress_if_needed(thread_id)
  │
  ├─ Phase 1: 增量摘要
  │   目标：控制 messages 长度不超限
  │   触发：human turns > max_turns(10) 或 chars > max_message_chars(12000)
  │   方法：LLM 合并 prev_summary + 旧消息 → 新摘要（≤500 字）
  │   工具：RemoveMessage + aupdate_state
  │
  ├─ Phase 2: 画像发现
  │   目标：从对话中提取用户个人信息
  │   方法：LLM 提取 JSON → ProfileManager.merge()
  │   示例：{"name": "张三", "occupation": "工程师"}
  │
  ├─ Phase 3: 结构化记忆提取
  │   目标：提取 5 类事实归入 MemoryStore
  │   方法：MemoryExtractor
  │     → _classify_gate() 启发式跳过问候语
  │     → LLM 提取 5 类（identity/environment/preferences/projects/facts）
  │     → 按置信度归并（explicit > discovered > inferred）
  │
  └─ Phase 4: 知识归并
      触发：raw_facts > 30
      方法：KnowledgeConsolidator
        → LLM 聚类 + 主题总结
        → 冲突检测（重叠率 > 40% 标记 superseded）
        → 上限 100 条活跃
```

#### 三层记忆存储

| 层级 | 存储 | 内容 | 生命周期 |
|------|------|------|---------|
| Core Memory | `memory.json` | 5 类核心事实 | 持久化 |
| Working Memory | SQLite `working_memories` | 原始事实 + 置信度 | 可删除 |
| Consolidated Knowledge | SQLite `consolidated_knowledge` | 归并后的知识 | 长期保留 |

**置信度优先级**：
```python
CONFIDENCE_ORDER = {"explicit": 3, "discovered": 2, "inferred": 1}
```
用户显式设定的 > 自动发现的 > 推断的。自动发现不会覆盖用户设定。

#### 记忆压缩隔离

```python
async def compress_if_needed(self, thread_id, force=False):
    if self._lock.locked():
        return False       # 已有压缩在进行中
    async with self._lock:
        ...
        # 1. 增量摘要
        # 2. 画像发现
        # 3. 记忆提取
        # 4. 知识归并
        # 5. invalidate_cache()
```
`asyncio.Lock` 防止每个会话的并发压缩，避免 LangGraph 状态竞争。

---

### 4.7 Skill 系统

Skill 是**可安装的提示注入包**。它们不定义自定义工具（至少当前版本如此），而是注入指令指导 LLM 如何使用现有工具。

#### Skill 定义格式

```markdown
# skill.md
---
name: weather-helper
description: 天气查询助手，提供更友好的天气解读
---

当用户询问天气时，请使用 weather 工具查询后，
补充以下信息：
- 温差提示
- 穿衣建议
- 紫外线强度提醒
```

#### 生命周期

```
安装: install_skill("git:https://...") 或 install_skill("npm:@scope/package")
  → 下载到 ~/.miaogent/skills/<name>/
  → 验证 skill.md 或 plugin.json 存在
  → 更新 .miaogent-index.json

发现: SkillRegistry.discover()
  → 扫描 src/skills/（内置）+ ~/.miaogent/skills/（第三方）
  → 解析 YAML frontmatter → {name: SkillDefinition}

激活: LLM 调用 load_skill("weather-helper")
  → 工具返回 "已激活 Skill 'weather-helper'——天气查询助手"
  → 消息历史中出现 load_skill 调用记录

注入: SkillContextMiddleware
  → 每次 LLM 调用前扫描消息历史
  → 找到 load_skill → SkillRegistry.get(name) → prompt_injection
  → 追加 SystemMessage("已激活的技能：\n\nweather-helper\n当用户询问天气时...")
```

**关键设计**：
- **无状态注入**：Skill 的激活状态完全来自消息历史，而非外部存储。消息被压缩后，Skill 自动失活
- **Skill 不引入新工具**：只注入提示文本，指导 LLM 更好地组合使用现有工具
- **四种安装源**：npm（纯 HTTP registry）、pip（纯 HTTP PyPI）、git clone、URL 下载

---

### 4.8 ReWOO 规划-执行模式

ReWOO（Reason Without Observation）将标准 ReAct 迭代循环替换为**规划 → 并行执行 → 合成**三阶段。

#### vs 标准 ReAct

```
ReAct:  LLM → Tool → LLM → Tool → LLM → Tool → LLM  (N 轮对话)
ReWOO:  LLM(规划) → 并行 Tool × N → LLM(合成)       (2 次 LLM 调用)
```

#### 执行流程

```python
class ReWOOExecutor:
    async def execute(self, user_query, context=""):
        # Phase 1: 生成计划
        plan = await self._generate_plan(user_query, context)
        # → LLM 输出 JSON: {"steps": [{"tool": "search", "args": {...}, ...}]}

        # Phase 2: 并行执行（信号量限制 max_parallel=6）
        results = await self._execute_plan(plan)
        # → asyncio.gather 所有工具调用

        # Phase 3: 合成答案
        answer = await self._synthesize(user_query, results, context)
        return answer
```

#### 意图判定（`rewoo_intent.py`）

`should_use_rewoo(query)` 使用启发式规则判断：

- **多步骤模式**：`首先...然后...最后...`、`first...then...` → 工具数 ≥ 3
- **工具数阈值**：估算工具数 ≥ 5
- **简短查询**（<20 字符）→ 不触发
- **多个分隔符 + 工具数 ≥ 4** → 触发

#### 中间件集成

`ReWOORoutingMiddleware` 位于 `MergedContextMiddleware` 之后：

```python
class ReWOORoutingMiddleware(AgentMiddleware):
    async def awrap_model_call(self, request, handler):
        if not self._enabled:
            return await handler(request)  # 未启用 → 正常 ReAct

        if len(human_msgs) != 1:
            return await handler(request)  # 仅首轮

        if not should_use_rewoo(query):
            return await handler(request)  # 不满足触发条件

        # 从 MergedContextMiddleware 注入的 SystemMessage 读取完整上下文
        full_context = _read_injected_context(request)
        result = await self._executor.execute(query, context=full_context)
        if result:
            return AIMessage(content=result)
        # 空结果 → 回退 ReAct
        return await handler(request)
```

**设计要点**：
- **优雅降级**：ReWOO 失败/空结果时安静回退到标准 ReAct
- **读取已注入的上下文**：ReWOO 不重新组装上下文，而是从 MergedContextMiddleware 已注入的消息中读取
- **信号量限并**：`Semaphore(6)` 防止突发工具调用压垮系统

---

### 4.9 Sub-Agent 委派与隔离

Sub-Agent 是 MiaoGent 实现多 Agent 编排的核心机制。

#### 防递归设计的三个层面

1. **工具集过滤**：`REGULAR_TOOLS` 明确不包含 `delegate_task`
2. **深度限制**：`contextvars` 追踪递归深度，最大 3 层
3. **独立 MemorySaver**：每次 `run_sub_agent()` 创建新的 `MemorySaver()` + `uuid thread_id`

```python
REGULAR_TOOLS = [
    calculator, current_time, weather, search, web_fetch,
    list_files, read_file, grep_search, create_folder,
    write_file, run_python, shell,
]  # 没有 delegate_task！
```

#### Sub-Agent 创建

```python
def create_sub_agent(llm, *, tools=None, prompt=None):
    return create_agent(
        model=llm,
        tools=tools or REGULAR_TOOLS,
        system_prompt=prompt or SUB_AGENT_PROMPT,
        checkpointer=MemorySaver(),    # 全新内存级 checkpointer
        name="sub-agent",
    )
```

#### Trace 上下文传播

```python
async def run_sub_agent(llm, task, ...):
    # 检查是否有正在进行的 trace
    tracer, parent_span_id = get_trace_context()

    agent = create_sub_agent(llm, tools=tools, prompt=prompt)
    config = {"configurable": {"thread_id": uuid.uuid4().hex}}

    if tracer is not None:
        # 共享模式：Sub-Agent 的 span 挂在父 tracer 下
        handler = TraceCallbackHandler(store=TraceStore(), tracer=tracer)
        config["callbacks"] = [handler]

    result = await agent.ainvoke(
        {"messages": [HumanMessage(content=task)]}, config
    )
```

**共享 Tracer 模式**：Sub-Agent 复用主 Agent 的 Tracer，其 LLM/Tool span 直接嵌套在 `delegate_task` span 下，在前端瀑布图中呈现为父子关系。

---

### 4.10 链路追踪系统

链路追踪是**零侵入的可观测性系统**，基于 LangChain 的 `BaseCallbackHandler`。

#### 核心架构

```
TraceCallbackHandler (LangChain 回调)
  │
  ├─ Tracer (栈式 span 管理)
  │   ├─ start_span() → 创建 SpanData + 压栈
  │   ├─ end_span() → 结束 + 出栈
  │   └─ 自动继承 trace_id + parent_span_id
  │
  ├─ SpanData (数据模型)
  │   ├─ 5 种 span_type: session_turn / llm_call / tool_call / delegate_task / agent_step
  │   ├─ token_usage: input / output / cache_hit / cache_miss
  │   └─ end() 幂等：已结束的 span 不会再次写入时间
  │
  └─ TraceStore (SQLite 持久化)
      ├─ write_span() / write_spans()
      ├─ get_trace_list() — 分页 + 搜索 + 状态过滤
      ├─ get_stats() — 今日聚合 + 昨日对比
      ├─ get_daily_stats() — 14 日趋势
      └─ get_token_top_traces() — Top N token 消耗
```

#### 四种 Span 类型

| Span Type | 来源回调 | 说明 |
|-----------|---------|------|
| `session_turn` | `on_chain_start` | 一次用户请求的根 span |
| `llm_call` | `on_llm_start/end` | LLM 调用，含 token 统计 |
| `tool_call` | `on_tool_start/end` | 工具执行，含输入输出 |
| `delegate_task` | 手动创建 | Sub-Agent 委派 span |

#### 共享模式 vs 独立模式

```python
class TraceCallbackHandler(BaseCallbackHandler):
    def __init__(self, store, session_id="", session_turn=0, tracer=None):
        self._is_shared = tracer is not None
        # 独立模式：创建自己的 Tracer（用于主 Agent）
        # 共享模式：复用外部 Tracer（用于 Sub-Agent）

    def on_chain_start(self, ...):
        if self._is_shared:
            return  # 不创建 chain span，直接挂父 tracer 栈顶下
```

#### 上下文传播（`tracing/context.py`）

```python
_current_tracer: ContextVar["Tracer | None"]
_current_parent_span_id: ContextVar[str]

def set_trace_context(tracer, parent_span_id): ...
def get_trace_context() -> tuple[Tracer | None, str]: ...
```

使用 Python 的 `contextvars` 跨 async 边界传播 tracer，确保 Sub-Agent 的 span 正确嵌套。

---

### 4.11 持久化与存储

#### 存储布局（`~/.miaogent/`）

| 文件 | 格式 | 用途 |
|------|------|------|
| `history.db` | SQLite | LangGraph 检查点持久化 |
| `traces.db` | SQLite | Trace span 数据 |
| `memory.db` | SQLite | Working Memory + Consolidated Knowledge |
| `memory.json` | JSON | Core Memory（5 类核心事实）|
| `.sessions.json` | JSON | 会话注册表 |
| `soul.json` | JSON | AI 角色设定 |
| `profile.json` | JSON | 用户画像 |
| `audit.db` | SQLite | Shell 命令审计日志 |
| `skills/` | 目录 | 第三方 Skill |

#### 会话管理（`SessionRegistry`）

```python
class SessionRegistry:
    def add(self, thread_id)        # 幂等添加
    def update(self, thread_id, turn_count=..., last_message=...)  # 更新活跃信息
    def remove(self, thread_id)     # 单个删除
    def remove_many(self, ids)      # 批量删除
```

#### 原子写入模式

```python
# 所有 JSON 文件使用原子写入：写 tmp → os.replace
with open(tmp_path, "w") as f:
    json.dump(data, f)
os.replace(tmp_path, target_path)
```

#### 幂等数据库迁移

```python
def _migrate_schema(self, conn):
    existing = {row[1] for row in conn.execute("PRAGMA table_info(spans)")}
    for sql in migrations:
        col_name = sql.split("ADD COLUMN ")[1].split(" ")[0]
        if col_name not in existing:
            conn.execute(sql)
```

---

## 5. LangChain/LangGraph 框架使用详解

> 本章节详细分析 MiaoGent 项目中 LangChain/LangGraph 的具体使用方式，以及可以举一反三的设计模式。

### 5.1 整体定位：LangChain/LangGraph 在本项目中扮演的角色

| 框架组件 | 在本项目中的角色 |
|----------|----------------|
| `langchain.agents.create_agent` | **核心入口**：构建 Agent 的 CompiledStateGraph |
| `langchain.agents.middleware.AgentMiddleware` | **上下文注入**：5 个自定义中间件 |
| `langchain_core.tools @tool` | **工具定义**：19+ 工具的声明方式 |
| `langchain_core.callbacks.BaseCallbackHandler` | **可观测性**：TraceCallbackHandler |
| `langchain_core.messages.*` | **消息模型**：SystemMessage, HumanMessage, AIMessage, RemoveMessage |
| `langgraph.checkpoint.memory.MemorySaver` | **状态持久化**：会话隔离 |
| `langgraph.graph.message.add_messages` | **消息归并**：Reducer |
| `langgraph.checkpoint.sqlite.AsyncSqliteSaver` | **持久化检查点**：写入 history.db |
| `langchain_core.language_models.BaseChatModel` | **LLM 抽象**：多 Provider 统一接口 |
| `astream_events` | **流式传输**：SSE 流式聊天 |

### 5.2 create_agent：Agent 构建入口

`create_agent()` 是 `langchain 1.x` 推荐的 Agent 构建方式。它返回一个 `CompiledStateGraph`（LangGraph 编译后的可执行图）。

#### 项目中的使用

```python
from langchain.agents import create_agent

agent = create_agent(
    model=llm,                          # BaseChatModel 实例
    tools=tools,                        # 工具列表
    system_prompt=system_prompt,        # 系统提示词
    state_schema=AgentState,            # 自定义状态 schema
    middleware=middleware,               # 中间件链
    name="single-agent",                 # 图名称
    checkpointer=checkpointer,          # 持久化检查点
)
```

#### 自定义 State Schema

```python
class AgentState(TypedDict):
    messages: Required[Annotated[list, add_messages]]
    summary: NotRequired[str]  # 自定义字段：历史摘要
```

**关键点**：
- `messages` 使用 `add_messages` reducer——LangGraph 自动将新消息追加到列表尾部，不覆盖已有消息
- `summary` 作为独立字段，**不与 system prompt 混在一起**。这是项目的设计决策：摘要单独管理，LLM 能感知但和系统提示词是两条独立内容
- 这种通过 `NotRequired` 扩展状态的方式，可以在不修改 LangGraph 内部代码的情况下增加自定义字段

#### 举一反三

```python
# 你也可以为状态添加更多自定义字段
class MyAgentState(TypedDict):
    messages: Required[Annotated[list, add_messages]]
    summary: NotRequired[str]
    user_mood: NotRequired[str]          # 用户情绪
    pending_actions: NotRequired[list]   # 待执行操作
    last_error: NotRequired[str]         # 上次错误
```

### 5.3 AgentMiddleware 中间件链

`AgentMiddleware` 是 LangChain 1.x 提供的 LLM 调用拦截机制。每个中间件可以读/写/拦截 LLM 请求。

#### 接口

```python
class AgentMiddleware:
    async def awrap_model_call(self, request, handler):
        # request: 包含 model, messages, state 等
        # handler: 下一个中间件或实际的 LLM 调用
        return await handler(request)
```

#### 项目中的三个中间件

**① MergedContextMiddleware** — 上下文注入

```python
class MergedContextMiddleware(AgentMiddleware):
    async def awrap_model_call(self, request, handler):
        # 前置处理：构建 SystemMessage 注入上下文
        context_msg = SystemMessage(content=...)
        request = request.override(messages=[context_msg, *request.messages])
        # 调用链中下一个中间件/LLM
        return await handler(request)
```

**② ReWOORoutingMiddleware** — ReWOO 路由

```python
class ReWOORoutingMiddleware(AgentMiddleware):
    async def awrap_model_call(self, request, handler):
        if self._enabled and should_use_rewoo(query):
            result = await self._executor.execute(query, context)
            if result:
                return AIMessage(content=result)  # 完全拦截 LLM 调用
        # 不满足条件 → 交给下一层
        return await handler(request)
```

**③ SkillContextMiddleware** — Skill 注入

```python
class SkillContextMiddleware(AgentMiddleware):
    async def awrap_model_call(self, request, handler):
        # 扫描消息历史中的 load_skill 调用
        skill_msg = self._build_skill_context(request.messages)
        if skill_msg:
            request = request.override(messages=[*request.messages, skill_msg])
        return await handler(request)
```

#### 中间件链执行顺序

```
用户消息 → MergedContext → ReWOO → Skill → LLM
              │               │       │       │
              │ 注入上下文     │ 可选拦截  │ 注入Skill
              ▼               ▼       ▼       ▼
```

**设计要点**：
1. 顺序重要：MergedContext 先注入上下文 → ReWOO 读取它 → Skill 再追加
2. 中间件可以完全拦截（ReWOO 返回 AIMessage），也可以增强请求（MergedContext 注入 SystemMessage）
3. 中间件链类似 Web 框架的 middleware（如 FastAPI/Express），但作用于 LLM 调用

#### 举一反三

```python
# 自定义中间件：敏感信息过滤
class SensitiveDataFilter(AgentMiddleware):
    async def awrap_model_call(self, request, handler):
        # 在发往 LLM 前过滤敏感信息
        filtered = self._filter_sensitive(request.messages)
        request = request.override(messages=filtered)
        return await handler(request)

# 自定义中间件：请求日志
class LoggingMiddleware(AgentMiddleware):
    async def awrap_model_call(self, request, handler):
        logger.info(f"LLM call: {len(request.messages)} messages")
        start = time.time()
        response = await handler(request)
        logger.info(f"LLM response: {time.time()-start:.2f}s")
        return response
```

### 5.4 BaseCallbackHandler 回调系统

`BaseCallbackHandler` 是 LangChain 的观察者模式实现，用于零侵入采集运行时事件。

#### 项目中的 TraceCallbackHandler

```python
from langchain_core.callbacks import BaseCallbackHandler

class TraceCallbackHandler(BaseCallbackHandler):
    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, **kwargs):
        # 创建 session_turn span
        span_id = tracer.start_span("session_turn", ...)
        self._run_id_to_span_id[run_id] = span_id

    def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):
        # 创建 llm_call span（含 model name）
        span_id = tracer.start_span("llm_call", model=name)

    def on_llm_end(self, response, *, run_id, **kwargs):
        # 提取 token_usage（input/output/cache_hit/cache_miss）
        tokens = self._extract_tokens(llm_output)
        span.input_tokens = tokens["input"]
        tracer.end_span(span_id)
        self.store.write_span(span)

    def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
        span_id = tracer.start_span("tool_call", tool_name=name, tool_input=input_str)

    def on_tool_end(self, output, *, run_id, **kwargs):
        tracer.end_span(span_id)
        self.store.write_span(span)
```

#### run_id → span_id 映射

```python
self._run_id_to_span_id: dict[uuid.UUID, str] = {}
```
每个 LangChain 的 run_id 映射到项目的 span_id，通过 `on_*_start` 建立映射，`on_*_end` 消费映射并弹出。

#### 两种模式

| 模式 | 场景 | 行为 |
|------|------|------|
| 独立模式 (`tracer=None`) | 主 Agent | 创建自己的 Tracer + root chain span |
| 共享模式 (`tracer=...`) | Sub-Agent | 不创建 chain span，嵌套到父 tracer 栈 |

#### 举一反三

```python
# 扩展 TraceCallbackHandler：采集更多维度的数据
class ExtendedTraceHandler(BaseCallbackHandler):
    def on_retriever_start(self, serialized, query, *, run_id, **kwargs):
        # 如果使用 RAG，可以采集检索事件
        ...

    def on_llm_start(self, ...):
        # 记录完整 input（注意长度控制）
        span.llm_input = str(prompts)[:1000]

    def on_tool_start(self, ...):
        # hooks 触发前执行（如环境检查）
        ...

# 其他有用的 BaseCallbackHandler 用法：
# - Token 计数监控
# - 延迟告警（当某次 LLM 调用超过 30s 时报警）
# - 调试：记录所有 LLM 的输入输出
```

### 5.5 BaseChatModel 多态 LLM 接口

`BaseChatModel` 是 LangChain 的 LLM 抽象基类，定义了 `invoke()`, `ainvoke()`, `stream()`, `astream()` 等核心方法。

#### 项目中的工厂

```python
def build_llm(settings, *, temperature=0.0) -> BaseChatModel:
    if settings.llm_provider == "anthropic":
        return ChatAnthropic(model="claude-sonnet-4-20250514", ...)
    elif settings.llm_provider == "openai":
        return CacheAwareChatOpenAI(model="gpt-4o", ...)
    else:  # deepseek
        return CacheAwareChatOpenAI(model="deepseek-chat", ...)
```

**下游代码完全不需要知道具体 Provider**：
- Agent 构建时：`create_agent(model=llm, ...)` — llm 可以是任意 BaseChatModel
- 记忆压缩时：`self.compression_llm.ainvoke(prompt)` — 统一接口
- ReWOO 规划时：`self._llm.ainvoke(messages)` — 统一接口

#### CacheAwareChatOpenAI

```python
class CacheAwareChatOpenAI(ChatOpenAI):
    """保留 DeepSeek 流式模式下的缓存 token 计数字段。"""
    def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):
        # 标准处理 + 从 chunk 提取 prompt_cache_hit/miss_tokens
        ...
```

#### 举一反三

```python
# 你可以为任何模型创建自定义子类以增强能力
class CustomChatModel(ChatOpenAI):
    @property
    def _llm_type(self) -> str:
        return "custom-model"

    def _generate(self, messages, stop, run_manager, **kwargs):
        # 自定义生成逻辑
        ...

# LLM 工厂模式的应用场景：
# - 测试时可以用 FakeListChatModel（返回预设响应）
# - 回退模型：当主模型失败时使用备选
# - 负载均衡：在多个 API 端点间分发请求
```

### 5.6 @tool 装饰器

`@tool` 是定义 LangChain 工具的最简洁方式。

#### 项目中的使用模式

**同步工具**：
```python
from langchain_core.tools import tool

@tool(description="执行数学计算。支持 +-*/、math 模块函数。")
def calculator(expression: str) -> str:
    """Execute math calculation."""
    ...

@tool(description="查看当前本地时间和 UTC 时间。")
def current_time() -> str:
    ...
```

**异步工具**：
```python
@tool(description="联网搜索。多引擎自动 fallback。")
async def search(query: str, topic: str = "text") -> str:
    ...
```

**动态构建的工具**：
```python
def build_delegate_task(llm, *, session_id=None, skill_registry=None):
    @tool(description="将子任务委托给隔离的 sub-agent 执行...")
    async def delegate_task(task: str, timeout: int = 60) -> str:
        ...
    return delegate_task
```

#### @tool 自动推导的元数据

- `name`：默认函数名，可覆盖 `@tool("custom_name")`
- `description`：从 docstring 或参数获取
- `args`：从函数签名自动推导（类型标注 + 默认值）
- LLM 通过这些元数据决定何时调用哪个工具

#### 工具使用指南自收集

```python
_TOOL_GUIDE_MODULES: dict[str, str] = {
    "calculator": "src.tools.calculator",
    "current_time": "src.tools.current_time",
    ...
}

def _build_tool_guide(tools):
    """自动收集各工具的 _TOOL_GUIDE → 拼入 system prompt"""
```

每个工具模块中的 `_TOOL_GUIDE` 字符串自声明最佳实践，系统自动聚合到 prompt 的 `## 工具使用指南` 部分。

#### 举一反三

```python
# 带缓存的工具
@tool(description="查询用户信息")
def get_user(user_id: str) -> str:
    """带内存缓存，避免重复查询。"""
    if user_id in _cache:
        return _cache[user_id]
    result = db.query(...)
    _cache[user_id] = result
    return result

# 带限流的工具
import asyncio
_semaphore = asyncio.Semaphore(5)

@tool(description="外部 API 调用")
async def call_external_api(endpoint: str) -> str:
    async with _semaphore:
        ...

# 工具链组合：一个工具的输出是另一个工具的输入
# LangChain 的 ToolNode 会自动处理这种依赖
```

### 5.7 LangGraph 状态管理与持久化

LangGraph 通过**状态归并（Reducers）**和**检查点（Checkpointers）**管理 Agent 状态。

#### add_messages Reducer

```python
from langgraph.graph.message import add_messages

class AgentState(TypedDict):
    messages: Required[Annotated[list, add_messages]]
    summary: NotRequired[str]
```

`add_messages` 是一个归并函数（Reducer），作用：
- 新消息默认**追加**到列表尾部
- 如果消息 ID 已存在，则**替换**
- `RemoveMessage` 特殊处理：按 ID 删除

**项目中的使用**：
```python
# 压缩时删除旧消息
removes = [RemoveMessage(id=m.id) for m in to_remove]
await self.agent.aupdate_state(config, values={"messages": removes + to_keep})
```

#### MemorySaver — 内存级检查点

每个 Sub-Agent 使用独立的 `MemorySaver()`：
```python
agent = create_agent(
    model=llm,
    tools=REGULAR_TOOLS,
    checkpointer=MemorySaver(),  # 新的检查点 → 完全隔离
    name="sub-agent",
)
```

#### AsyncSqliteSaver — SQLite 持久化

主 Agent 使用 `AsyncSqliteSaver` 持久化到 `history.db`：
```python
conn = await aiosqlite.connect(settings.db_path)
checkpointer = AsyncSqliteSaver(conn)
```

#### aget_state / aupdate_state

```python
# 读取当前状态
state = await self.agent.aget_state(config)
messages = state.values.get("messages", [])
summary = state.values.get("summary", "")

# 更新状态（压缩时）
await self.agent.aupdate_state(config, values={
    "messages": removes + to_keep,
    "summary": new_summary,
})
```

#### 并发控制

```python
_chat_locks: dict[str, asyncio.Lock] = {}

async def chat_stream(self, thread_id, message):
    if thread_id not in _chat_locks:
        _chat_locks[thread_id] = asyncio.Lock()
    async with _chat_locks[thread_id]:
        # 同一会话同一时间只能有一个聊天请求
        ...
```

#### 举一反三

```python
# 自定义 Reducer
def custom_reducer(old, new):
    """合并新旧值，保留历史峰值。"""
    if old is None:
        return new
    return max(old, new)

class MyState(TypedDict):
    messages: Annotated[list, add_messages]
    peak_tokens: Annotated[int, custom_reducer]
    errors: Annotated[list, add_messages]

# 多种 Checkpointer 选择：
# - MemorySaver: 内存级，隔离性好，重启丢失
# - AsyncSqliteSaver: SQLite 持久化
# - PostgresSaver / MongoDBSaver: 生产级
```

### 5.8 astream_events 流式事件

`astream_events` 提供细粒度的事件流，用于在前端实时展示 Agent 执行过程。

#### 项目中的使用

```python
# bridge.py
async for event in agent.astream_events(input, config, version="v2"):
    kind = event["event"]
    if kind == "on_chat_model_stream":
        # 流式 token → 前端 SSE
        token = event["data"]["chunk"].content
        yield {"type": "token", "content": token}
    elif kind == "on_tool_start":
        # 工具开始执行
        yield {"type": "tool_start", "name": event["name"]}
    elif kind == "on_tool_end":
        # 工具执行完成
        yield {"type": "tool_end", "name": event["name"], "output": output}
```

#### TracingStreamHandler

```python
class TracingStreamHandler:
    def handle_event(self, event):
        kind = event["event"]
        if kind == "on_tool_start":
            # 创建 tool_call span
            span_id = tracer.start_span("tool_call", ...)
        elif kind == "on_tool_end":
            # 结束 span
            tracer.end_span(span_id)
```

#### 举一反三

```python
# 流式事件类型（v2）：
# - on_chat_model_start / on_chat_model_stream / on_chat_model_end
# - on_chain_start / on_chain_stream / on_chain_end
# - on_tool_start / on_tool_end
# - on_retriever_start / on_retriever_end
# - on_llm_start / on_llm_new_token / on_llm_end

# 典型用法：前端实时进度显示
async for event in agent.astream_events(input, config, version="v2"):
    if event["event"] == "on_tool_start":
        show_spinner(event["name"])
    elif event["event"] == "on_tool_end":
        hide_spinner(event["name"])
        show_result(event["name"], event["data"]["output"])
    elif event["event"] == "on_chat_model_stream":
        append_token(event["data"]["chunk"].content)
```

### 5.9 Message 类型体系

LangChain 定义了完整的消息类型体系，项目中使用了几种关键类型。

| 消息类型 | 用途 | 项目中使用场景 |
|----------|------|---------------|
| `SystemMessage` | 系统指令 | 上下文注入、Skill 注入、ReWOO 规划 prompt |
| `HumanMessage` | 用户输入 | chat 请求、Sub-Agent 任务、压缩 prompt |
| `AIMessage` | 模型回复 | 正常回复、ReWOO 合成结果 |
| `RemoveMessage` | 消息删除 | 记忆压缩时删除旧消息 |

#### 使用示例

```python
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langgraph.graph.message import RemoveMessage

# 注入上下文
context_msg = SystemMessage(content="[对话历史摘要]\n...")

# Sub-Agent 任务
task_msg = HumanMessage(content="请搜索今天的新闻")

# ReWOO 拦截返回
return AIMessage(content="合成后的答案")

# 压缩时删除消息
removes = [RemoveMessage(id=m.id) for m in to_remove]
await agent.aupdate_state(config, values={"messages": removes + to_keep})
```

#### 消息内容的多态性

```python
# content 可以是 str 或 list（多模态）
msg = SystemMessage(content=[
    {"type": "text", "text": "指令文本",
     "cache_control": {"type": "ephemeral"}},
    {"type": "text", "text": "动态内容"},
])

# 项目中多处需要兼容两种格式
def _content_str(msg):
    c = msg.content
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(b.get("text", "") for b in c if isinstance(b, dict))
    return str(c)
```

### 5.10 举一反三：LangChain/LangGraph 设计思想

#### 模式 1：中间件链（Middleware Chain）

类似 Web 框架（如 Express/FastAPI）的 middleware，LangChain 的 `AgentMiddleware` 允许你在 LLM 调用前后插入逻辑。

```python
# 扩展中间件链可以做什么：
# 1. 输入验证：在发往 LLM 前检查消息格式
# 2. 内容过滤：屏蔽敏感词
# 3. 增强上下文：注入更多信息
# 4. 路由分发：决定走哪个 LLM（如简单问题用小模型）
# 5. 监控统计：记录每次调用的延迟
# 6. 缓存：对相同请求返回缓存结果
# 7. 重试：LLM 失败时自动重试
```

#### 模式 2：观察者模式（BaseCallbackHandler）

LangChain 的回调系统是标准的观察者模式。你可以挂载多个 Handler 做不同的事：

```python
callbacks = [
    TraceCallbackHandler(),     # 链路追踪
    TokenCountingHandler(),     # Token 计数
    LatencyAlertHandler(),      # 延迟告警
    DebugLogger(),              # 调试日志
]

agent = create_agent(model=llm, tools=tools, callbacks=callbacks)
```

#### 模式 3：工具即函数（@tool Pattern）

LangChain 的工具设计哲学是**工具就是带描述的、可被 LLM 调用的函数**。本质上是将函数签名、类型标注、文档字符串转化为 LLM 可理解的 JSON schema。

```python
# 一个 @tool 背后的 JSON Schema：
{
    "name": "calculator",
    "description": "执行数学计算",
    "parameters": {
        "type": "object",
        "properties": {
            "expression": {"type": "string", "description": "数学表达式"}
        },
        "required": ["expression"]
    }
}
```

#### 模式 4：Reducer 状态管理

LangGraph 的 Reducer 模式类似 Redux：状态更新通过 Reducer 函数归并，而不是直接覆盖。

```
add_messages reducer:
  [msg1, msg2] + [msg3] → [msg1, msg2, msg3]    # 追加
  [msg1, msg2] + [RemoveMessage(id=msg1.id)] → [msg2]  # 删除
  [msg1] + [msg1(id=1, content="updated")] → [msg1(updated)]  # 替换
```

#### 模式 5：工厂方法模式（LLM Factory）

`build_llm()` 是典型的工厂方法——返回抽象接口 `BaseChatModel`，调用方无需关心具体实现：

```python
# 统一接口，任意 Provider
llm = build_llm(settings)
response = await llm.ainvoke(messages)  # 不知道也不关心背后是哪个模型

# 测试时可以注入 mock
test_llm = FakeListChatModel(responses=["fake response"])
```

#### 模式 6：适配器模式（Search）

搜索系统的 `SearchAdapter` 是适配器模式的典型应用：

```python
# 定义接口
class SearchAdapter(ABC):
    async def search(self, query, max_results) -> SearchResponse: ...

# 多个实现
class TavilyAdapter(SearchAdapter): ...
class DuckDuckGoAdapter(SearchAdapter): ...
class BingAdapter(SearchAdapter): ...

# 客户端透明切换
adapters = [TavilyAdapter(), DuckDuckGoAdapter(), BingAdapter()]
for adapter in adapters:
    try:
        result = await adapter.search(query)
        if result.results:
            return result
    except:
        continue  # 自动 fallback
```

---

## 6. 关键设计亮点

### 6.1 零侵入可观测性

TraceCallbackHandler 通过 LangChain 回调机制采集 span，所有 agent/tool/llm 代码**完全无感知**。这与传统的手动埋点相比，大幅降低了代码侵入和维护成本。

### 6.2 层级化安全（Defense in Depth）

Shell 子系统的四层安全门从不同维度防御：解析层防注入、高危层防破坏、白名单层控制范围、确认层兜底。每层独立工作，单层失效不影响整体安全。

### 6.3 组装式 Agent 设计

Agent 的能力通过中间件链分层注入，而非在一个巨大的 system prompt 中堆砌所有指令。这使得：
- 每层职责单一（MergedContext 管上下文、ReWOO 管路由、Skill 管技能）
- 可以独立开关（ReWOO 通过环境变量控制）
- 易于扩展（新增中间件只需加到链中）

### 6.4 递归安全的多 Agent 编排

通过三层防护解决 Agent 递归这个经典问题：
1. **工具集过滤**：Sub-Agent 拿不到 `delegate_task` 工具
2. **深度限制**：`contextvars` 追踪，最大 3 层
3. **资源隔离**：独立 MemorySaver + 独立 thread_id

### 6.5 提示缓存友好

MergedContextMiddleware 按稳定性分层注入上下文，并针对 Anthropic 模型使用 `cache_control` 块标记稳定内容。这会显著降低延迟和 API 费用——在实践中最稳定的摘要部分被缓存命中。

### 6.6 成本控制的记忆系统

记忆系统的分类门控（`_classify_gate`）可跳过 50-70% 的低价值对话，LLM 调用只在必要时发出。增量摘要确保每次只处理新增消息，而非全量历史。

### 6.7 幂等与容错设计

- `SpanData.end()` 幂等：已结束 span 不会覆写时间
- `SessionRegistry.add()` 幂等：重复添加不报错
- 数据库迁移幂等：`PRAGMA table_info` 检测现有列
- JSON 写入原子性：`tmp + os.replace` 防止写损
- ReWOO 优雅降级：失败时静默回退 ReAct
- 锁保护：MemoryManager 和 Chat 各有 `asyncio.Lock` 防并发竞争

### 6.8 框架设计思想的工程落地

本项目不仅是 LangChain/LangGraph 的使用者，也体现了与这些框架一致的架构哲学：
- **接口抽象**（BaseChatModel、SearchAdapter）
- **关注点分离**（中间件链、回调与核心逻辑分离）
- **组合优于继承**（中间件组合代替继承重写）
- **不可变状态**（frozen Settings、Reducer 状态管理）
- **防御性编程**（四层安全、幂等操作、优雅降级）

---

> 本文档覆盖了 MiaoGent 项目的完整架构、所有核心模块的详细设计、LangChain/LangGraph 框架的具体使用方式，以及举一反三的设计思想。可作为面试讲解的系统性参考资料。
