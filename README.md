# kimi-deepseek

多模型代码生成流水线，采用 **Reflexion + Capability-Aware Router** 架构：

- **Planner**（Kimi）：根据需求输出结构化实现方案与详细 Prompt。
- **Coder**（DeepSeek）：利用大上下文能力生成完整可运行代码。
- **Critic**（KimiCode）：批判性审查，列出问题并给出修复版代码。
- **Validator**：静态语法检查 + LLM 功能验证。
- **Repairer**（DeepSeek）：综合审查与验证报告，输出终版代码。

三种模式按需选择：

| 模式 | 流程 | 适用场景 |
|------|------|----------|
| `full` | Planner → Coder → Critic → Validator → Repairer | 复杂任务，质量最高 |
| `fast` | DeepSeek Coder → DeepSeek Repairer | 中等任务，速度质量平衡 |
| `ultra` | DeepSeek 单模型 CoT + 自我审查 | 简单任务，响应最快 |

## 安装

```bash
git clone https://github.com/toromesht/kimi-deeepseek.git
cd kimi-deeepseek
```

复制示例配置文件并填入真实 key：

```bash
cp .env.example .env
# 编辑 .env 填入 KIMI_KEY / KIMI_CODE_KEY / DEEPSEEK_KEY
```

## 使用方式

### 1. 终端页面版（推荐）

```bash
python code_tui.py
python code_tui.py "用 Python 写一个 Flask REST API，支持用户注册和登录"
```

效果：
- 实时显示流水线进度（Planner / Coder / Critic / Validator / Repairer）
- 最终代码高亮展示
- 自动列出所有中间文件

### 2. 命令行版

```bash
python pipeline.py "用 Python 写一个 Flask REST API，支持用户注册和登录"
```

切换模式：

```bash
PIPELINE_MODE=ultra python pipeline.py "写一个 Python 快速排序"
```

### 3. Claude Code 插件

把仓库放到任意位置后，Claude Code 会自动识别 `.claude/commands/code.json`：

```bash
# 在 Claude Code 中输入
/code 用 Python 写一个 Flask REST API，支持用户注册和登录
```

如果你把仓库 clone 到了其他位置，需要修改 `.claude/commands/code.json` 里的 `command` 路径。

### 4. 快捷命令 `kcode`

**PowerShell**（已写入 profile，新开窗口生效）：

```powershell
kcode "用 Rust 写一个 CLI 计时器"
kcode                 # 不带参数会提示输入
```

**Git Bash**（已写入 `~/.bashrc`）：

```bash
kcode "用 Rust 写一个 CLI 计时器"
```

> 用 `kcode` 而不是 `code`，避免和 VS Code 的 `code` 命令冲突。

### 5. 终端对话框版 `kchat`

类似 Kimi / Claude 的聊天界面，支持多轮对话：

```bash
python code_chat.py
```

```powershell
kchat    # PowerShell alias，新开窗口生效
```

```bash
kchat    # Git Bash alias
```

功能：
- 底部输入框，Enter 发送
- 代码块语法高亮
- 支持粘贴文件路径自动读取
- Ctrl+L 清空对话

## 环境变量

| 变量 | 说明 | 必填 |
|------|------|------|
| `KIMI_KEY` | Kimi API Key | 是 |
| `DEEPSEEK_KEY` | DeepSeek API Key | 是 |
| `KIMI_CODE_KEY` | Kimi-Code API Key | 是 |
| `PIPELINE_MODE` | `full` / `fast` / `ultra` | 否 |
| `SMART_ATTACH` | 智能附件路由 `auto` / `1` / `0` | 否 |
| `MAX_ATTACH_CHARS` | 附件截断长度，`0` 表示不截断 | 否 |
| `KIMI_CONTEXT_LIMIT` | Kimi 安全上下文 token 数 | 否 |
| `DS_CONTEXT_LIMIT` | DeepSeek 安全上下文 token 数 | 否 |
| `API_MAX_RETRIES` | API 失败重试次数 | 否 |
| `API_TIMEOUT` | API 超时秒数 | 否 |
| `OUTPUT_DIR` | 中间文件输出目录 | 否 |

## 上下文长度策略

- **DeepSeek**：默认保留高达 `DS_CONTEXT_LIMIT`（100K token）的完整上下文，完整文件、历史对话、长代码全部喂入。
- **Kimi / KimiCode**：默认限制在 `KIMI_CONTEXT_LIMIT`（6K token），超过时自动截断；开启 `SMART_ATTACH=1` 后 Kimi 只看文件路径，DeepSeek 看完整内容。
- 任意模型失败时，Router 会按角色能力自动回退到其他模型。

## 输出

每次运行会在 `outputs/` 目录保存中间文件：

- `planner_*.md` — 实现方案与详细 Prompt
- `coder_*.md` — DeepSeek 初版代码
- `critic_*.md` — 批判性审查与修复
- `validator_*.md` — 静态检查 + LLM 验证报告
- `repairer_*.md` — 终版代码
- `ultra_*.md` — ultra 模式单模型输出

## 项目结构

```
.
├── .claude/commands/code.json     # Claude Code slash 命令
├── .env.example                   # 密钥模板
├── .gitignore                     # 忽略 .env / outputs
├── README.md
├── code_tui.py                    # 终端页面版入口
├── code_chat.py                   # 终端对话框版入口
├── pipeline.py                    # 流水线编排器
├── pipeline_core.py               # 配置、模型客户端、上下文管理
└── pipeline_steps.py              # Planner / Coder / Critic / Validator / Repairer
```
