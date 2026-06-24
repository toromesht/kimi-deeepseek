# kimi-deepseek

多模型代码生成流水线

## 流程

```
用户问题
  → Kimi (写提示词)
  → Kimi-Code (写框架)
  → DeepSeek-Pro 满血版 (写代码)
  → Kimi-Code (检验 + 批判性质疑 + 修复)
  → DeepSeek (复查批判建议，输出终版)
```

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
- 实时显示 5 步流水线进度
- 最终代码高亮展示
- 自动列出所有中间文件

### 2. 命令行版

```bash
python pipeline.py "用 Python 写一个 Flask REST API，支持用户注册和登录"
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

### 5. Web 网页版 `kweb`（最像 Kimi）

启动 Web 服务器，浏览器打开 `http://127.0.0.1:5000`：

```bash
python web_chat.py
```

```powershell
kweb    # PowerShell alias，新开窗口生效
```

```bash
kweb    # Git Bash alias
```

特点：
- 类似 Kimi 网页端的干净界面
- 左侧边栏 + 中间对话
- 消息气泡
- 代码语法高亮
- 实时显示流水线 5 步进度
- 支持粘贴文件路径

### 6. 终端对话框版 `kchat`

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
| `KIMI_BASE` | Kimi API Base URL | 否 |
| `DEEPSEEK_BASE` | DeepSeek API Base URL | 否 |
| `KIMI_CODE_BASE` | Kimi-Code API Base URL | 否 |
| `KIMI_MODEL` / `KIMI_CODE_MODEL` / `DEEPSEEK_MODEL` | 模型名称 | 否 |

## 输出

每次运行会在 `outputs/` 目录保存 5 个中间文件：

- `01_prompt_*.md` — Kimi 生成的提示词
- `02_framework_*.md` — KimiCode 写的框架
- `03_code_*.md` — DeepSeek 初版代码
- `04_review_*.md` — KimiCode 的批判性审查与修复
- `05_final_*.md` — DeepSeek 终版代码

## 项目结构

```
.
├── .claude/commands/code.json     # Claude Code slash 命令
├── .env.example                   # 密钥模板
├── .gitignore                     # 忽略 .env / outputs
├── README.md
├── code_tui.py                    # 终端页面版入口
├── code_chat.py                   # 终端对话框版入口
└── pipeline.py                    # 核心流水线
```
