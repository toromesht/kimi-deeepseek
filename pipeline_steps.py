#!/usr/bin/env python3
"""
Kimi-DeepSeek 流水线步骤：Planner / Coder / Critic / Validator / Repairer。
"""
import ast
import re
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable, Tuple

from pipeline_core import (
    Config, Router, ContextManager,
    save_round, notify, estimate_tokens,
)


# ------------------------------------------------------------
# 结构化结果
# ------------------------------------------------------------
@dataclass
class PipelineResult:
    code: str = ""
    review: str = ""
    validation: str = ""
    elapsed: float = 0.0
    paths: List[str] = field(default_factory=list)
    mode: str = "full"
    error: Optional[str] = None
    step: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "review": self.review,
            "validation": self.validation,
            "elapsed": self.elapsed,
            "paths": self.paths,
            "mode": self.mode,
            "error": self.error,
            "step": self.step,
        }


# ------------------------------------------------------------
# Prompt 模板
# ------------------------------------------------------------
class Prompts:
    PLANNER_SYSTEM = """你是资深 Prompt 工程师与软件架构师。
请根据用户问题、历史对话和上下文，产出一份结构化的《实现方案》。
要求：
1. 先做任务分析与技术选型；
2. 明确输入输出、边界条件、错误处理、测试要点；
3. 给出模块划分、函数签名、数据流；
4. 最后写一段给编码 AI 的详细 Prompt。
输出必须使用以下 XML 标签结构（不要省略任何标签）：
<analysis>任务分析</analysis>
<requirements>功能与非功能需求</requirements>
<architecture>模块划分与函数签名</architecture>
<plan>实施步骤 1/2/3...</plan>
<prompt_for_coder>给编码 AI 的详细 Prompt</prompt_for_coder>"""

    CODER_SYSTEM = """你是世界级编程专家，擅长写出可直接运行的生产级代码。
请先写简短推理 <reasoning>，再输出代码 <code>。
要求：
1. 代码结构清晰、注释适度、错误处理完善；
2. 多文件时用 ```filename:相对路径 标记；
3. 如需求含测试，给出测试用例；
4. 不要输出与代码无关的寒暄。
输出格式：
<reasoning>你的思考过程</reasoning>
<code>
完整代码
</code>"""

    CRITIC_SYSTEM = """你是严格、挑剔且具备批判性思维的代码审查员。
请审查用户提供的代码是否满足原始需求，并输出：
<issues>
- [severity] 问题描述 + 修复建议
</issues>
<fixed_code>
修复后的完整代码（不要只给 diff）
</fixed_code>
注意：
- 如果没有问题，issues 列表为空，fixed_code 仍输出原代码；
- 必须保证 fixed_code 是完整可运行的。"""

    VALIDATOR_SYSTEM = """你是代码功能验证专家。请根据原始需求，评估下面代码是否存在功能性缺陷、边界错误或需求遗漏。
输出格式：
<validation>
- [PASS/FAIL] 检查项：说明
</validation>
<issues_found>
- 问题描述
</issues_found>"""

    REPAIRER_SYSTEM = """你是代码修复专家。请综合原始需求、代码审查意见和验证报告，输出最终版完整代码。
要求：
1. 逐条判断审查意见，接受的修改写入最终代码，驳回的给出理由；
2. 修复验证报告中发现的功能性缺陷；
3. 输出最终可运行代码。
输出格式：
<accepted>接受的修改</accepted>
<rejected>驳回的修改及理由</rejected>
<reasoning>综合判断</reasoning>
<code>
最终完整代码
</code>"""

    ULTRA_SYSTEM = """你是世界级编程专家。请一次性完成需求分析、方案设计、代码编写和自我审查，输出高质量可运行代码。
请按以下结构输出：
<analysis>分析</analysis>
<plan>方案</plan>
<reasoning>编码思路</reasoning>
<self_review>自我审查与修复点</self_review>
<code>
最终完整代码
</code>"""


# ------------------------------------------------------------
# 代码提取
# ------------------------------------------------------------
def extract_code(text: str) -> str:
    """从模型输出中提取 <code> 标签或最后一个代码块里的代码。"""
    # 优先 <code>
    m = re.search(r"<code>\s*(.*?)\s*</code>", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # 其次 ```...``` 代码块
    blocks = re.findall(r"```(?:\w*:?[^\n]*)?\n(.*?)```", text, re.DOTALL)
    if blocks:
        return blocks[-1].strip()
    return text.strip()


# ------------------------------------------------------------
# 步骤基类
# ------------------------------------------------------------
class Step:
    def __init__(self, router: Router, callback: Optional[Callable] = None, verbose: bool = True):
        self.router = router
        self.callback = callback
        self.verbose = verbose
        self.paths: List[str] = []

    def _log(self, msg: str):
        if self.verbose:
            print(msg)

    def _save(self, step: str, text: str) -> str:
        path = save_round(step, text)
        self.paths.append(path)
        return path

    def _notify(self, step: str, status: str, data: Dict[str, Any]):
        notify(self.callback, step, status, data)

    def _ok(self, step: str, name: str, svc: str, text: str, usage: Dict[str, Any]) -> Dict[str, Any]:
        self._save(step, text)
        self._notify(step, "done", {
            "name": name,
            "model": svc,
            "tokens": usage.get("completion_tokens", "?"),
            "len": len(text),
            "path": self.paths[-1],
        })
        return {"text": text, "service": svc, "usage": usage}

    def _fail(self, step: str, name: str, error: str) -> Dict[str, Any]:
        self._notify(step, "fail", {"error": error})
        self._log(f"FAIL {step}: {error}")
        return {"error": f"Step {step} ({name}) failed: {error}"}


# ------------------------------------------------------------
# Planner
# ------------------------------------------------------------
class PlannerStep(Step):
    def run(self, ctx: ContextManager) -> Dict[str, Any]:
        step_name = "planner"
        self._notify(step_name, "start", {"name": "Planner", "role": "架构/提示词"})
        system_ctx, user_ctx = ctx.view_for("kimi")
        user = f"{user_ctx}\n\n请输出结构化实现方案。"

        svc, text, usage = self.router.call_with_fallback(
            "planner", Prompts.PLANNER_SYSTEM, user,
            max_tokens=4096, preferred="kimi"
        )
        if not text:
            return self._fail(step_name, "Planner", usage.get("error", "unknown"))

        self._log(f"\n--- STEP planner [{svc}] [{len(text)}c, {usage.get('completion_tokens', '?')}t] ---\n{text[:500]}{'...' if len(text) > 500 else ''}")
        return self._ok(step_name, "Planner", svc, text, usage)


# ------------------------------------------------------------
# Coder
# ------------------------------------------------------------
class CoderStep(Step):
    def run(self, ctx: ContextManager, plan: str) -> Dict[str, Any]:
        step_name = "coder"
        self._notify(step_name, "start", {"name": "Coder", "role": "生成代码"})
        full_ctx = ctx.full_context_for_deepseek()
        user = (
            f"{full_ctx}\n\n"
            f"<implementation_plan>\n{plan}\n</implementation_plan>\n\n"
            "请根据以上需求和实现方案，输出完整可运行代码。"
        )
        # 始终优先 DeepSeek，失败才回退
        svc, text, usage = self.router.call_with_fallback(
            "coder", Prompts.CODER_SYSTEM, user,
            max_tokens=16384, preferred="deepseek"
        )
        if not text:
            return self._fail(step_name, "Coder", usage.get("error", "unknown"))

        self._log(f"\n--- STEP coder [{svc}] [{len(text)}c, {usage.get('completion_tokens', '?')}t] ---\n{text[:500]}{'...' if len(text) > 500 else ''}")
        return self._ok(step_name, "Coder", svc, text, usage)


# ------------------------------------------------------------
# Critic
# ------------------------------------------------------------
class CriticStep(Step):
    def run(self, ctx: ContextManager, code: str) -> Dict[str, Any]:
        step_name = "critic"
        self._notify(step_name, "start", {"name": "Critic", "role": "批判性审查"})
        system_ctx, user_ctx = ctx.view_for("kimi_code")
        user = (
            f"{user_ctx}\n\n"
            f"<code_to_review>\n{code}\n</code_to_review>\n\n"
            "请审查以上代码，列出所有问题并给出修复后的完整代码。"
        )
        svc, text, usage = self.router.call_with_fallback(
            "critic", Prompts.CRITIC_SYSTEM, user,
            max_tokens=8192, preferred="kimi_code"
        )
        if not text:
            return self._fail(step_name, "Critic", usage.get("error", "unknown"))

        self._log(f"\n--- STEP critic [{svc}] [{len(text)}c, {usage.get('completion_tokens', '?')}t] ---\n{text[:500]}{'...' if len(text) > 500 else ''}")
        return self._ok(step_name, "Critic", svc, text, usage)


# ------------------------------------------------------------
# Validator
# ------------------------------------------------------------
class StaticValidator:
    """静态代码验证：Python 语法、imports、placeholder。"""

    def validate(self, code: str) -> Tuple[bool, str]:
        code_clean = extract_code(code)
        issues = []

        # 检查是否有明显占位符
        placeholders = re.findall(r"(TODO|FIXME|XXX|placeholder|请实现|待实现)", code_clean, re.IGNORECASE)
        if placeholders:
            issues.append(f"发现占位符/未完成标记: {set(placeholders)}")

        # Python 语法检查
        if self._looks_like_python(code_clean):
            try:
                ast.parse(code_clean)
                issues.append("Python 语法检查通过")
            except SyntaxError as e:
                issues.append(f"Python 语法错误: {e}")

        # 提取 imports
        imports = re.findall(r"^(?:import|from)\s+([\w.]+)", code_clean, re.MULTILINE)
        if imports:
            issues.append(f"使用到的导入: {', '.join(sorted(set(imports)))}")

        ok = not any("错误" in i or "占位符" in i for i in issues)
        return ok, "\n".join(f"- {i}" for i in issues)

    @staticmethod
    def _looks_like_python(code: str) -> bool:
        python_markers = ["def ", "import ", "from ", "class ", "print(", "if __name__"]
        return any(m in code for m in python_markers)


class ValidatorStep(Step):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.static = StaticValidator()

    def run(self, ctx: ContextManager, code: str) -> Dict[str, Any]:
        step_name = "validator"
        self._notify(step_name, "start", {"name": "Validator", "role": "验证"})

        ok, static_report = self.static.validate(code)
        self._log(f"\n--- STEP validator [static] ok={ok} ---\n{static_report}")

        # LLM 功能验证
        full_ctx = ctx.full_context_for_deepseek()
        llm_report = ""
        svc, text, usage = self.router.call_with_fallback(
            "validator", Prompts.VALIDATOR_SYSTEM,
            f"{full_ctx}\n\n<code_to_validate>\n{code}\n</code_to_validate>\n\n请评估代码是否满足需求。",
            max_tokens=4096, preferred="deepseek"
        )
        if text:
            llm_report = text
            self._log(f"\n--- STEP validator [llm] [{svc}] ---\n{text[:500]}{'...' if len(text) > 500 else ''}")
        else:
            llm_report = f"LLM 验证失败: {usage.get('error', 'unknown')}"

        report = f"静态检查:\n{static_report}\n\nLLM 验证:\n{llm_report}"
        self._save("validator", report)
        self._notify(step_name, "done", {
            "name": "Validator",
            "model": svc if text else "static",
            "tokens": usage.get("completion_tokens", "?") if text else "?",
            "len": len(report),
            "path": self.paths[-1] if self.paths else "",
            "static_ok": ok,
        })
        return {"report": report, "static_ok": ok, "llm": llm_report}


# ------------------------------------------------------------
# Repairer
# ------------------------------------------------------------
class RepairerStep(Step):
    def run(self, ctx: ContextManager, code: str, review: str, validation: str) -> Dict[str, Any]:
        step_name = "repairer"
        self._notify(step_name, "start", {"name": "Repairer", "role": "修复/终版"})
        full_ctx = ctx.full_context_for_deepseek()
        user = (
            f"{full_ctx}\n\n"
            f"<original_code>\n{code}\n</original_code>\n\n"
            f"<code_review>\n{review}\n</code_review>\n\n"
            f"<validation_report>\n{validation}\n</validation_report>\n\n"
            "请综合以上信息，输出最终版完整代码。"
        )
        svc, text, usage = self.router.call_with_fallback(
            "repairer", Prompts.REPAIRER_SYSTEM, user,
            max_tokens=16384, preferred="deepseek"
        )
        if not text:
            return self._fail(step_name, "Repairer", usage.get("error", "unknown"))

        self._log(f"\n--- STEP repairer [{svc}] [{len(text)}c, {usage.get('completion_tokens', '?')}t] ---\n{text[:500]}{'...' if len(text) > 500 else ''}")
        return self._ok(step_name, "Repairer", svc, text, usage)


# ------------------------------------------------------------
# Ultra：单模型强输出
# ------------------------------------------------------------
class UltraStep(Step):
    def run(self, ctx: ContextManager) -> Dict[str, Any]:
        step_name = "ultra"
        self._notify(step_name, "start", {"name": "Ultra", "role": "单模型强输出"})
        full_ctx = ctx.full_context_for_deepseek()
        user = f"{full_ctx}\n\n请一次性完成分析、设计、编码和自我审查，输出最终可运行代码。"
        svc, text, usage = self.router.call_with_fallback(
            "coder", Prompts.ULTRA_SYSTEM, user,
            max_tokens=16384, preferred="deepseek"
        )
        if not text:
            return self._fail(step_name, "Ultra", usage.get("error", "unknown"))

        self._log(f"\n--- STEP ultra [{svc}] [{len(text)}c, {usage.get('completion_tokens', '?')}t] ---\n{text[:500]}{'...' if len(text) > 500 else ''}")
        return self._ok(step_name, "Ultra", svc, text, usage)
