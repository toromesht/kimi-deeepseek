#!/usr/bin/env python3
"""
Kimi-DeepSeek 流水线核心：配置、模型客户端、上下文管理。
"""
import os
import sys
import json
import time
import ssl
import re
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple, Callable


# ------------------------------------------------------------
# 加载 .env（不依赖 python-dotenv）
# ------------------------------------------------------------
_CANDIDATE_ENV_PATHS = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
    os.path.join(os.getcwd(), ".env"),
    os.path.join(os.path.expanduser("~"), ".claude", "skills", "code-pipeline", ".env"),
]
_ENV_PATH = next((p for p in _CANDIDATE_ENV_PATHS if os.path.exists(p)), None)
if _ENV_PATH:
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


def _env_int(name: str, default: int) -> int:
    try:
        v = int(os.environ.get(name, str(default)))
        return v if v >= 0 else default
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name, str(default)).lower()
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    return default


def _env_str(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


# ------------------------------------------------------------
# 配置
# ------------------------------------------------------------
@dataclass(frozen=True)
class ModelProfile:
    name: str
    service: str
    base_url: str
    api_key: str
    model: str
    context_limit: int          # token
    safe_context_limit: int     # token，超过则触发摘要/裁剪
    max_output_tokens: int
    temperature: float
    strengths: Tuple[str, ...]


class Config:
    # 运行模式
    PIPELINE_MODE: str = _env_str("PIPELINE_MODE", "full").lower()

    # 附件与上下文
    MAX_ATTACH_CHARS: int = _env_int("MAX_ATTACH_CHARS", 0)   # 0 = 不截断
    SMART_ATTACH: str = _env_str("SMART_ATTACH", "auto").lower()
    HISTORY_TURNS: int = _env_int("HISTORY_TURNS", 6)
    HISTORY_CHARS_PER_TURN: int = _env_int("HISTORY_CHARS_PER_TURN", 800)

    # API 行为
    API_MAX_RETRIES: int = _env_int("API_MAX_RETRIES", 2)
    API_TIMEOUT: int = _env_int("API_TIMEOUT", 300)
    DEBUG: bool = _env_bool("PIPELINE_DEBUG", False)

    # 模型安全上下文
    KIMI_CONTEXT_LIMIT: int = _env_int("KIMI_CONTEXT_LIMIT", 6000)
    DS_CONTEXT_LIMIT: int = _env_int("DS_CONTEXT_LIMIT", 100000)

    # 输出
    OUTPUT_DIR: str = _env_str("OUTPUT_DIR", "outputs")

    @classmethod
    def model_profiles(cls) -> Dict[str, ModelProfile]:
        return {
            "kimi": ModelProfile(
                name="Kimi",
                service="kimi",
                base_url=_env_str("KIMI_BASE", "https://api.moonshot.cn/v1"),
                api_key=_env_str("KIMI_KEY", ""),
                model=_env_str("KIMI_MODEL", "moonshot-v1-8k"),
                context_limit=8192,
                safe_context_limit=cls.KIMI_CONTEXT_LIMIT,
                max_output_tokens=2048,
                temperature=0.6,
                strengths=("planning", "prompt_engineering", "summarization"),
            ),
            "kimi_code": ModelProfile(
                name="KimiCode",
                service="kimi_code",
                base_url=_env_str("KIMI_CODE_BASE", "https://api.moonshot.cn/v1"),
                api_key=_env_str("KIMI_CODE_KEY", ""),
                model=_env_str("KIMI_CODE_MODEL", "moonshot-v1-8k"),
                context_limit=8192,
                safe_context_limit=cls.KIMI_CONTEXT_LIMIT,
                max_output_tokens=4096,
                temperature=0.5,
                strengths=("review", "critic", "architecture", "repair"),
            ),
            "deepseek": ModelProfile(
                name="DeepSeek",
                service="deepseek",
                base_url=_env_str("DEEPSEEK_BASE", "https://api.deepseek.com/v1"),
                api_key=_env_str("DEEPSEEK_KEY", ""),
                model=_env_str("DEEPSEEK_MODEL", "deepseek-chat"),
                context_limit=128000,
                safe_context_limit=cls.DS_CONTEXT_LIMIT,
                max_output_tokens=16384,
                temperature=0.3,
                strengths=("coding", "long_context", "reasoning", "validation"),
            ),
        }


# ------------------------------------------------------------
# Token 估算
# ------------------------------------------------------------
def estimate_tokens(text: str) -> int:
    """粗略估算 token 数：中文字符按 1 token，其他按 4 字符/token。"""
    if not text:
        return 0
    cn = len(re.findall(r"[\u4e00-\u9fff]", text))
    other = len(text) - cn
    return cn + max(1, other // 4)


def fit_context(text: str, token_limit: int, reserve: int = 500) -> str:
    """如果 text 超过 token_limit - reserve，则截断并加提示。"""
    if estimate_tokens(text) <= token_limit - reserve:
        return text
    target_chars = (token_limit - reserve) * 4
    if target_chars < 200:
        target_chars = 200
    truncated = text[:target_chars]
    return truncated + f"\n\n[上下文已截断至约 {token_limit - reserve} token 以适配模型上下文]"


# ------------------------------------------------------------
# 模型客户端
# ------------------------------------------------------------
CallResult = Tuple[Optional[str], Dict[str, Any]]


class ModelClient:
    """统一 LLM 调用客户端：重试、超时、Fallback、Token 预算。"""

    def __init__(self, profile: ModelProfile):
        self.profile = profile
        self.url = profile.base_url.rstrip("/") + "/chat/completions"
        self._ctx = ssl.create_default_context()

    def call(self, system: str, user: str, max_tokens: Optional[int] = None) -> CallResult:
        if not self.profile.api_key:
            return None, {"error": f"MISSING_KEY:{self.profile.service}"}

        body = json.dumps({
            "model": self.profile.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens or self.profile.max_output_tokens,
            "temperature": self.profile.temperature,
        }, ensure_ascii=False).encode("utf-8")

        last_error = ""
        for attempt in range(Config.API_MAX_RETRIES + 1):
            req = urllib.request.Request(self.url, data=body, method="POST")
            req.add_header("Content-Type", "application/json; charset=utf-8")
            req.add_header("Authorization", f"Bearer {self.profile.api_key}")
            try:
                with urllib.request.urlopen(req, timeout=Config.API_TIMEOUT, context=self._ctx) as r:
                    d = json.loads(r.read().decode("utf-8"))
                    content = d["choices"][0]["message"]["content"]
                    usage = d.get("usage", {})
                    if Config.DEBUG:
                        print(f"[DEBUG {self.profile.name}] ok tokens={usage}")
                    return content, usage
            except urllib.error.HTTPError as e:
                err_body = e.read()[:1200].decode("utf-8", errors="replace") if e.fp else str(e)
                last_error = f"HTTP{e.code}:{err_body}"
                if 400 <= e.code < 500:
                    break
            except Exception as e:
                last_error = str(e)[:500]

            if attempt < Config.API_MAX_RETRIES:
                wait = 2 ** attempt
                if Config.DEBUG:
                    print(f"[DEBUG {self.profile.name}] retry in {wait}s ({attempt+1}/{Config.API_MAX_RETRIES}): {last_error}")
                time.sleep(wait)

        return None, {"error": last_error}


class Router:
    """能力感知路由：为每个角色选择合适模型，并在失败时回退。"""

    def __init__(self):
        self.profiles = Config.model_profiles()
        self.clients = {k: ModelClient(v) for k, v in self.profiles.items()}

    def client(self, service: str) -> ModelClient:
        return self.clients[service]

    def route(self, role: str, context_text: str) -> str:
        """根据角色和上下文长度选择首选模型服务名。"""
        tokens = estimate_tokens(context_text)
        if role in ("planner", "critic"):
            if tokens <= Config.KIMI_CONTEXT_LIMIT:
                return "kimi_code" if role == "critic" else "kimi"
            return "deepseek"
        if role in ("coder", "repairer", "validator"):
            if tokens <= Config.DS_CONTEXT_LIMIT:
                return "deepseek"
            return "kimi_code"
        return "deepseek"

    def call_with_fallback(self, role: str, system: str, user: str,
                           max_tokens: Optional[int] = None,
                           preferred: Optional[str] = None) -> Tuple[str, str, Dict[str, Any]]:
        """
        调用首选模型，失败则按能力回退。
        返回 (service, content, usage)。
        """
        if role in ("planner", "critic"):
            fallback_order = [o for o in (preferred, "kimi_code", "kimi", "deepseek") if o]
        else:
            fallback_order = [o for o in (preferred, "deepseek", "kimi_code", "kimi") if o]
        seen = []
        for svc in fallback_order:
            if svc not in seen and svc in self.clients:
                seen.append(svc)
                content, usage = self.clients[svc].call(system, user, max_tokens=max_tokens)
                if content is not None:
                    return svc, content, usage
                if Config.DEBUG:
                    print(f"[DEBUG fallback] {svc} failed: {usage.get('error')}")
        last_error = usage.get("error", "all_fallbacks_failed") if fallback_order else "no_model_available"
        return fallback_order[-1] if fallback_order else "unknown", "", {"error": last_error}


# ------------------------------------------------------------
# 上下文管理
# ------------------------------------------------------------
_FILE_PATH_RE = re.compile(
    r'["\']([a-zA-Z]:\\[^"\']+)["\']|([a-zA-Z]:\\\S+)',
    re.IGNORECASE,
)
_FILE_CACHE: Dict[Tuple[str, int], str] = {}


def extract_file_paths(text: str) -> List[str]:
    paths = []
    for m in _FILE_PATH_RE.finditer(text):
        p = m.group(1) or m.group(2)
        if p and os.path.isfile(p):
            paths.append(p)
    return list(dict.fromkeys(paths))


def read_file(path: str, max_chars: Optional[int] = None) -> str:
    key = (path, max_chars if max_chars is not None else -1)
    if key in _FILE_CACHE:
        return _FILE_CACHE[key]
    try:
        for enc in ("utf-8", "utf-8-sig", "gbk", "gb2312", "latin1"):
            try:
                with open(path, "r", encoding=enc, errors="replace") as f:
                    content = f.read() if max_chars == 0 or max_chars is None else f.read(max_chars)
                if max_chars and max_chars > 0 and len(content) >= max_chars:
                    content = content[:max_chars] + f"\n\n[文件内容过长，已截断至前 {max_chars} 字符]"
                _FILE_CACHE[key] = content
                return content
            except UnicodeDecodeError:
                continue
        _FILE_CACHE[key] = "[无法解码文件内容]"
    except Exception as e:
        _FILE_CACHE[key] = f"[读取文件失败: {e}]"
    return _FILE_CACHE[key]


class ContextManager:
    """管理用户问题、附件、历史对话，按模型能力生成不同上下文视图。"""

    def __init__(self, question: str, history: Optional[List[Dict[str, str]]] = None,
                 context_files: Optional[List[str]] = None):
        self.question = question
        self.history = history or []
        self.extra_files = context_files or []
        self.file_paths = self._collect_paths()
        self.file_contents = {p: read_file(p, max_chars=Config.MAX_ATTACH_CHARS) for p in self.file_paths}

    def _collect_paths(self) -> List[str]:
        paths = extract_file_paths(self.question)
        for p in self.extra_files:
            if os.path.isfile(p) and p not in paths:
                paths.append(p)
        return paths

    def _use_smart_attach(self) -> bool:
        if Config.SMART_ATTACH in ("1", "true", "yes", "on"):
            return True
        if Config.SMART_ATTACH in ("0", "false", "no", "off"):
            return False
        return Config.MAX_ATTACH_CHARS == 0

    def file_summary(self) -> str:
        if not self.file_paths:
            return ""
        parts = ["\n--- 涉及文件 ---"]
        for p in self.file_paths:
            size = os.path.getsize(p)
            parts.append(f"- {p} ({size} bytes)")
        parts.append("---\n")
        return "\n".join(parts)

    def file_contents_block(self, token_limit: Optional[int] = None) -> str:
        if not self.file_paths:
            return ""
        parts = ["\n--- 附件文件内容 ---"]
        for p in self.file_paths:
            content = self.file_contents[p]
            if token_limit:
                content = fit_context(content, token_limit, reserve=200)
            parts.append(f"\n### {p}\n```\n{content}\n```\n")
        return "".join(parts)

    def history_block(self, max_turns: int = None, max_chars: int = None) -> str:
        max_turns = max_turns or Config.HISTORY_TURNS
        max_chars = max_chars or Config.HISTORY_CHARS_PER_TURN
        if not self.history:
            return ""
        parts = ["\n--- 历史对话 ---"]
        for item in self.history[-max_turns:]:
            role = item.get("role", "")
            content = item.get("content", "")
            label = {"user": "用户", "assistant": "助手"}.get(role, role)
            display = content[:max_chars]
            if len(content) > max_chars:
                display += " ...[截断]"
            parts.append(f"{label}：{display}")
        parts.append("---\n")
        return "\n".join(parts)

    def view_for(self, service: str) -> Tuple[str, str]:
        """
        为指定模型返回 (system_context, user_context)。
        system_context 是通用背景，user_context 是任务+文件+历史。
        """
        profile = Config.model_profiles()[service]
        token_limit = profile.safe_context_limit

        history = self.history_block()
        if service.startswith("kimi") and self._use_smart_attach():
            files = self.file_summary()
        else:
            files = self.file_contents_block(token_limit=token_limit)

        user_ctx = f"当前问题：{self.question}\n{history}{files}"
        system_ctx = (
            f"你是 {profile.name} ({profile.model})。"
            f"上下文安全上限约 {token_limit} tokens。"
        )
        return system_ctx, fit_context(user_ctx, token_limit)

    def full_context_for_deepseek(self) -> str:
        """给 DeepSeek 的完整上下文（不经过 Kimi 安全限制）。"""
        history = self.history_block()
        files = self.file_contents_block(token_limit=Config.DS_CONTEXT_LIMIT)
        return f"当前问题：{self.question}\n{history}{files}"


# ------------------------------------------------------------
# 工具函数
# ------------------------------------------------------------
def save_round(step: str, text: str, out_dir: Optional[str] = None) -> str:
    out_dir = out_dir or Config.OUTPUT_DIR
    os.makedirs(out_dir, exist_ok=True)
    ts = time.strftime("%m%d_%H%M%S")
    path = os.path.join(out_dir, f"{step}_{ts}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def notify(callback: Optional[Callable], step: str, status: str, data: Dict[str, Any] = None):
    if callback:
        try:
            callback(step, status, data or {})
        except Exception:
            pass


def check_missing_keys() -> List[str]:
    missing = []
    for k, svc in [("KIMI_KEY", "kimi"), ("KIMI_CODE_KEY", "kimi_code"), ("DEEPSEEK_KEY", "deepseek")]:
        if not _env_str(k):
            missing.append(k)
    return missing
