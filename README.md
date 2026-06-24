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

## 使用

```bash
# 方式 1：直接运行
python pipeline.py "用 Python 写一个 Flask REST API，支持用户注册和登录"

# 方式 2：标准输入
echo "用 Go 写一个并发爬虫" | python pipeline.py
```

推荐 shell alias（把 `code-` 变成调用入口）：

```bash
alias code-='python "C:/Users/ShortPome/kimi-deeepseek/pipeline.py"'
# 之后就可以：
code- "用 Rust 写一个 CLI 计时器"
```

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
