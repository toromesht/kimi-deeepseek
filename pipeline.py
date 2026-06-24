#!/usr/bin/env python3
"""
code-<问题> 多模型流水线
  Kimi(提示词) -> KimiCode(框架) -> DeepSeek(代码)
  -> KimiCode(检验+批判+修理) -> DeepSeek(复查修bug) -> 输出

用法:
  python pipeline.py "<问题>"
  code-"<问题>"   (如果配了 shell alias)

密钥读取优先级: 环境变量 > 同目录 .env 文件
"""
import sys, os, json, time, ssl, urllib.request, urllib.error

# ------------------------------------------------------------
# 加载 .env（不依赖 python-dotenv）
# ------------------------------------------------------------
_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_ENV_PATH):
    with open(_ENV_PATH, "r", encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _v = _line.split("=", 1)
            _k = _k.strip()
            _v = _v.strip().strip('"').strip("'")
            if _k and _k not in os.environ:
                os.environ[_k] = _v

CFG = {
    "kimi": {
        "url": os.environ.get("KIMI_BASE", "https://api.moonshot.cn/v1") + "/chat/completions",
        "key": os.environ.get("KIMI_KEY", ""),
        "model": os.environ.get("KIMI_MODEL", "moonshot-v1-8k"),
        "max_tok": 2048,
        "temp": 0.7,
    },
    "kimi_code": {
        "url": os.environ.get("KIMI_CODE_BASE", "https://api.moonshot.cn/v1") + "/chat/completions",
        "key": os.environ.get("KIMI_CODE_KEY", ""),
        "model": os.environ.get("KIMI_CODE_MODEL", "moonshot-v1-8k"),
        "max_tok": 4096,
        "temp": 0.5,
    },
    "deepseek": {
        "url": os.environ.get("DEEPSEEK_BASE", "https://api.deepseek.com/v1") + "/chat/completions",
        "key": os.environ.get("DEEPSEEK_KEY", ""),
        "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        "max_tok": 16384,
        "temp": 0.3,
    },
}


def call(svc, system, user, max_tok=None):
    c = CFG[svc]
    if not c["key"]:
        return None, f"MISSING_KEY:{svc}"
    body = json.dumps({
        "model": c["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tok or c["max_tok"],
        "temperature": c["temp"],
    }).encode()
    req = urllib.request.Request(c["url"], data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {c['key']}")
    try:
        with urllib.request.urlopen(req, timeout=300,
                                     context=ssl.create_default_context()) as r:
            d = json.loads(r.read())
            return d["choices"][0]["message"]["content"], d.get("usage", {})
    except urllib.error.HTTPError as e:
        return None, f"HTTP{e.code}:{(e.read()[:400].decode() if e.fp else str(e))}"
    except Exception as e:
        return None, str(e)[:300]


def save_round(step, text, out_dir="outputs"):
    os.makedirs(out_dir, exist_ok=True)
    ts = time.strftime("%m%d_%H%M%S")
    path = os.path.join(out_dir, f"{step}_{ts}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def run(q):
    t0 = time.time()
    print(f"\n{'#' * 55}\n  Q: {q[:120]}{'...' if len(q) > 120 else ''}\n{'#' * 55}")

    # STEP 1: Kimi 写提示词
    r1, u1 = call("kimi",
        "你是 Prompt 工程师。根据用户问题，写给代码 AI 的详细提示词。"
        "包含：技术选型、输入输出、边界条件、错误处理、测试要点。直接输出提示词。", q)
    if not r1:
        print(f"FAIL1: {u1}"); return None
    print(f"\n--- STEP1 Kimi->提示词 [{len(r1)}c, {u1.get('completion_tokens', '?')}t] ---")
    print(r1[:400] + ("..." if len(r1) > 400 else ""))
    save_round("01_prompt", r1)

    # STEP 2: KimiCode 写框架
    r2, u2 = call("kimi_code",
        "你是架构师。根据提示词写代码框架：模块划分、函数签名、伪代码、数据流。"
        "只写框架，不写具体实现。", r1)
    if not r2:
        print(f"FAIL2: {u2}"); return None
    print(f"\n--- STEP2 KimiCode->框架 [{len(r2)}c, {u2.get('completion_tokens', '?')}t] ---")
    print(r2[:400] + ("..." if len(r2) > 400 else ""))
    save_round("02_framework", r2)

    # STEP 3: DeepSeek 写代码
    r3, u3 = call("deepseek",
        "你是编程专家。根据框架写完整可运行代码。要求："
        "1. 代码干净、注释适度；2. 有错误处理；3. 多文件时用 ```filename:path 标记；"
        "4. 如果问题含测试要求，给出测试用例。", r2,
        max_tok=16384)
    if not r3:
        print(f"FAIL3: {u3}"); return None
    print(f"\n--- STEP3 DeepSeek->代码 [{len(r3)}c, {u3.get('completion_tokens', '?')}t] ---")
    print(r3[:400] + ("..." if len(r3) > 400 else ""))
    save_round("03_code", r3)

    # STEP 4: KimiCode 检验+批判+修理
    r4, u4 = call("kimi_code",
        "你是严格代码审查员，具备批判性思维。请对以下代码做：\n"
        "1. 检验语法和逻辑是否完整正确；\n"
        "2. 提出批判性质疑——对每个设计决策追问「为什么要这样做？」「有没有更好的方案？」"
        "「如果输入极端值会怎样？」「这个假设在什么条件下不成立？」；\n"
        "3. 直接修复发现的所有问题，输出修正后的完整代码。\n\n"
        f"原始需求：{q}\n\n待审查代码：\n{r3}",
        max_tok=8192)
    if not r4:
        print(f"FAIL4: {u4}"); return None
    print(f"\n--- STEP4 KimiCode->检验+批判+修理 [{len(r4)}c, {u4.get('completion_tokens', '?')}t] ---")
    print(r4[:500] + ("..." if len(r4) > 500 else ""))
    save_round("04_review", r4)

    # STEP 5: DeepSeek 复查批判意见，最终修改
    r5, u5 = call("deepseek",
        "代码审查员提出了批判性建议和修改。请你：\n"
        "1. 逐条审视审查意见，判断哪些接受、哪些驳回（附理由）；\n"
        "2. 综合所有合理建议，输出最终版完整代码。\n\n"
        f"原始需求：{q}\n\n审查意见与修改：\n{r4}",
        max_tok=16384)
    if not r5:
        print(f"FAIL5: {u5}"); return None
    print(f"\n--- STEP5 DeepSeek->复查终版 [{len(r5)}c, {u5.get('completion_tokens', '?')}t] ---")
    print(r5[:500] + ("..." if len(r5) > 500 else ""))
    save_round("05_final", r5)

    dt = time.time() - t0
    print(f"\n{'=' * 55}\n  DONE {dt:.1f}s\n{'=' * 55}")
    return {"code": r5, "review": r4, "elapsed": round(dt, 1)}


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else sys.stdin.read().strip()
    if not q:
        print("用法: python pipeline.py '<问题>'")
        sys.exit(1)

    missing = []
    for k, svc in [("KIMI_KEY", "kimi"), ("KIMI_CODE_KEY", "kimi_code"), ("DEEPSEEK_KEY", "deepseek")]:
        if not CFG[svc]["key"]:
            missing.append(k)
    if missing:
        print(f"缺少环境变量: {', '.join(missing)}")
        print("请在 .env 文件或环境变量中设置。")
        sys.exit(2)

    r = run(q)
    if r:
        print("\n===== FINAL CODE =====\n")
        print(r["code"])
