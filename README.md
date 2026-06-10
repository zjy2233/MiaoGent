# MiaoGent

AI 助手框架，支持工具调用、技能市场和多 Agent 编排。

基于 LangChain/LangGraph，通过 **HTTP API** 暴露能力，提供 **Electron** 桌面客户端和 **Web** 界面。

## 特性

- **18+ 内置工具**：计算器、天气、搜索、文件操作、Python 执行、Shell 命令（4 层安全门）等
- **HTTP API 前端**：aiohttp 服务器 + Electron 桌面应用
- **多 LLM 支持**：DeepSeek / OpenAI / Anthropic Claude 全系支持，Anthropic Prompt Caching 开箱即用
- **Shell 4 层语义安全门**：SAFE 自动执行 / CONFIRM 请求确认 / HIGH_RISK 阻止 / 危险命令审计
- **链路追踪**：span 嵌套栈 + 事件流双重采集，SQLite 持久化，可视化 trace 树 + 瀑布图，Token 缓存命中率监控
- **技能市场**：支持 builtin/npm/pip/URL 安装 Skill，动态注入提示和工具
- **多 Agent 编排**：Supervisor Graph 自动分解复杂任务，ReWOO 计划-执行模式，delegate_task 委派隔离 sub-agent
- **会话管理**：多会话隔离、消息压缩、增量摘要、历史对话去工具调用展示、批量删除

## 项目结构

```
src/
├── core/                 # 核心配置
│   ├── config.py         # Settings dataclass，从 .env 加载
│   ├── llm.py            # LLM 工厂：支持 deepseek/openai/anthropic，CacheAwareChatOpenAI 子类
│   ├── miaogent_home.py  # ~/.miaogent/ 路径 + 临时目录管理
│   ├── known_skills.json # 内置已知 Skill 索引
│   └── skills_index.py   # Skill 来源索引（builtin/npm/pip/url）
├── tracing/              # 链路追踪与 Token 监控
│   ├── models.py         # SpanData 数据模型
│   ├── tracer.py         # span 生命周期管理（嵌套栈）
│   ├── handler.py        # LangChain BaseCallbackHandler 零侵入采集
│   ├── store.py          # SQLite 持久化 + 统计查询
│   ├── context.py        # TraceContext：跨 span 上下文传播
│   └── api.py            # 供 bridge.py 调用的查询接口
├── agent/                # Agent 构造
│   ├── builder.py        # LangGraph agent 构建 + 中间件
│   ├── supervisor.py     # Supervisor Graph：意图识别→规划→派发→汇总
│   ├── sub_agent.py      # Sub-agent 工厂（隔离 MemorySaver + delegate_task 委派）
│   └── memory.py         # MemoryManager：消息压缩与增量摘要
├── store/                # 数据持久化
│   ├── sessions.py       # SessionRegistry：会话注册表
│   ├── soul.py           # SoulManager + ProfileManager
│   ├── knowledge.py      # KnowledgeManager：知识库持久化
│   └── audit.py          # AuditLogger：危险命令审计
├── tools/                # LangChain @tool 工具集
│   ├── calculator.py     # AST 白名单安全数学计算
│   ├── current_time.py   # 获取当前时间
│   ├── file_operations.py# 文件读写、目录列表、创建、grep
│   ├── hot_search.py     # 百度热搜
│   ├── run_python.py     # 隔离子进程执行 Python 代码
│   ├── weather.py        # wttr.in 免费天气查询
│   ├── web_search.py     # DuckDuckGo 联网搜索
│   ├── write_file.py     # 写入文件（支持 temp=True 写入 ~/.miaogent/temp/）
│   ├── install_skill.py  # 技能市场安装/卸载/浏览
│   └── shell/            # Shell 子系统（4 层语义门）
│       ├── tool.py       # @tool shell 入口
│       ├── patterns.py   # CommandClassifier（SAFE/CONFIRM/HIGH_RISK）
│       ├── executor.py   # 沙箱子进程执行
│       └── danger.py     # 危险命令检测
├── skills/               # Skill 能力适配系统
│   ├── schema.py         # SkillDefinition + SkillTriggers 数据模型
│   ├── registry.py       # SkillRegistry：YAML 扫描 + 工具加载
│   ├── middleware.py     # SkillContextMiddleware：LLM 调用前注入上下文
│   ├── weather/          # 内置 Skill：天气查询
│   └── web_scraper/      # 内置 Skill：网页抓取

frontend/                 # HTTP API 桥接层（唯一入口）
├── bridge.py             # Api 类封装所有后端能力
├── http_server.py        # aiohttp 服务器
├── index.html            # 主页面
├── app.js                # 前端 UI 逻辑
├── styles.css            # 样式
├── assets/               # 静态资源
└── electron/             # Electron 桌面包装
    ├── main.js
    └── preload.js

~/.miaogent/              # 运行时数据（默认用户目录）
├── skills/               # 已安装的第三方 Skill
├── temp/                 # Agent 临时脚本（应用退出自动清理）
├── .sessions.json        # 会话注册表
├── history.db            # 聊天历史
├── traces.db             # 链路追踪数据
├── soul.json             # AI 角色设定
├── profile.json          # 用户画像
├── audit.db              # 审计日志
└── .ball-pos.json        # Electron 窗口位置

scripts/
├── clean_agent_temp.py   # Agent 临时文件清理工具
├── pipeline.py           # ComfyUI → Lottie 动画生成流水线
├── build_comfyui_workflows.py  # 批量构建 ComfyUI 工作流
├── pack_lottie.py        # 帧序列打包为 Lottie JSON
├── extract_frames.py     # 从视频提取帧
├── process_frames.py     # 帧后处理
├── setup_comfyui.py      # ComfyUI 环境配置
└── comfyui_workflows/    # 预定义工作流 JSON
```

## 快速开始

### 1. 准备环境

需要 Python 3.11+。

```bash
uv venv .venv --python 3.11
uv pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 2. 配置 DeepSeek API Key

复制 `.env.example` 为 `.env`，填入你的 key：

```bash
cp .env.example .env
# 编辑 .env，设置 DEEPSEEK_API_KEY=sk-...
```

### 3. 启动

**HTTP 服务器（浏览器访问）：**
```bash
.venv\Scripts\python -m frontend.http_server
```
打开浏览器访问 `http://127.0.0.1:18794`。

**Electron 桌面应用：**
```bash
# 先启动 HTTP 服务器
.venv\Scripts\python -m frontend.http_server

# 再启动 Electron（另一个终端）
cd frontend/electron && npm start
```

### 4. 运行测试

```bash
.venv\Scripts\python -m pytest -v
```

## Shell 子系统

Shell 命令经过四层安全门：

| 层级 | 模块 | 功能 |
|------|------|------|
| 1 | `patterns.py` | `CommandClassifier` 分类命令为 SAFE / CONFIRM / HIGH_RISK |
| 2 | `danger.py` | 调用分类器，决定策略 |
| 3 | `executor.py` | 沙箱子进程执行，超时控制，输出截断 |
| 4 | `tool.py` | `@tool` 装饰器入口 |

- `ls`、`cat`、`grep`、`git status` → 自动执行
- `rm`、`mv`、`curl` → 请求用户确认
- `rm -rf /`、`dd`、`fork bomb` → 阻止并审计

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/tools` | GET | 获取工具列表 |
| `/api/sessions` | GET | 获取会话列表 |
| `/api/sessions` | POST | 创建新会话 |
| `/api/soul` | GET/POST | 读取/保存 Soul |
| `/api/profile` | GET/POST | 读取/保存 Profile |
| `/api/sessions/{id}` | DELETE | 删除会话 |
| `/api/sessions/batch-delete` | POST | 批量删除会话 |
| `/api/sessions/{id}/messages` | GET | 获取会话历史消息 |
| `/api/chat` | POST | 非流式发送消息 |
| `/api/chat/stream` | POST | SSE 流式聊天 |
| `/api/skills` | GET | 获取 Skill 列表 |
| `/api/skills/{name}` | GET | 获取 Skill 详情 |
| `/api/traces` | GET | Trace 列表（支持搜索/分页） |
| `/api/traces/stats` | GET | 今日追踪统计 |
| `/api/traces/stats/daily` | GET | 近 14 日趋势 |
| `/api/traces/stats/cache` | GET | 缓存命中率统计 |
| `/api/traces/{trace_id}` | GET | Trace 详情（树形结构） |
| `/api/traces/sessions/{session_id}` | GET | 按会话查询 trace |

## 关键设计

### 工具声明

所有工具使用 LangChain `@tool` 装饰器，自动生成名称、描述和参数 schema。

### 会话管理

多会话隔离，支持切换/删除/批量删除。`MemoryManager` 在每轮结束后判断消息是否超限，触发增量摘要。历史消息加载时自动过滤工具调用内容，仅保留人机对话。

### 链路追踪

基于 `TraceCallbackHandler`（LangChain `BaseCallbackHandler`）零侵入采集 + `astream_events` 事件流双重采集 span 数据，持久化到 `~/.miaogent/traces.db`。支持 trace 树展开、瀑布图可视化、Token 统计、缓存命中率追踪。`delegate_task` 类型 span 通过 `TraceContext` 实现跨 span 上下文传播。

### 持久化

所有运行时数据默认存储在 `~/.miaogent/` 目录下，分离代码与数据。

## 扩展

- **加新工具**：在 `src/tools/` 下新建文件，用 `@tool` 装饰器声明函数
- **加新 Skill**：在 `src/skills/<name>/` 下创建 `skill.md` + `tools.py`
- **切换模型**：修改 `.env` 的 `LLM_PROVIDER` 选择 `deepseek` / `openai` / `anthropic`，配合对应的 `LLM_BASE_URL` 和 `LLM_MODEL`
