#!/usr/bin/env python3
"""
Kimi-DeepSeek 终端聊天（类 Claude Code 内联界面）

用法:
  python code_chat.py
  kchat

命令:
  /quit        退出
  /clear       清空屏幕和对话历史
  /mode full   切换模式: full / fast / ultra
  /history     显示当前历史轮数
"""
import sys
import os
import time
import threading
from queue import Queue

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rich.console import Console
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.panel import Panel
from rich.text import Text
from rich.rule import Rule

import pipeline
from pipeline import Pipeline, Mode


console = Console()


class TerminalChat:
    """内联终端聊天：消息滚动、底部输入、显示思考状态。"""

    def __init__(self):
        self.history = []
        self.mode = pipeline.Config.PIPELINE_MODE
        if self.mode not in ("full", "fast", "ultra"):
            self.mode = "full"

    def _detect_language(self, code: str) -> str:
        code = code.strip()
        if code.startswith("<") and ">" in code:
            return "html"
        if "package main" in code or ("func " in code and "func main" in code):
            return "go"
        if "function" in code and "{" in code:
            return "javascript"
        if "#include" in code or "int main(" in code:
            return "cpp"
        return "python"

    def _print_user(self, text: str):
        console.print(Text(f"> {text}", style="bold cyan"))

    def _print_ai(self, text: str):
        lang = self._detect_language(text)
        console.print(Panel(
            Syntax(text, lang, theme="monokai", line_numbers=False, word_wrap=True),
            border_style="green",
            title="助手",
            title_align="left"
        ))

    def _print_system(self, text: str, style: str = "dim yellow"):
        console.print(Text(text, style=style))

    def _run_pipeline_thread(self, question: str, result_queue: Queue):
        try:
            p = Pipeline(mode=self.mode, callback=None, verbose=False)
            result = p.run(question, history=self.history)
            result_queue.put(("ok", result))
        except Exception as e:
            import traceback
            result_queue.put(("error", f"{e}\n{traceback.format_exc()}"))

    def _thinking_label(self) -> str:
        labels = {
            "full": "● full  Planner → Coder → Critic → Validator → Repairer",
            "fast": "● fast  DeepSeek Coder → DeepSeek Repairer",
            "ultra": "● ultra DeepSeek CoT",
        }
        return labels.get(self.mode, f"● {self.mode}")

    def _process_input(self, text: str) -> bool:
        text = text.strip()
        if not text:
            return True

        if text == "/quit" or text == "/exit":
            return False

        if text == "/clear":
            console.clear()
            self.history.clear()
            self._print_system("屏幕与历史已清空。", style="dim")
            return True

        if text.startswith("/mode"):
            parts = text.split(maxsplit=1)
            if len(parts) == 2 and parts[1] in ("full", "fast", "ultra"):
                self.mode = parts[1]
                self._print_system(f"模式切换为: {self.mode}", style="bold green")
            else:
                self._print_system("用法: /mode full|fast|ultra", style="yellow")
            return True

        if text == "/history":
            self._print_system(f"当前历史轮数: {len(self.history) // 2}", style="dim")
            return True

        if text.startswith("/"):
            self._print_system(f"未知命令: {text}", style="yellow")
            return True

        # 正常问题
        self.history.append({"role": "user", "content": text})
        self._print_user(text)

        result_queue = Queue()
        thread = threading.Thread(
            target=self._run_pipeline_thread,
            args=(text, result_queue),
            daemon=True,
        )

        with console.status(
            f"[bold green]{self._thinking_label()}[/]  正在思考...",
            spinner="dots",
            refresh_per_second=10,
        ):
            thread.start()
            thread.join()

        status, payload = result_queue.get()

        if status == "error":
            self._print_system(f"❌ {payload}", style="red")
            return True

        result = payload
        if result.error:
            self._print_system(f"❌ {result.error} (step: {result.step})", style="red")
            return True

        self._print_ai(result.code)
        self.history.append({"role": "assistant", "content": result.code})
        console.print(Text(f"✓ 完成 · {result.mode} · {result.elapsed}s", style="dim green"))
        console.print()
        return True

    def run(self):
        console.print(Rule("[bold cyan]Kimi-DeepSeek 终端聊天[/]", style="cyan"))
        console.print(
            "[dim]输入问题生成代码 · /mode full|fast|ultra · /clear · /quit[/dim]\n"
        )

        while True:
            try:
                question = console.input("[bold cyan]> [/]")
            except (EOFError, KeyboardInterrupt):
                console.print()
                break

            if not self._process_input(question):
                break

        console.print("[dim]再见。[/dim]")


if __name__ == "__main__":
    missing = pipeline.check_missing_keys()
    if missing:
        console.print(f"[red]缺少环境变量: {', '.join(missing)}[/red]")
        console.print("[dim]请检查 .env 文件或环境变量。[/dim]")
        sys.exit(2)

    TerminalChat().run()
