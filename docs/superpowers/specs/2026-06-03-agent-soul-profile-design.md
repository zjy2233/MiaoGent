# Agent Soul & 用户画像 功能设计

## 概述

新增两个功能：
1. **Agent Soul** — 用户自定义 agent 回复风格，支持自然语言描述
2. **用户画像** — 全局共享的用户信息，分"显式配置"和"自动发现"两种来源

---

## 数据结构

### soul.json

存放用户自定义的风格描述。文件路径：项目根目录 `soul.json`。

```json
{
  "version": 1,
  "description": "你是一个幽默、简洁的助手，回复喜欢用 emoji，总结论点用 Markdown 列表。"
}
```

- `version`：格式版本，便于未来迁移
- `description`：自然语言风格描述，会拼接到 system prompt 头部

### profile.json

存放用户画像，全局共享（所有会话通用）。文件路径：项目根目录 `profile.json`。

```json
{
  "version": 1,
  "name": "张三",
  "name_source": "explicit",
  "occupation": "软件工程师",
  "occupation_source": "discovered",
  "interests": ["编程", "阅读"],
  "interests_source": "discovered",
  "language": "zh",
  "language_source": "explicit"
}
```

- 每个字段附带 `_source` 后缀，记录来源：`explicit`（用户显式配置）或 `discovered`（agent 自动发现）
- 字段可自由扩展，agent 发现新事实后可自行添加新字段
- `_source` 字段本身固定用于记录来源，不存储实际值

---

## 注入机制

### Soul 注入

在 `build_agent` 时读取 `soul.json`，拼接进 system prompt：

```
你是一个<soul.description>的助手。
<原始 SYSTEM_PROMPT 行为准则>
```

实现方式：在 `create_agent` 的 `system_prompt` 参数中动态拼接。

### Profile 注入

通过新增 `ProfileMiddleware`（与 `SummaryMiddleware` 同级）注入：

```
[用户画像]
name: 张三
occupation: 软件工程师
interests: 编程, 阅读
```

注入位置：主 system prompt 之后、第一条 human 消息之前，与 `SummaryMiddleware` 顺序无关。

### Profile 自动发现

在 `MemoryManager.compress_if_needed` 触发压缩时，额外调用 LLM 提取本轮对话中用户透露的新事实。

Prompt 示例：
```
从以下对话中提取用户的事实信息（姓名、职业、兴趣、偏好等），
以 JSON key-value 形式返回，只返回有实际内容的字段：
<本轮对话>
```

提取结果合并入 `profile.json`（仅追加新字段，不覆盖已有字段）。

---

## REPL 命令

内置命令用于查看和编辑 soul / profile：

| 命令 | 说明 |
|------|------|
| `:soul` | 查看当前风格描述 |
| `:soul <文本>` | 更新风格描述并写入 `soul.json` |
| `:profile` | 查看完整用户画像 |
| `:profile get <字段>` | 查看指定字段 |
| `:profile set <字段> <值>` | 显式设置某字段 |
| `:profile unset <字段>` | 删除某字段 |

---

## 实现文件

| 文件 | 改动内容 |
|------|---------|
| `src/agent.py` | 新增 `ProfileMiddleware`；修改 `build_agent` 读取并注入 soul |
| `src/memory.py` | `MemoryManager` 增加 `_discover_profile_facts` 方法 |
| `src/soul.py` | 新模块：加载/保存 soul 和 profile，提供 `SoulManager` 和 `ProfileManager` |
| `src/main.py` | 新增 REPL 命令处理 `:soul` 和 `:profile` |
| `soul.json` | 用户风格配置文件（新建） |
| `profile.json` | 用户画像配置文件（新建） |

---

## 错误处理

- `soul.json` 不存在或格式错误 → 使用默认空风格，继续运行
- `profile.json` 不存在 → 自动创建空文件
- Profile 更新时 JSON 解析失败 → 打印错误，不更新
- 自动发现 LLM 调用失败 → 静默降级，不触发压缩失败

---

## 测试策略

- `test_soul.py`：验证 soul 加载、拼接注入、REPL 命令
- `test_profile.py`：验证 profile 读写、字段来源标记、auto-discover 逻辑
- 集成测试：手动 end-to-end 流程（设置 soul/profile → 对话 → 检查 profile 变更）