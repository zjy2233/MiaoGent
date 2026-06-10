# MiaoGent

AI 助手框架，支持工具调用、技能市场和多 Agent 编排。  
运行时数据存储在 `~/.miaogent/`（默认），支持从 builtin/npm/pip/URL 安装 Skill。

## 运行命令

```bash
# 依赖安装（需要 Python 3.11+）
uv venv .venv --python 3.11
uv pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 启动 HTTP 服务器（前端 API）
.venv\Scripts\python -m frontend.http_server

# 启动 Electron 桌面应用
cd frontend/electron && npm start

# 测试
.venv\Scripts\python -m pytest -v
```

## 项目结构

```
src/
├── __init__.py
├── core/                 # 核心配置
│   ├── __init__.py
│   ├── config.py         # Settings dataclass，从 .env 加载
│   ├── llm.py            # LLM 工厂：支持 deepseek/openai/anthropic，CacheAwareChatOpenAI 子类保留 streaming 缓存字段
│   ├── miaogent_home.py  # ~/.miaogent/ 路径工具 + get_temp_dir + cleanup_temp_dir
│   ├── known_skills.json # 内置已知 Skill 索引
│   └── skills_index.py   # Skill 来源索引（builtin/npm/pip/url）
├── tracing/              # 链路追踪与 Token 监控
│   ├── __init__.py
│   ├── models.py         # SpanData 数据模型（span_id/trace_id/type/status/tokens/duration）
│   ├── tracer.py         # Tracer：span 生命周期管理（start_span/end_span，嵌套栈）
│   ├── handler.py        # TraceCallbackHandler：LangChain BaseCallbackHandler，零侵入采集
│   ├── store.py          # TraceStore：SQLite 持久化 + 统计查询
│   ├── context.py        # TraceContext：跨 span 上下文传播（delegate_task 中切换 trace）
│   └── api.py            # TracingAPI：供 bridge.py 调用的 trace 查询接口
├── agent/                # Agent 构造
│   ├── __init__.py
│   ├── builder.py        # LangGraph agent 构建 + SummaryMiddleware + ProfileMiddleware
│   ├── supervisor.py     # Supervisor Graph：意图识别→规划→sub-agent 派发→汇总
│   ├── sub_agent.py      # Sub-agent 工厂（隔离 MemorySaver + 受限工具集，含 delegate_task 支持）
│   └── memory.py         # MemoryManager：消息压缩与增量摘要
├── store/                # 数据持久化
│   ├── __init__.py
│   ├── sessions.py       # SessionRegistry：轻量级 JSON 注册表
│   ├── soul.py           # SoulManager + ProfileManager
│   ├── knowledge.py      # KnowledgeManager：知识库持久化存储
│   └── audit.py          # AuditLogger：危险命令审计
└── tools/                # LangChain @tool 工具集
    ├── __init__.py       # 导出所有工具
    ├── calculator.py
    ├── current_time.py
    ├── file_operations.py
    ├── hot_search.py
    ├── run_python.py
    ├── weather.py
    ├── web_search.py
    ├── write_file.py     # 支持 temp=True 写入 ~/.miaogent/temp/
    ├── install_skill.py  # 技能市场安装/卸载/浏览
    └── shell/            # Shell 子系统（4 层语义门）
        ├── __init__.py
        ├── tool.py       # @tool shell 入口
        ├── patterns.py   # CommandClassifier（SAFE/CONFIRM/HIGH_RISK）
        ├── executor.py   # 沙箱子进程执行
        └── danger.py     # 危险命令检测
├── skills/               # Skill 能力适配系统
│   ├── __init__.py
│   ├── schema.py         # SkillDefinition + SkillTriggers 数据模型
│   ├── registry.py       # SkillRegistry：YAML 扫描 + 工具加载 + 消息匹配
│   ├── middleware.py     # SkillContextMiddleware：LLM 调用前注入上下文
│   ├── weather/          # 内置 Skill：天气查询（get_weather / get_forecast）
│   └── web_scraper/      # 内置 Skill：网页抓取（fetch_page / list_links）

frontend/                 # HTTP API 桥接层（唯一入口）
├── __init__.py
├── bridge.py             # Api 类：会话、设置、Soul/Profile、工具枚举、聊天
├── http_server.py        # aiohttp HTTP 服务器
├── app.js                # 前端 UI 逻辑
├── index.html            # 主页面
├── styles.css            # 样式
├── assets/               # 静态资源
└── electron/             # Electron 桌面包装
    ├── main.js
    └── preload.js

~/.miaogent/              # 运行时数据（默认用户目录）
├── skills/               # 已安装的第三方 Skill
│   ├── .miaogent-index.json  # 已安装记录
│   └── <name>/               # 每个 Skill 一个目录
│       ├── skill.md
│       └── tools.py
├── temp/                 # Agent 临时脚本（write_file temp=True）
├── .sessions.json        # 会话注册表
├── history.db            # 聊天历史
├── traces.db             # 链路追踪数据
├── soul.json             # AI 角色设定
├── profile.json          # 用户画像
├── audit.db              # Shell 审计日志
└── .ball-pos.json        # Electron 窗球位置

data/                     # 旧版数据目录（仍兼容）

scripts/                  # 辅助工具脚本
├── clean_agent_temp.py   # 清理 Agent 临时文件
├── pipeline.py           # ComfyUI → Lottie 动画生成流水线
├── build_comfyui_workflows.py  # 批量构建 ComfyUI 工作流
├── pack_lottie.py        # 帧序列打包为 Lottie JSON
├── extract_frames.py     # 从视频提取帧
├── process_frames.py     # 帧后处理（去背景/缩放）
├── setup_comfyui.py      # ComfyUI 环境配置
└── comfyui_workflows/    # 预定义工作流 JSON（mascot_idle/jump/wave 等）
```

## 架构概览

### 核心模块

- **`frontend/http_server.py`** — 唯一入口：启动 aiohttp HTTP 服务器，提供 REST API 给前端（Electron/浏览器），使用 `AsyncSqliteSaver` 做持久化；退出时自动清理临时文件
- **`src/core/llm.py`** — LLM 工厂：支持 `deepseek` / `openai` / `anthropic` 三种 provider 自动切换，`CacheAwareChatOpenAI` 子类保留 streaming 模式下 DeepSeek 的 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` 字段
- **`src/agent/builder.py`** — 提供 `build_agent()`（单 agent）、`build_rewire_agent()`（ReWOO 模式）和 `build_supervisor_agent()`（多 agent 编排）三个入口
- **`src/agent/supervisor.py`** — Supervisor Graph：`intent_router` → `planner` → `step_dispatcher` → `aggregator`；意图识别分流简单/复杂任务
- **`src/agent/sub_agent.py`** — Sub-agent 工厂：用 `create_agent` + 独立 `MemorySaver` 创建隔离执行单元，`REGULAR_TOOLS` 不含委派能力防止递归
- **`src/core/config.py`** — 从 `.env` 加载配置（API key、base_url、max_turns、max_message_chars 等）
- **`src/core/miaogent_home.py`** — `~/.miaogent/` 目录管理，自动创建目录结构，提供 `get_temp_dir()` / `cleanup_temp_dir()`，兼容旧 `data/` 路径
- **`src/agent/memory.py`** — `MemoryManager`：消息压缩与增量摘要，避免 context 溢出；含 `_drop_orphans`（清理不完整的 tool_calls/响应对）和 `_split_by_turns`（按完整 turn 切分消息）
- **`src/store/sessions.py`** — `SessionRegistry`：轻量级 JSON 注册表，管理 `~/.miaogent/.sessions.json` 中的历史 thread_id，支持批量删除
- **`src/store/knowledge.py`** — `KnowledgeManager`：知识库持久化存储，支持增删查改
- **`src/tracing/`** — 链路追踪系统：`Tracer` 管理 span 嵌套栈，`TraceCallbackHandler` 零侵入采集 LangChain 事件，`TraceStore` SQLite 持久化，`TracingAPI` 查询接口；支持 `delegate_task` 类型 span 和 `TraceContext` 跨 span 上下文传播
- **`src/skills/registry.py`** — `SkillRegistry`：扫描 `src/skills/`（内置）+ `~/.miaogent/skills/`（用户安装），加载工具和提示注入
- **`src/core/skills_index.py`** — Skill 来源索引：读取 `known_skills.json`，支持 builtin/npm/pip/url 四种安装方式
- **`src/tools/install_skill.py`** — `install_skill` / `uninstall_skill` / `list_registry` 工具，支持多种来源自动检测安装 Skill
- **`src/skills/middleware.py`** — `SkillContextMiddleware`：在 LLM 调用前注入已启用 Skill 的上下文

### Skill 用法

Skill 是按目录声明的能力包，放在 `src/skills/<name>/` 下：

```
src/skills/weather/
├── skill.md    # 名称、描述、触发条件、prompt_injection
└── tools.py    # @tool 函数 + __tool_list__ 导出
```

**运行时集成**（`builder.py`）：
1. `build_agent(session_id="xxx")` 传入 session_id 即可激活 Skill 系统
2. `SkillRegistry` 自动扫描 `src/skills/` 发现所有 Skill
3. 已启用 Skill 的 tools 合并到 agent 工具列表，prompt_injection 通过 `SkillContextMiddleware` 注入
4. 不传 `session_id` 时行为完全不变（向后兼容）

**API 端点**：`GET /api/skills`、`GET /api/skills/{name}`、`GET/POST /api/sessions/{id}/skills/enable|disable`

### Shell 子系统（`src/tools/shell/`）

四层语义安全门：
1. **`danger.py`** — 调用 `CommandClassifier` 分类命令
2. **`patterns.py`** — `SAFE` → 自动执行，`CONFIRM` → 请求用户确认，`HIGH_RISK` → 阻止
3. **`executor.py`** — 沙箱子进程执行，超时控制，输出截断
4. **`tool.py`** — `@tool` 装饰器入口，暴露为 LangChain 工具

### Tracing 链路追踪（`src/tracing/`）

基于 LangChain `BaseCallbackHandler` 的零侵入追踪，span 数据持久化到 SQLite：

1. **`TraceCallbackHandler`** — 挂载到 agent 执行链上，通过 `on_llm_start/on_tool_start/on_chain_start` 等回调采集 span
2. **`Tracer`** — 维护 span 嵌套栈，自动继承 `trace_id`，确保父子关系正确
3. **`TraceStore`** — SQLite 存储，支持按 trace_id/session/日期查询，提供统计聚合（tokens、duration、error_rate）
4. **`TracingAPI`** — 供 `bridge.py` 调用，提供 trace 树展开、每日统计、会话维度查询

**API 端点**：
- `GET /api/traces` — trace 列表（支持搜索、状态过滤、分页）
- `GET /api/traces/stats` — 今日统计（tokens、duration、error_rate）
- `GET /api/traces/stats/daily` — 近 14 日趋势
- `GET /api/traces/{trace_id}` — trace 详情（树形结构）
- `GET /api/traces/{trace_id}/spans` — 原始 span 列表
- `GET /api/traces/sessions/{session_id}` — 按会话查询

### ReWOO 计划-执行模式（`src/agent/builder.py`）

ReWOO（Reason Without Observation）将复杂任务拆分为"计划 → 批量执行 → 汇总"三阶段：

1. **`planner`** — LLM 分析用户问题，输出包含依赖关系的步骤列表
2. **`worker`** — 按步骤生成批量工具调用（含 delegate_task 委派 sub-agent），一次性并行执行
3. **`solver`** — 汇总所有执行结果，生成最终回复

相比 Supervisor 模式的优势：工具调用结果不反馈回 LLM，大幅降低 token 消耗。

### 多 Agent Supervisor 模式（`src/agent/supervisor.py` + `sub_agent.py`）

在单 agent 之上叠加 Supervisor Graph 实现复杂任务编排：

1. **`intent_router`** — LLM 分类消息复杂度，返回 `"direct"` 或 `"plan_and_execute"`
2. **`existing_agent`** — "direct" 路径，复用原单 agent 子图（含所有工具+中间件）
3. **`planner`** — "plan_and_execute" 路径，LLM 分解任务为步骤列表
4. **`step_dispatcher`** — 循环执行每个步骤，动态创建隔离的 sub-agent（独立 MemorySaver）
5. **`aggregator`** — 汇总所有 sub-agent 结果

**防无限递归设计**：
- sub-agent 只拿到 `REGULAR_TOOLS`（无委派能力）
- `step_dispatcher` 是 Python 节点函数，不是 LLM 可调用的工具
- `current_step >= len(plan)` 硬条件终止循环

### 持久化策略

- 所有运行时数据默认存储在 `~/.miaogent/` 目录下（兼容旧 `data/`）：
  - `history.db` — `AsyncSqliteSaver` 检查点存储
  - `traces.db` — Tracing span 数据（SQLite）
  - `.sessions.json` — `SessionRegistry` 会话注册表
  - `temp/` — Agent 临时脚本文件（write_file temp=True），应用退出时自动清理
  - `soul.json` — AI 角色设定
  - `profile.json` — 用户画像
  - `audit.db` — 危险命令审计日志
  - `skills/` — 从市场安装的第三方 Skill

### 提交规范

- **小步提交**：每完成一个独立功能点或修复立即提交，不攒批
- 提交格式：`<type>: <description>`（type: feat/fix/refactor/docs/test/chore/perf/ci）
- 不要在一个 commit 中混合无关改动

### 已知约束

- 所有 agent 调用均为异步（`ainvoke` / `astream_events`），必须通过 `asyncio.run()` 或 aiohttp handler 调用
- 能使用 langchain 和 langGraph 框架内置功能的就不要重复造轮子
- 必须用中文回复用户
