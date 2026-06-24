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

自动保存:
  检测到“输出 html 文件”“保存到桌面”“生成 xxx.py”等意图时，
  自动把内容写入桌面（或指定路径），无需确认；已存在文件自动重命名。
"""
import sys
import os
import re
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from queue import Queue, Empty
from pathlib import Path, PureWindowsPath
from typing import Optional, Tuple

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
        self.auto_save_dir: Path = Path.home() / "Desktop"
        self.last_saved_path: Optional[Path] = None
        self.intent_save_path: Optional[Path] = None  # 本轮检测到的保存意图路径

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

    # --------------------------------------------------------
    # 自动保存：意图检测 + 安全落盘
    # --------------------------------------------------------
    def _detect_file_intent(self, text: str) -> Tuple[bool, Optional[Path], bool]:
        """
        检测用户是否希望直接把输出写成文件。
        返回: (should_auto_save, suggested_path, save_code_only)
        """
        text_lower = text.lower()

        # 1. 文件/保存意图关键词
        intent_patterns = [
            r"输出\s*html",
            r"输出\s*html\s*文件",
            r"生成\s*html",
            r"保存\s*html",
            r"html\s*文件",
            r"输出\s*文件",
            r"生成\s*文件",
            r"保存\s*文件",
            r"保存到",
            r"保存为",
            r"输出到",
            r"写入",
            r"落盘",
            r"生成\s*报告",
            r"导出",
        ]
        has_intent = any(re.search(p, text_lower) for p in intent_patterns)
        if not has_intent:
            return False, None, False

        # 2. 尝试提取显式路径或文件名
        # 先匹配完整 Windows/Linux 路径
        path_candidates = []
        # 完整路径：C:\... 或 /home/... 或 \\server\...
        for m in re.finditer(r'(?:[A-Za-z]:[\\/]|/|\\\\)[^\s"\'<>|*?]*\.[A-Za-z0-9]+', text):
            path_candidates.append(m.group(0))
        # 单独文件名：xxx.html, xxx.py 等
        for m in re.finditer(r'\b[A-Za-z0-9_\-]+\.(html?|py|js|css|json|md|txt|csv|yaml|yml|go|rs|java|cpp|c|h)\b', text_lower):
            path_candidates.append(m.group(0))

        # 3. 推断扩展名
        ext = ".html" if re.search(r"html?\s*文件|输出\s*html|生成\s*html|保存\s*html", text_lower) else None
        if not ext and re.search(r"python\s*文件|py\s*文件|\.py", text_lower):
            ext = ".py"
        if not ext and re.search(r"json\s*文件|\.json", text_lower):
            ext = ".json"
        if not ext and re.search(r"markdown|md\s*文件|\.md", text_lower):
            ext = ".md"
        if not ext and re.search(r"csv\s*文件|\.csv", text_lower):
            ext = ".csv"

        # 4. 解析候选路径
        chosen_path = None
        for candidate in path_candidates:
            try:
                p = Path(candidate)
                # 如果只有文件名，会解析为相对路径（无盘符）
                if p.is_absolute():
                    chosen_path = p
                    break
                else:
                    # 单独文件名：先记录，后面拼到默认目录
                    if not chosen_path:
                        chosen_path = self.auto_save_dir / p.name
            except Exception:
                continue

        if chosen_path is None:
            # 没有时间戳的默认名，交给 _resolve_save_path 处理
            chosen_path = self.auto_save_dir / (f"kchat_output{ext or ''}")

        # 5. 是否只保存代码块（默认代码意图保存代码块；html 文件意图保存整个 html）
        save_code_only = bool(re.search(r"代码|code|\.py|python", text_lower)) and not re.search(r"html?\s*文件|输出\s*html", text_lower)

        return True, chosen_path, save_code_only

    def _is_safe_path(self, path: Path) -> bool:
        """限制可写范围，防止误写系统目录。"""
        try:
            resolved = path.resolve()
        except Exception:
            return False

        # 1. 明确禁止的系统目录前缀（Windows / 类 Unix 都覆盖）
        forbidden_prefixes = [
            r"C:\Windows",
            r"C:\Program Files",
            r"C:\Program Files (x86)",
            r"C:\ProgramData",
            r"C:\System Volume Information",
            "/etc",
            "/usr/bin",
            "/bin",
            "/sbin",
            "/sys",
            "/proc",
            "/dev",
        ]
        path_str = str(resolved)
        for prefix in forbidden_prefixes:
            pl = prefix.lower()
            ps = path_str.lower()
            if ps == pl or ps.startswith(pl + os.sep) or ps.startswith(pl + "/"):
                return False

        # 2. 允许写入的根目录白名单
        allowed_roots = [
            Path.home().resolve(),                    # C:\Users\ShortPome
            (Path.home() / "Desktop").resolve(),      # 桌面
            Path(__file__).resolve().parent,          # 项目目录
            Path(tempfile.gettempdir()).resolve(),    # 临时目录
        ]

        for allowed in allowed_roots:
            try:
                if resolved == allowed or allowed in resolved.parents:
                    return True
            except Exception:
                continue

        # 不在白名单里的路径：拒绝自动写入
        return False

    def _resolve_save_path(self, raw_path: Path, default_ext: str = ".html") -> Path:
        """把路径规范化到安全区域，必要时重命名避免覆盖。"""
        # 补扩展名
        if not raw_path.suffix:
            raw_path = raw_path.with_suffix(default_ext)

        # 如果只有文件名，放到默认目录
        if not raw_path.is_absolute():
            raw_path = self.auto_save_dir / raw_path.name

        # 安全检查不通过则迁到桌面
        if not self._is_safe_path(raw_path):
            raw_path = self.auto_save_dir / raw_path.name

        # 目录不存在则创建
        raw_path.parent.mkdir(parents=True, exist_ok=True)

        # 如果文件已存在，自动加时间戳后缀，避免覆盖
        if raw_path.exists():
            stem = raw_path.stem
            suffix = raw_path.suffix
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            raw_path = raw_path.parent / f"{stem}_{timestamp}{suffix}"

        return raw_path

    def _extract_save_content(self, full_response: str, save_code_only: bool) -> Tuple[str, str]:
        """决定保存什么内容，以及保存时使用的语言/类型标签。"""
        # 如果用户明确要代码块，且能提取到，优先保存代码块
        code = extract_last_code_block(full_response)
        if save_code_only and code and code != full_response.strip():
            return code, "code block"

        # 如果整个回复看起来就是 HTML，保存完整回复
        stripped = full_response.strip()
        if stripped.startswith(("<!DOCTYPE", "<!doctype", "<html", "<HTML")):
            return stripped, "html"

        # 如果回复里有代码块，保存最后一个代码块（最常见情况）
        if code and code != full_response.strip():
            return code, "code block"

        # 兜底：保存完整回复
        return full_response, "full response"

    def _auto_save(self, question: str, response: str):
        """根据用户意图自动保存文件。"""
        should_save, raw_path, save_code_only = self._detect_file_intent(question)
        if not should_save or not raw_path:
            return

        content, kind = self._extract_save_content(response, save_code_only)
        if not content.strip():
            self._print_system("[WARN] 检测到保存意图，但回复内容为空，未保存。", style="yellow")
            return

        # 根据内容进一步修正扩展名
        ext = raw_path.suffix
        if not ext or ext == ".html" and not content.strip().startswith(("<", "<!DOCTYPE")):
            # 根据内容推断
            if content.strip().startswith(("{", "[")):
                ext = ".json"
            elif "import " in content or "def " in content or "class " in content:
                ext = ".py"
            elif content.strip().startswith("#") or "## " in content[:200]:
                ext = ".md"
            else:
                ext = ".html" if "<html" in content.lower() or "<!doctype" in content.lower() else ".txt"
            raw_path = raw_path.with_suffix(ext)

        final_path = self._resolve_save_path(raw_path)
        try:
            with open(final_path, "w", encoding="utf-8") as f:
                f.write(content)
            self.last_saved_path = final_path
            self._print_system(
                f"[OK] 已自动保存 [{kind}] -> {final_path} ({len(content)} 字符)",
                style="bold green"
            )
        except Exception as e:
            self._print_system(f"[ERR] 自动保存失败: {e}", style="red")

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

        # 检测文件输出意图，并把格式要求注入给 pipeline
        should_save, raw_path, _ = self._detect_file_intent(text)
        pipeline_question = text
        if should_save and raw_path:
            suffix = raw_path.suffix.lower()
            if suffix in (".html", ".htm"):
                pipeline_question += (
                    "\n\n[系统提示：请直接输出完整的 HTML 文件内容，"
                    "必须是可直接保存为 .html 的纯 HTML，不要输出用于生成 HTML 的脚本或说明。]"
                )
            elif suffix == ".py":
                pipeline_question += (
                    "\n\n[系统提示：请输出完整可运行的 Python 代码，"
                    "可直接保存为 .py 文件执行。]"
                )
            elif suffix == ".json":
                pipeline_question += (
                    "\n\n[系统提示：请直接输出合法 JSON 内容，可直接保存为 .json 文件。]"
                )
            elif suffix in (".md", ".markdown"):
                pipeline_question += (
                    "\n\n[系统提示：请直接输出 Markdown 内容，可直接保存为 .md 文件。]"
                )

        update_queue = Queue()
        result_queue = Queue()
        thread = threading.Thread(
            target=self._run_pipeline_thread,
            args=(pipeline_question, update_queue, result_queue),
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

        # 自动保存：检测意图并落盘（无需确认）
        self._auto_save(text, result.code)

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

自动保存（类 Claude Code Artifacts）:
  - 只要你的问题包含“输出 html 文件”“保存到桌面”“生成 report.py”等意图，
    kchat 会自动把生成的内容写到桌面，无需手动 /save。
  - 可写范围限制在：桌面、用户目录、项目目录、临时目录。
  - 若目标文件已存在，会自动加时间戳后缀，避免覆盖。

说明:
  - kchat 生成代码后不会自动运行。
  - 用 /savecode 把代码保存为 .py 文件，再手动运行；
  - 或用 /run 直接运行（仅建议运行你信任的代码）。
"""
        console.print(Panel(help_text, title="帮助", border_style="cyan"))

    def run(self):
        console.print(Rule("[bold cyan]Kimi-DeepSeek 终端聊天[/]", style="cyan"))
        console.print(
            "[dim]输入问题生成代码 · 自动保存：说“输出html文件/保存到桌面”即可落盘 · /help[/dim]\n"
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
