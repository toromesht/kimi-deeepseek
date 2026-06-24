#!/usr/bin/env python3
"""
Kimi-DeepSeek 多模型代码流水线 —— 终端对话框版

用法:
  python code_chat.py

操作:
  - 底部输入框输入问题，按 Enter 发送
  - 支持粘贴文件路径，会自动读取文件内容
  - 多轮对话会保留上下文
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from textual.app import App, ComposeResult
from textual.containers import Vertical, Horizontal
from textual.widgets import Header, Footer, Input, Button, Static
from textual import work
from rich.syntax import Syntax
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

import pipeline


class Message(Static):
    """单条消息组件"""

    def __init__(self, role: str, content: str, is_code: bool = False, **kwargs):
        self._role = role
        self._content = content
        self._is_code = is_code
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        if self._role == "user":
            header = Text("你", style="bold cyan")
            body = Text(self._content)
            panel = Panel(body, title=header, border_style="cyan")
        elif self._role == "assistant":
            header = Text("Kimi-DeepSeek", style="bold green")
            if self._is_code:
                body = Syntax(self._content, "python", theme="monokai", line_numbers=True, word_wrap=True)
            else:
                body = Markdown(self._content)
            panel = Panel(body, title=header, border_style="green")
        elif self._role == "system":
            header = Text("系统", style="bold yellow")
            panel = Panel(Text(self._content), title=header, border_style="yellow")
        else:
            header = Text(self._role, style="bold")
            panel = Panel(Text(self._content), title=header)
        yield Static(panel)


class CodeChatApp(App):
    """终端对话框应用"""

    CSS = """
    Screen { align: center middle; }

    #chat-container {
        width: 100%;
        height: 100%;
        border: solid $primary;
    }

    #chat-log {
        width: 100%;
        height: 1fr;
        overflow-y: auto;
        padding: 1 2;
    }

    #input-row {
        width: 100%;
        height: auto;
        dock: bottom;
        padding: 1 2;
    }

    #msg-input {
        width: 1fr;
    }

    #send-btn {
        width: auto;
        margin-left: 1;
    }

    .message {
        margin-bottom: 1;
    }
    """

    BINDINGS = [
        ("ctrl+c", "quit", "退出"),
        ("ctrl+l", "clear", "清空对话"),
    ]

    def __init__(self, **kwargs):
        self.history = []
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="chat-container"):
            yield Vertical(id="chat-log")
            with Horizontal(id="input-row"):
                yield Input(placeholder="输入问题，支持粘贴文件路径...", id="msg-input")
                yield Button("发送", id="send-btn", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        self.add_system_message(
            "欢迎使用 Kimi-DeepSeek 代码流水线对话框。\n"
            "输入问题后我会调用 Kimi → KimiCode → DeepSeek → KimiCode(批判修复) → DeepSeek(终版) 生成代码。\n"
            "快捷键: Ctrl+C 退出 | Ctrl+L 清空"
        )
        self.query_one("#msg-input", Input).focus()

    def add_message(self, role: str, content: str, is_code: bool = False) -> None:
        log = self.query_one("#chat-log", Vertical)
        msg = Message(role, content, is_code= is_code, classes="message")
        log.mount(msg)
        self.call_after_refresh(log.scroll_end, animate=False)

    def add_system_message(self, content: str) -> None:
        self.add_message("system", content)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "send-btn":
            self.send_message()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.send_message()

    def send_message(self) -> None:
        input_widget = self.query_one("#msg-input", Input)
        text = input_widget.value.strip()
        if not text:
            return
        input_widget.value = ""
        input_widget.disabled = True
        self.query_one("#send-btn", Button).disabled = True

        self.history.append({"role": "user", "content": text})
        self.add_message("user", text)
        self.run_pipeline(text)

    @work(exclusive=True)
    async def run_pipeline(self, question: str) -> None:
        try:
            # 先显示思考中
            self.call_from_thread(self.add_system_message, "🤔 正在调用多模型流水线，请稍候...")

            t0 = time.time()
            result = pipeline.run(question, verbose=False)
            elapsed = time.time() - t0

            if result is None:
                self.call_from_thread(self.add_system_message, "流水线返回空结果")
            elif "error" in result:
                self.call_from_thread(
                    self.add_system_message,
                    f"❌ 流水线失败 (Step {result.get('step', '?')}): {result['error']}"
                )
            else:
                code = result.get("code", "")
                paths = result.get("paths", [])
                self.history.append({"role": "assistant", "content": code})
                self.call_from_thread(self.add_message, "assistant", code, is_code=True)
                if paths:
                    files = "\n".join(f"- {p}" for p in paths)
                    self.call_from_thread(
                        self.add_system_message,
                        f"✅ 完成，耗时 {elapsed:.1f}s\n中间文件:\n{files}"
                    )
        except Exception as e:
            import traceback
            err = f"{e}\n\n{traceback.format_exc()}"
            self.call_from_thread(self.add_system_message, f"❌ 运行异常:\n{err}")
        finally:
            def enable_input():
                self.query_one("#msg-input", Input).disabled = False
                self.query_one("#send-btn", Button).disabled = False
                self.query_one("#msg-input", Input).focus()
            self.call_from_thread(enable_input)

    def action_clear(self) -> None:
        log = self.query_one("#chat-log", Vertical)
        log.remove_children()
        self.history.clear()
        self.add_system_message("对话已清空。")


if __name__ == "__main__":
    # 检查 key
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
