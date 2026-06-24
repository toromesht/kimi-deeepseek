#!/usr/bin/env python3
"""
Kimi-DeepSeek 多模型代码流水线 —— 终端对话框版（Claude Code 风格）

用法:
  python code_chat.py
  kchat

操作:
  - 底部输入框输入问题，按 Enter 发送
  - 支持粘贴文件路径，会自动读取文件内容
  - 多轮对话保留上下文
  - Ctrl+L 清空，Ctrl+C 退出，Ctrl+S 保存对话
"""
import sys
import os
import time
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Header, Footer, Input, Static, RichLog
from textual import work
from rich.syntax import Syntax
from rich.markdown import Markdown
from rich.text import Text
from rich.rule import Rule

import pipeline


HISTORY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history")


class CodeChatApp(App):
    """Claude Code 风格终端对话框"""

    CSS = """
    Screen { align: center middle; }

    #chat-log {
        width: 100%;
        height: 1fr;
        padding: 0 2;
        border: none;
        background: $surface-darken-1;
    }

    #status-bar {
        width: 100%;
        height: 1;
        content-align: left middle;
        padding: 0 2;
        color: $text-muted;
        background: $surface;
    }

    #input-row {
        width: 100%;
        height: auto;
        dock: bottom;
        padding: 1 2;
        background: $surface;
    }

    #msg-input {
        width: 1fr;
    }

    .user-label {
        color: cyan;
        text-style: bold;
    }

    .assistant-label {
        color: green;
        text-style: bold;
    }

    .system-label {
        color: yellow;
        text-style: bold;
    }
    """

    BINDINGS = [
        ("ctrl+c", "quit", "退出"),
        ("ctrl+l", "clear", "清空"),
        ("ctrl+s", "save", "保存"),
    ]

    def __init__(self, **kwargs):
        self.history = []
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield RichLog(id="chat-log", wrap=True, highlight=False, markup=False)
        yield Static("就绪 | Kimi → KimiCode → DeepSeek → KimiCode → DeepSeek", id="status-bar")
        with Horizontal(id="input-row"):
            yield Input(placeholder="输入问题，支持粘贴文件路径...", id="msg-input")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#msg-input", Input).focus()
        self._print_welcome()

    def _print_welcome(self):
        log = self.query_one("#chat-log", RichLog)
        log.write("")
        log.write(Text("Kimi-DeepSeek 代码助手", style="bold bright_white"))
        log.write(Text("输入问题生成代码，支持粘贴文件路径自动读取。Ctrl+S 保存对话，Ctrl+L 清空。", style="dim"))
        log.write("")

    def _set_status(self, text: str):
        self.query_one("#status-bar", Static).update(text)

    def _add_message(self, role: str, content: str, is_code: bool = False):
        log = self.query_one("#chat-log", RichLog)
        timestamp = datetime.now().strftime("%H:%M:%S")

        if role == "user":
            label = Text(f"[{timestamp}] 你", style="bold cyan")
            log.write(label)
            log.write(Text(content))
        elif role == "assistant":
            label = Text(f"[{timestamp}] Kimi-DeepSeek", style="bold green")
            log.write(label)
            if is_code:
                # 自动检测语言，默认 python
                lang = self._detect_language(content)
                log.write(Syntax(content, lang, theme="monokai", line_numbers=True, word_wrap=True))
            else:
                log.write(Markdown(content))
        elif role == "system":
            label = Text(f"[{timestamp}] 系统", style="bold yellow")
            log.write(label)
            log.write(Text(content, style="dim"))

        log.write("")

    def _detect_language(self, code: str) -> str:
        if code.strip().startswith("<") and ">" in code:
            return "html"
        if "function" in code and "{" in code and "console.log" in code:
            return "javascript"
        if "package main" in code or "func " in code:
            return "go"
        if "fn " in code or "let " in code:
            return "rust"
        return "python"

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._send_message()

    def _send_message(self) -> None:
        input_widget = self.query_one("#msg-input", Input)
        text = input_widget.value.strip()
        if not text:
            return
        input_widget.value = ""
        input_widget.disabled = True

        self.history.append({"role": "user", "content": text, "time": time.time()})
        self._add_message("user", text)
        self._set_status("思考中...")
        self.run_pipeline(text)

    @work(exclusive=True)
    async def run_pipeline(self, question: str) -> None:
        try:
            t0 = time.time()
            result = pipeline.run(question, verbose=False)
            elapsed = time.time() - t0

            if result is None:
                self.call_from_thread(self._on_error, "流水线返回空结果")
            elif "error" in result:
                self.call_from_thread(self._on_error, f"Step {result.get('step', '?')} 失败: {result['error']}")
            else:
                code = result.get("code", "")
                paths = result.get("paths", [])
                self.history.append({"role": "assistant", "content": code, "time": time.time()})
                self.call_from_thread(self._add_message, "assistant", code, True)
                if paths:
                    files = "  ".join(os.path.basename(p) for p in paths)
                    self.call_from_thread(self._set_status, f"完成 {elapsed:.1f}s | {files}")
                else:
                    self.call_from_thread(self._set_status, f"完成 {elapsed:.1f}s")
        except Exception as e:
            import traceback
            err = f"{e}\n\n{traceback.format_exc()}"
            self.call_from_thread(self._on_error, err)
        finally:
            def enable():
                self.query_one("#msg-input", Input).disabled = False
                self.query_one("#msg-input", Input).focus()
            self.call_from_thread(enable)

    def _on_error(self, err: str):
        self._add_message("system", f"❌ {err}")
        self._set_status("运行失败")

    def action_clear(self) -> None:
        log = self.query_one("#chat-log", RichLog)
        log.clear()
        self.history.clear()
        self._print_welcome()
        self._set_status("对话已清空")

    def action_save(self) -> None:
        os.makedirs(HISTORY_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(HISTORY_DIR, f"chat_{ts}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.history, f, ensure_ascii=False, indent=2)
        self._add_message("system", f"对话已保存: {path}")


if __name__ == "__main__":
    missing = []
    for k, svc in [("KIMI_KEY", "kimi"), ("KIMI_CODE_KEY", "kimi_code"), ("DEEPSEEK_KEY", "deepseek")]:
        if not pipeline.CFG[svc]["key"]:
            missing.append(k)
    if missing:
        print(f"缺少环境变量: {', '.join(missing)}")
        print("请检查 .env 文件或环境变量。")
        sys.exit(2)

    app = CodeChatApp()
    app.run()
