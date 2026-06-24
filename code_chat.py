#!/usr/bin/env python3
"""
Kimi-Code 终端对话框

用法:
  python code_chat.py
  kchat
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

import pipeline


class KimiCodeChat(App):
    """简洁终端对话应用"""

    def __init__(self, **kwargs):
        self.history = []
        super().__init__(**kwargs)

    CSS = """
    Screen { align: center middle; }

    #chat-log {
        width: 100%;
        height: 1fr;
        padding: 0 1;
        border: none;
        background: $surface;
    }

    #status-bar {
        width: 100%;
        height: 1;
        content-align: left middle;
        padding: 0 1;
        color: $text-muted;
        background: $surface;
    }

    #input-row {
        width: 100%;
        height: auto;
        dock: bottom;
        padding: 0 1;
        background: $surface;
    }

    #msg-input {
        width: 1fr;
    }
    """

    BINDINGS = [
        ("ctrl+c", "quit", "退出"),
        ("ctrl+l", "clear", "清空"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield RichLog(id="chat-log", wrap=True, highlight=False, markup=False)
        yield Static("Kimi-Code 终端版 | 输入问题开始", id="status-bar")
        with Horizontal(id="input-row"):
            yield Input(placeholder="输入问题...", id="msg-input")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#msg-input", Input).focus()
        self._log().write(Text("Kimi-Code 终端版已启动。输入问题生成代码。", style="dim"))
        self._log().write("")

    def _log(self) -> RichLog:
        return self.query_one("#chat-log", RichLog)

    def _status(self, text: str):
        self.query_one("#status-bar", Static).update(text)

    def _add_user(self, text: str):
        self._log().write(Text(f"> {text}", style="cyan"))
        self._log().write("")

    def _add_ai(self, text: str):
        # 简单语言检测
        lang = "python"
        if text.strip().startswith("<"):
            lang = "html"
        elif "function" in text and "{" in text:
            lang = "javascript"
        elif "package main" in text or "func " in text:
            lang = "go"

        self._log().write(Syntax(text, lang, theme="monokai", line_numbers=False, word_wrap=True))
        self._log().write("")

    def _add_system(self, text: str):
        self._log().write(Text(text, style="dim yellow"))
        self._log().write("")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._send()

    def _send(self) -> None:
        inp = self.query_one("#msg-input", Input)
        text = inp.value.strip()
        if not text:
            return
        inp.value = ""
        inp.disabled = True

        self.history.append({"role": "user", "content": text})
        self._add_user(text)
        self._status("思考中...")
        self.run_pipeline(text)

    @work(exclusive=True, thread=True)
    def run_pipeline(self, question: str) -> None:
        try:
            t0 = time.time()
            result = pipeline.run(question, history=self.history, verbose=False)
            elapsed = time.time() - t0

            if result is None:
                self.call_from_thread(self._add_system, "❌ 流水线返回空结果")
            elif "error" in result:
                self.call_from_thread(self._add_system, f"❌ {result['error']}")
            else:
                code = result.get("code", "")
                self.history.append({"role": "assistant", "content": code})
                self.call_from_thread(self._add_ai, code)
                self.call_from_thread(self._status, f"完成 {elapsed:.1f}s")
        except Exception as e:
            import traceback
            self.call_from_thread(self._add_system, f"❌ {e}\n{traceback.format_exc()}")
        finally:
            def enable():
                self.query_one("#msg-input", Input).disabled = False
                self.query_one("#msg-input", Input).focus()
            self.call_from_thread(enable)

    def action_clear(self) -> None:
        self.history.clear()
        self._log().clear()
        self._log().write(Text("Kimi-Code 终端版已启动。输入问题生成代码。", style="dim"))
        self._log().write("")
        self._status("已清空")


if __name__ == "__main__":
    missing = pipeline.check_missing_keys()
    if missing:
        print(f"缺少环境变量: {', '.join(missing)}")
        sys.exit(2)

    app = KimiCodeChat()
    app.run()
