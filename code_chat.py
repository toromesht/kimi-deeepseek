#!/usr/bin/env python3
"""
Kimi-DeepSeek 终端聊天（类 Claude Code 内联界面）

用法:
  python code_chat.py
  kchat

命令:
  /quit                退出
  /clear               清空屏幕和对话历史
  /mode full           切换模式: full / fast / ultra
  /history             显示当前历史轮数
  /save <file>         保存上一次完整回复到文件
  /savecode <file>     保存上一次代码块到文件
  /run                 运行上一次代码块（Python，需确认）
  /help                显示帮助
"""
import sys
import os
import re
import subprocess
import tempfile
import threading
import time
from queue import Queue, Empty
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rich.console import Console
from rich.syntax import Syntax
from rich.panel import Panel
from rich.text import Text
from rich.rule import Rule
from rich.live import Live
from rich.table import Table

import pipeline
from pipeline import Pipeline


console = Console()


STEP_INFO = {
    "planner": ("Planner", "架构与提示词"),
    "coder": ("Coder", "生成完整代码"),
    "critic": ("Critic", "批判性审查"),
    "validator": ("Validator", "静态 + LLM 验证"),
    "repairer": ("Repairer", "修复输出终版"),
    "ultra": ("Ultra", "单模型强输出"),
}


def extract_last_code_block(text: str) -> str:
    """提取文本中最后一个 ```...``` 代码块。"""
    blocks = re.findall(r"```(?:\w*:?[^\n]*)?\n(.*?)```", text, re.DOTALL)
    if blocks:
        return blocks[-1].strip()
    m = re.search(r"<code>\s*(.*?)\s*</code>", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text.strip()


class TerminalChat:
    """内联终端聊天：消息滚动、底部输入、显示每个模块运转状态。"""

    def __init__(self):
        self.history = []
        self.mode = pipeline.Config.PIPELINE_MODE
        if self.mode not in ("full", "fast", "ultra"):
            self.mode = "full"
        self.last_response: str = ""
        self.last_code: str = ""

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

    def _print_hint(self):
        console.print(
            Text("提示: /save <文件> 保存回复  /savecode <文件> 保存代码块  /run 运行代码块  /help 帮助",
                 style="dim")
        )

    def _step_order(self) -> list:
        if self.mode == "ultra":
            return ["ultra"]
        if self.mode == "fast":
            return ["coder", "repairer"]
        return ["planner", "coder", "critic", "validator", "repairer"]

    def _make_progress_table(self, step_status: dict, elapsed: float = 0.0) -> Table:
        table = Table(title=f"● {self.mode.upper()} 模式  正在运转...  {elapsed:.1f}s", expand=True)
        table.add_column("状态", style="yellow", no_wrap=True)
        table.add_column("模块", style="cyan", no_wrap=True)
        table.add_column("任务", style="green")
        table.add_column("模型 / Token", style="dim")

        for key in self._step_order():
            name, task = STEP_INFO[key]
            st = step_status.get(key, {})
            if st.get("status") == "start":
                state = "[bold yellow]⏳ 运行中[/bold yellow]"
            elif st.get("status") == "done":
                if st.get("data", {}).get("skipped"):
                    state = "[dim]⏭ 跳过[/dim]"
                else:
                    state = "[bold green]✅ 完成[/bold green]"
            elif st.get("status") == "fail":
                state = "[bold red]❌ 失败[/bold red]"
            else:
                state = "[dim]⏸ 等待[/dim]"

            info = ""
            if st.get("status") == "done" and not st.get("data", {}).get("skipped"):
                d = st.get("data", {})
                info = f"{d.get('model', '')} · {d.get('tokens', '?')} tok"

            table.add_row(state, name, task, info)
        return table

    def _run_pipeline_thread(self, question: str, update_queue: Queue, result_queue: Queue):
        def callback(step, status, data):
            update_queue.put((step, status, data))

        try:
            p = Pipeline(mode=self.mode, callback=callback, verbose=False)
            result = p.run(question, history=self.history)
            result_queue.put(("ok", result))
        except Exception as e:
            import traceback
            result_queue.put(("error", f"{e}\n{traceback.format_exc()}"))

    def _process_input(self, text: str) -> bool:
        text = text.strip()
        if not text:
            return True

        if text == "/quit" or text == "/exit":
            return False

        if text == "/clear":
            console.clear()
            self.history.clear()
            self.last_response = ""
            self.last_code = ""
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

        if text.startswith("/savecode"):
            self._cmd_save(text, save_code=True)
            return True

        if text.startswith("/save"):
            self._cmd_save(text, save_code=False)
            return True

        if text == "/run":
            self._cmd_run()
            return True

        if text == "/help":
            self._print_help()
            return True

        if text.startswith("/"):
            self._print_system(f"未知命令: {text}", style="yellow")
            return True

        # 正常问题
        self.history.append({"role": "user", "content": text})
        self._print_user(text)

        update_queue = Queue()
        result_queue = Queue()
        thread = threading.Thread(
            target=self._run_pipeline_thread,
            args=(text, update_queue, result_queue),
            daemon=True,
        )

        step_status = {}
        start = time.time()
        thread.start()

        with Live(self._make_progress_table(step_status), console=console, refresh_per_second=8) as live:
            while thread.is_alive() or not update_queue.empty():
                try:
                    step, status, data = update_queue.get(timeout=0.1)
                    step_status[step] = {"status": status, "data": data}
                    live.update(self._make_progress_table(step_status, time.time() - start))
                except Empty:
                    live.update(self._make_progress_table(step_status, time.time() - start))

        status, payload = result_queue.get()

        if status == "error":
            self._print_system(f"❌ {payload}", style="red")
            return True

        result = payload
        if result.error:
            self._print_system(f"❌ {result.error} (step: {result.step})", style="red")
            return True

        self.last_response = result.code
        self.last_code = extract_last_code_block(result.code)
        self._print_ai(result.code)
        self.history.append({"role": "assistant", "content": result.code})
        console.print(Text(f"✓ 完成 · {result.mode} · {result.elapsed}s", style="dim green"))
        self._print_hint()
        console.print()
        return True

    def _cmd_save(self, text: str, save_code: bool = False):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            self._print_system("用法: /save <文件路径>", style="yellow")
            return
        path = parts[1].strip()
        content = self.last_code if save_code else self.last_response
        if not content:
            self._print_system("还没有可保存的内容。先问一个问题生成代码。", style="yellow")
            return
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            self._print_system(f"✓ 已保存到 {path} ({len(content)} 字符)", style="green")
        except Exception as e:
            self._print_system(f"❌ 保存失败: {e}", style="red")

    def _cmd_run(self):
        if not self.last_code:
            self._print_system("还没有可运行的代码块。先问一个问题生成代码。", style="yellow")
            return

        self._print_system("即将运行上一次代码块。请确认是否继续？(y/n)", style="yellow")
        try:
            confirm = console.input("[bold yellow]> [/]").strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return

        if confirm not in ("y", "yes", "是", "确认"):
            self._print_system("已取消运行。", style="dim")
            return

        fd, tmp_path = tempfile.mkstemp(suffix=".py", prefix="kchat_run_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(self.last_code)
            self._print_system(f"运行临时文件: {tmp_path}", style="dim")
            result = subprocess.run(
                [sys.executable, tmp_path],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.stdout:
                console.print(Panel(result.stdout, title="输出", border_style="blue"))
            if result.stderr:
                console.print(Panel(result.stderr, title="错误", border_style="red"))
            if result.returncode == 0:
                self._print_system("✓ 运行完成", style="green")
            else:
                self._print_system(f"❌ 运行失败，退出码 {result.returncode}", style="red")
        except subprocess.TimeoutExpired:
            self._print_system("❌ 运行超时（60s）", style="red")
        except Exception as e:
            self._print_system(f"❌ 运行出错: {e}", style="red")
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass

    def _print_help(self):
        help_text = """
可用命令:
  /quit              退出
  /clear             清空屏幕和对话历史
  /mode full|fast|ultra   切换流水线模式
  /history           显示当前历史轮数
  /save <文件>       保存上一次完整回复到文件
  /savecode <文件>   保存上一次代码块到文件
  /run               运行上一次代码块（Python，需确认）
  /help              显示本帮助

说明:
  - kchat 生成代码后不会自动运行。
  - 用 /savecode 把代码保存为 .py 文件，再手动运行；
  - 或用 /run 直接运行（仅建议运行你信任的代码）。
"""
        console.print(Panel(help_text, title="帮助", border_style="cyan"))

    def run(self):
        console.print(Rule("[bold cyan]Kimi-DeepSeek 终端聊天[/]", style="cyan"))
        console.print(
            "[dim]输入问题生成代码 · /mode full|fast|ultra · /clear · /quit · /help[/dim]\n"
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
