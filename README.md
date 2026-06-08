# LangChain Agent: Frontend + HTTP API

一个基于 LangChain/LangGraph 的多工具 Agent，通过 **HTTP API** 暴露能力，提供 **Electron** 桌面客户端和 **Web** 界面。

## 特性

- **11 个内置工具**：计算器、天气、搜索、文件操作、Python 执行、Shell 命令（4 层安全门）等
- **HTTP API 前端**：aiohttp 服务器 + Electron 桌面应用
- **Shell 4 层语义安全门**：SAFE 自动执行 / CONFIRM 请求确认 / HIGH_RISK 阻止 / 危险命令审计
- **DeepSeek LLM**：兼容 OpenAI API 格式，可切换其他模型
- **会话管理**：多会话隔离、消息压缩、增量摘要

## 项目结构

```
src/
├── core/                 # 核心配置
│   ├── config.py         # Settings dataclass，从 .env 加载
│   └── llm.py            # DeepSeek LLM 工厂（ChatOpenAI）
├── agent/                # Agent 构造
│   ├── builder.py        # LangGraph agent 构建 + 中间件
│   └── memory.py         # MemoryManager：消息压缩与增量摘要
├── store/                # 数据持久化
│   ├── sessions.py       # SessionRegistry：会话注册表
│   ├── soul.py           # SoulManager + ProfileManager
│   └── audit.py          # AuditLogger：危险命令审计
└── tools/                # LangChain @tool 工具集
    ├── calculator.py     # AST 白名单安全数学计算
    ├── current_time.py   # 获取当前时间
    ├── file_operations.py# 文件读写、目录列表、创建、grep
    ├── hot_search.py     # 百度热搜
    ├── run_python.py     # 隔离子进程执行 Python 代码
    ├── weather.py        # wttr.in 免费天气查询
    ├── web_search.py     # DuckDuckGo 联网搜索
    ├── write_file.py     # 写入文件
    └── shell/            # Shell 子系统（4 层语义门）
        ├── tool.py       # @tool shell 入口
        ├── patterns.py   # CommandClassifier（SAFE/CONFIRM/HIGH_RISK）
        ├── executor.py   # 沙箱子进程执行
        └── danger.py     # 危险命令检测

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

data/                     # 运行时数据
├── .sessions.json        # 会话注册表
├── history.db            # 检查点存储
├── soul.json             # AI 角色设定
├── profile.json          # 用户画像
├── audit.db              # 审计日志
└── .ball-pos.json        # Electron 窗口位置
```

## 快速开始

### 1. 准备环境

需要 Python 3.11+。

```bash
cd single-agent
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
# 先启动 HTTP 服务器（另一个终端）
.venv\Scripts\python -m frontend.http_server

# 再启动 Electron
cd frontend/electron && npm start
```

### 4. 运行测试

```bash
.venv\Scripts\python -m pytest -v
```

预期：200 passed。

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
| `/api/chat/{thread_id}` | POST | 发送消息 |
| `/api/chat/{thread_id}/stream` | GET | SSE 流式聊天 |

## 关键设计

### 工具声明

所有工具使用 LangChain `@tool` 装饰器，自动生成名称、描述和参数 schema。

### 会话管理

多会话隔离，支持切换/删除会话。`MemoryManager` 在每轮结束后判断消息是否超限，触发增量摘要。

### 持久化

所有运行时数据存储在 `data/` 目录，分离代码与数据。

## 扩展

- **加新工具**：在 `src/tools/` 下新建文件，用 `@tool` 装饰器声明函数
- **切换模型**：修改 `.env` 的 `DEEPSEEK_BASE_URL` 和 `DEEPSEEK_MODEL`
