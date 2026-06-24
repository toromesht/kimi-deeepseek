#!/usr/bin/env python3
"""
Kimi-DeepSeek 多模型代码流水线 —— 终端页面版

用法:
  python code_tui.py
  python code_tui.py "你的问题"
"""
import sys
import os
import time
import threading
import traceback

# 把脚本所在目录加入路径，以便 import pipeline
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rich.console import Console
from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.syntax import Syntax
from rich.align import Align

import pipeline

console = Console()

STEP_NAMES = {
    "planner": ("Kimi", "架构与提示词"),
    "coder": ("DeepSeek", "生成完整代码"),
    "critic": ("KimiCode", "批判性审查"),
    "validator": ("DeepSeek", "静态 + LLM 验证"),
    "repairer": ("DeepSeek", "修复输出终版"),
    "ultra": ("DeepSeek", "单模型强输出"),
}


def make_layout(question=""):
    layout = Layout(name="root")
    layout.split(
        Layout(name="header", size=3),
        Layout(name="main"),
        Layout(name="footer", size=3),
    )
    layout["header"].update(Panel(
        Align.left(f"[bold cyan]Kimi-DeepSeek 代码流水线[/bold cyan]  [dim]{question[:80]}{'...' if len(question) > 80 else ''}[/dim]"),
        border_style="cyan"
    ))
    layout["main"].update(make_progress_table())
    layout["footer"].update(Panel("[dim]等待开始...[/dim]", border_style="dim"))
    return layout


def make_progress_table(status=None):
    status = status or {}
    table = Table(title="流水线进度", expand=True)
    table.add_column("步骤", style="cyan", no_wrap=True)
    table.add_column("模型", style="magenta")
    table.add_column("任务", style="green")
    table.add_column("状态", style="yellow")
    table.add_column("Token / 长度", justify="right", style="dim")

    for key in STEP_NAMES:
        model, task = STEP_NAMES[key]
        st = status.get(key, {})
        if st.get("status") == "start":
            state = "[bold yellow]⏳ 运行中...[/bold yellow]"
        elif st.get("status") == "done":
            state = "[bold green]✅ 完成[/bold green]"
        elif st.get("status") == "fail":
            state = f"[bold red]❌ 失败: {st.get('data', {}).get('error', '')[:30]}[/bold red]"
        else:
            state = "[dim]⏸ 等待[/dim]"
        info = ""
        if st.get("status") == "done":
            d = st.get("data", {})
            info = f"{d.get('tokens', '?')} tok / {d.get('len', 0)} chars"
        table.add_row(str(i), model, task, state, info)
    return table


def main():
    question = " ".join(sys.argv[1:]).strip() if len(sys.argv) > 1 else ""
    if not question:
        console.print("[bold]请输入要生成的代码问题:[/bold]", end=" ")
        question = input().strip()
    if not question:
        console.print("[red]问题为空，退出。[/red]")
        sys.exit(1)

    # 检查 key
    missing = []
    for k, svc in [("KIMI_KEY", "kimi"), ("KIMI_CODE_KEY", "kimi_code"), ("DEEPSEEK_KEY", "deepseek")]:
        if not pipeline.CFG[svc]["key"]:
            missing.append(k)
    if missing:
        console.print(f"[red]缺少环境变量: {', '.join(missing)}[/red]")
        console.print("[dim]请检查 .env 文件或环境变量。[/dim]")
        sys.exit(2)

    status = {}
    result = {"value": None}
    start = time.time()

    def callback(step, state, data):
        status[step] = {"status": state, "data": data}

    def worker():
        try:
            result["value"] = pipeline.run(question, callback=callback, verbose=False)
        except Exception as e:
            result["error"] = f"{e}\n\n{traceback.format_exc()}"

    layout = make_layout(question)
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    with Live(layout, console=console, refresh_per_second=4, screen=False) as live:
        while thread.is_alive():
            elapsed = time.time() - start
            layout["main"].update(make_progress_table(status))
            layout["footer"].update(Panel(
                f"[dim]已运行 {elapsed:.1f}s  |  输出目录: {os.path.abspath('outputs')}[/dim]",
                border_style="dim"
            ))
            time.sleep(0.1)

        elapsed = time.time() - start
        layout["main"].update(make_progress_table(status))

        if result.get("value"):
            res = result["value"]
            if "error" in res:
                # pipeline 返回了失败信息
                step = res.get("step", "?")
                err = res["error"]
                layout["main"].update(Panel(f"[red]Step {step} 失败:\n{err}[/red]", title="错误", border_style="red"))
                layout["footer"].update(Panel("[red]运行失败[/red]", border_style="red"))
            else:
                code = res.get("code", "")
                # 展示最终代码
                syntax = Syntax(code, "python", theme="monokai", line_numbers=True, word_wrap=True)
                layout["main"].update(Panel(syntax, title="最终代码", border_style="green"))
                paths = "\n".join(f"[dim]{p}[/dim]" for p in res.get("paths", []))
                layout["footer"].update(Panel(
                    f"[bold green]✅ 完成[/bold green]  耗时 {res.get('elapsed', round(elapsed, 1))}s\n"
                    f"[dim]中间文件:[/dim]\n{paths}",
                    border_style="green"
                ))
        else:
            err = result.get("error", "未知错误")
            layout["main"].update(Panel(f"[red]{err}[/red]", title="错误", border_style="red"))
            layout["footer"].update(Panel("[red]运行失败[/red]", border_style="red"))

    # 最后再打印一次最终代码，方便复制
    if result.get("value"):
        console.print("\n[bold]===== FINAL CODE =====[/bold]\n")
        console.print(result["value"]["code"])


if __name__ == "__main__":
    main()
