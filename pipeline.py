#!/usr/bin/env python3
"""
Kimi-DeepSeek 多模型代码生成流水线（Reflexion + Capability-Aware Router）

流程：
  Planner(Kimi) -> Coder(DeepSeek) -> Critic(KimiCode) -> Validator -> Repairer(DeepSeek)

模式：
  full  : 完整五角色反射环
  fast  : DeepSeek 编码 + DeepSeek 自审查修复
  ultra : DeepSeek 单模型强输出（CoT + 自我审查）

环境变量：
  PIPELINE_MODE=full|fast|ultra
  SMART_ATTACH=auto|1|0
  MAX_ATTACH_CHARS=0
  API_MAX_RETRIES=2
  API_TIMEOUT=300
  KIMI_CONTEXT_LIMIT=6000
  DS_CONTEXT_LIMIT=100000
"""
import sys
import time
from enum import Enum
from typing import Optional, List, Dict, Any, Callable

from pipeline_core import Config, Router, ContextManager, check_missing_keys
from pipeline_steps import (
    PipelineResult, PlannerStep, CoderStep, CriticStep,
    ValidatorStep, RepairerStep, UltraStep, extract_code,
)


class Mode(str, Enum):
    FULL = "full"
    FAST = "fast"
    ULTRA = "ultra"


class Pipeline:
    """多模型代码生成流水线编排器。"""

    def __init__(self, mode: Optional[str] = None,
                 callback: Optional[Callable] = None,
                 verbose: bool = True):
        self.mode = self._normalize_mode(mode or Config.PIPELINE_MODE)
        self.callback = callback
        self.verbose = verbose
        self.router = Router()
        self.paths: List[str] = []

    def _normalize_mode(self, mode: str) -> str:
        m = mode.lower()
        if m in ("full", "fast", "ultra"):
            return m
        return "full"

    def _log(self, msg: str):
        if self.verbose:
            print(msg)

    def run(self, question: str,
            history: Optional[List[Dict[str, str]]] = None,
            context_files: Optional[List[str]] = None) -> PipelineResult:
        t0 = time.time()
        self.paths = []
        ctx = ContextManager(question, history=history, context_files=context_files)

        self._log(f"\n{'#' * 60}\n  MODE: {self.mode.upper()}\n  Q: {question[:120]}{'...' if len(question) > 120 else ''}\n{'#' * 60}")

        if self.mode == "ultra":
            return self._run_ultra(ctx, t0)
        if self.mode == "fast":
            return self._run_fast(ctx, t0)
        return self._run_full(ctx, t0)

    def _run_full(self, ctx: ContextManager, t0: float) -> PipelineResult:
        # Step 1: Planner
        planner = PlannerStep(self.router, self.callback, self.verbose)
        r1 = planner.run(ctx)
        if "error" in r1:
            return self._error(r1["error"], "planner")
        self.paths.extend(planner.paths)
        plan = r1["text"]

        # Step 2: Coder
        coder = CoderStep(self.router, self.callback, self.verbose)
        r2 = coder.run(ctx, plan)
        if "error" in r2:
            return self._error(r2["error"], "coder")
        self.paths.extend(coder.paths)
        code_v1 = extract_code(r2["text"])

        # Step 3: Critic
        critic = CriticStep(self.router, self.callback, self.verbose)
        r3 = critic.run(ctx, code_v1)
        if "error" in r3:
            return self._error(r3["error"], "critic")
        self.paths.extend(critic.paths)
        review = r3["text"]
        code_v2 = extract_code(review) or code_v1

        # Step 4: Validator
        validator = ValidatorStep(self.router, self.callback, self.verbose)
        r4 = validator.run(ctx, code_v2)
        if "error" in r4:
            # 验证失败不致命，继续用 v2
            validation = r4.get("error", "")
        else:
            self.paths.extend(validator.paths)
            validation = r4["report"]

        # Step 5: Repairer
        repairer = RepairerStep(self.router, self.callback, self.verbose)
        r5 = repairer.run(ctx, code_v2, review, validation)
        if "error" in r5:
            return self._error(r5["error"], "repairer")
        self.paths.extend(repairer.paths)
        final_code = extract_code(r5["text"])

        elapsed = round(time.time() - t0, 1)
        self._log(f"\n{'=' * 60}\n  DONE full mode in {elapsed}s\n{'=' * 60}")
        return PipelineResult(
            code=final_code,
            review=review,
            validation=validation,
            elapsed=elapsed,
            paths=self.paths,
            mode="full",
        )

    def _run_fast(self, ctx: ContextManager, t0: float) -> PipelineResult:
        # DeepSeek 直接编码
        coder = CoderStep(self.router, self.callback, self.verbose)
        plan = (
            "1. 理解用户需求\n"
            "2. 编写结构清晰、可运行的代码\n"
            "3. 添加必要错误处理和注释\n"
            "4. 自我审查常见边界错误并修复"
        )
        r1 = coder.run(ctx, plan)
        if "error" in r1:
            return self._error(r1["error"], "coder")
        self.paths.extend(coder.paths)
        code_v1 = extract_code(r1["text"])

        # DeepSeek 自审查修复
        repairer = RepairerStep(self.router, self.callback, self.verbose)
        r2 = repairer.run(ctx, code_v1, "请自我审查并修复潜在问题", "")
        if "error" in r2:
            # 修复失败回退到 v1
            final_code = code_v1
            review = ""
        else:
            self.paths.extend(repairer.paths)
            review = r2["text"]
            final_code = extract_code(review) or code_v1

        elapsed = round(time.time() - t0, 1)
        self._log(f"\n{'=' * 60}\n  DONE fast mode in {elapsed}s\n{'=' * 60}")
        return PipelineResult(
            code=final_code,
            review=review,
            validation="",
            elapsed=elapsed,
            paths=self.paths,
            mode="fast",
        )

    def _run_ultra(self, ctx: ContextManager, t0: float) -> PipelineResult:
        ultra = UltraStep(self.router, self.callback, self.verbose)
        r = ultra.run(ctx)
        if "error" in r:
            return self._error(r["error"], "ultra")
        self.paths.extend(ultra.paths)
        final_code = extract_code(r["text"])

        elapsed = round(time.time() - t0, 1)
        self._log(f"\n{'=' * 60}\n  DONE ultra mode in {elapsed}s\n{'=' * 60}")
        return PipelineResult(
            code=final_code,
            review=r["text"],
            validation="",
            elapsed=elapsed,
            paths=self.paths,
            mode="ultra",
        )

    def _error(self, error: str, step: str) -> PipelineResult:
        self._log(f"\n{'=' * 60}\n  ERROR at {step}: {error}\n{'=' * 60}")
        return PipelineResult(error=error, step=step, mode=self.mode, paths=self.paths)


# ------------------------------------------------------------
# 兼容旧接口
# ------------------------------------------------------------
def run(question: str,
        history: Optional[List[Dict[str, str]]] = None,
        callback: Optional[Callable] = None,
        verbose: bool = True,
        context_files: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    兼容旧版调用：pipeline.run(question, history=..., callback=..., verbose=...)
    返回 dict（含 code / review / validation / elapsed / paths / mode / error）。
    """
    p = Pipeline(callback=callback, verbose=verbose)
    result = p.run(question, history=history, context_files=context_files)
    return result.to_dict()


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else sys.stdin.read().strip()
    if not q:
        print("用法: python pipeline.py '<问题>'")
        sys.exit(1)

    missing = check_missing_keys()
    if missing:
        print(f"缺少环境变量: {', '.join(missing)}")
        print("请复制 .env.example 为 .env 并填入真实 key。")
        sys.exit(2)

    result = Pipeline().run(q)
    if result.error:
        print(f"\nERROR: {result.error}")
        sys.exit(3)

    print("\n===== FINAL CODE =====\n")
    print(result.code)
