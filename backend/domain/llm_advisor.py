"""可选大模型（OpenAI 兼容接口）深度本质拆解、灵活抄作业与差距建议模块（支持原生 SSE 流式输出）。

支持：OpenAI / DeepSeek / Gemini (OpenAI 兼容端点) / 本地 Ollama / 任意兼容接口。
特性：
  1. 用离线精简版本词典（patch_dict.json）自动补齐已点天赋/升华分支子节点与全部主动/辅助宝石的官方原文与核心数值；
  2. 支持 4 种分析模式：
     - target  : 目标 BD 本质拆解（轻量 JSON 卡片头含 gearTiers + `---` + 5 块深度本质拆解 Markdown）
     - copy    : 按自身角色等级 + 造价预算滑块 + 已有件生成定制抄作业落地方案
     - consult : 无 PoB 码时按「升华/玩法 + 等级 + 预算」流式咨询起步与过渡路线
     - gap     : 我的 BD vs 目标 BD 精确差距流式追赶路线
  3. 原生 SSE 流式调用（stream: true），单次 chunk 读空闲超时 60s（总时长不设硬上限），兼容思考型模型 reasoning_content。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Iterator

from backend.data import patch_dict_updater
from backend.domain import affix_analyzer, trade_url
from backend.engine import rune_index
from backend.market.price_store import STATE_DIR

LLM_CONFIG_FILENAME = "llm_config.json"
LLM_CACHE_FILENAME = "llm_profile_cache.json"
_STRUCTURED_PROMPT_VERSION = "v13-reverse-spec-weighted-blueprint-pob-sandbox"
_CACHE_MAX_ENTRIES = 32
_VALID_BUDGET_TIERS = {"league-start", "mid", "high", "mirror"}
_VALID_GRADES = {"S", "A", "B", "C", "D"}
_VALID_RADAR_IDS = (
    "clearSpeed",
    "bossing",
    "mobility",
    "survivability",
    "sustain",
    "costEfficiency",
)
_RADAR_AXIS_LABELS: dict[str, str] = {
    "clearSpeed": "清图效率",
    "bossing": "攻坚爆发",
    "mobility": "机动位移",
    "survivability": "生存硬度",
    "sustain": "续航手感",
    "costEfficiency": "造价性价比",
}
_VALID_CONTENT_IDS = (
    "t15_mapping",
    "juiced_t15_delirium",
    "pinnacle_t0_t2",
    "pinnacle_t4_uber",
    "trials_sekhema_chaos",
)
_CONTENT_READINESS_META: dict[str, tuple[str, str]] = {
    "t15_mapping": ("T15 常规速刷", "T15 Mapping"),
    "juiced_t15_delirium": ("T15+ 高危/迷雾/裂隙", "Juiced T15+ / Delirium"),
    "pinnacle_t0_t2": ("终局首领 (T0~T2)", "Pinnacle Bosses (T0–T2)"),
    "pinnacle_t4_uber": ("终局满难 Boss (T4)", "Uber / T4 Pinnacle"),
    "trials_sekhema_chaos": ("圣所/混沌试炼", "Sekhemas / Chaos Trials"),
}
_DEFAULT_GRADE_VERDICT: dict[str, tuple[str, str]] = {
    "S": ("碾压速通", "Crushing / Speedrun"),
    "A": ("稳健推荐", "Solid & Reliable"),
    "B": ("可打需操作", "Viable w/ Mechanics"),
    "C": ("吃力易暴毙", "Struggles / Rippy"),
    "D": ("红线卡住/不推荐", "Blocked / Not Recommended"),
}

PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "deepseek": {
        "label": "DeepSeek",
        "baseUrl": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
    },
    "gemini": {
        "label": "Gemini (OpenAI 兼容)",
        "baseUrl": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-2.5-flash",
    },
    "openai": {
        "label": "OpenAI",
        "baseUrl": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    },
    "ollama": {
        "label": "本地 Ollama",
        "baseUrl": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5:14b",
    },
    "custom": {
        "label": "自定义 OpenAI 兼容",
        "baseUrl": "",
        "model": "",
    },
}


def _config_path(state_dir: str = STATE_DIR) -> str:
    return os.path.join(state_dir, LLM_CONFIG_FILENAME)


def load_llm_config(state_dir: str = STATE_DIR) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "enabled": False,
        "provider": "deepseek",
        "baseUrl": PROVIDER_PRESETS["deepseek"]["baseUrl"],
        "apiKey": "",
        "model": PROVIDER_PRESETS["deepseek"]["model"],
        "temperature": 0.3,
    }
    path = _config_path(state_dir)
    if not os.path.isfile(path):
        return defaults
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return defaults
        return {
            "enabled": bool(raw.get("enabled", False)),
            "provider": str(raw.get("provider") or "custom"),
            "baseUrl": str(raw.get("baseUrl") or "").strip(),
            "apiKey": str(raw.get("apiKey") or "").strip(),
            "model": str(raw.get("model") or "").strip(),
            "temperature": float(raw.get("temperature") if raw.get("temperature") is not None else 0.3),
        }
    except Exception:
        return defaults


def public_llm_config(cfg: dict[str, Any]) -> dict[str, Any]:
    key = str(cfg.get("apiKey") or "")
    preview = ""
    if len(key) >= 10:
        preview = f"{key[:4]}...{key[-4:]}"
    elif key:
        preview = "***"
    return {
        "enabled": bool(cfg.get("enabled")),
        "provider": str(cfg.get("provider") or "deepseek"),
        "baseUrl": str(cfg.get("baseUrl") or ""),
        "model": str(cfg.get("model") or ""),
        "temperature": float(cfg.get("temperature") or 0.3),
        "hasApiKey": bool(key),
        "apiKeyPreview": preview,
        "presets": PROVIDER_PRESETS,
    }


def save_llm_config(payload: dict[str, Any], state_dir: str = STATE_DIR) -> dict[str, Any]:
    prev = load_llm_config(state_dir)
    provider = str(payload.get("provider") or prev.get("provider") or "custom").strip()
    base_url = str(payload.get("baseUrl") if payload.get("baseUrl") is not None else prev.get("baseUrl") or "").strip()
    model = str(payload.get("model") if payload.get("model") is not None else prev.get("model") or "").strip()

    if payload.get("clearApiKey"):
        api_key = ""
    elif payload.get("apiKey") is not None and str(payload.get("apiKey")).strip() != "":
        api_key = str(payload.get("apiKey")).strip()
    else:
        api_key = str(prev.get("apiKey") or "")

    try:
        temp = max(0.0, min(1.5, float(payload.get("temperature", prev.get("temperature", 0.3)))))
    except (TypeError, ValueError):
        temp = 0.3

    enabled = bool(payload.get("enabled", prev.get("enabled", False)))
    cfg = {
        "enabled": enabled,
        "provider": provider,
        "baseUrl": base_url,
        "apiKey": api_key,
        "model": model,
        "temperature": temp,
    }

    os.makedirs(state_dir, exist_ok=True)
    path = _config_path(state_dir)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return cfg


def _chat_completions_url(base_url: str) -> str:
    b = (base_url or "").strip().rstrip("/")
    if not b:
        raise ValueError("请先填写大模型 API Base URL")
    if b.endswith("/chat/completions"):
        return b
    return f"{b}/chat/completions"


def _build_request_headers(cfg: dict[str, Any], url: str) -> dict[str, str]:
    provider = str(cfg.get("provider") or "").lower()
    api_key = str(cfg.get("apiKey") or "").strip()
    if not api_key and provider != "ollama" and "127.0.0.1" not in url and "localhost" not in url:
        raise ValueError("请先填写大模型 API Key")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "PoE2Workbench/1.0",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _default_http_post(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="ignore")[:300]
        raise RuntimeError(f"大模型接口返回 HTTP {exc.code}：{err_body}") from exc
    except Exception as exc:
        raise RuntimeError(f"连接大模型接口失败：{exc}") from exc


def call_chat_completion(
    cfg: dict[str, Any],
    system_prompt: str,
    user_prompt: str,
    *,
    http_post: Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]] | None = None,
    timeout: float = 150.0,
) -> dict[str, Any]:
    url = _chat_completions_url(str(cfg.get("baseUrl") or ""))
    model = str(cfg.get("model") or "").strip()
    if not model:
        raise ValueError("请先填写大模型名称（Model）")
    headers = _build_request_headers(cfg, url)

    payload = {
        "model": model,
        "temperature": float(cfg.get("temperature") or 0.3),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    post_fn = http_post or _default_http_post
    resp = post_fn(url, headers, payload, timeout)
    choices = resp.get("choices") or []
    if not choices:
        raise RuntimeError("大模型未返回任何结果（choices 为空）")
    msg = choices[0].get("message") or {}
    content = msg.get("content")
    if isinstance(content, list):
        content = "\n".join(str(part.get("text") or "") for part in content if isinstance(part, dict)).strip()
    text = str(content or "").strip()
    if not text:
        raise RuntimeError("大模型返回内容为空")

    return {
        "text": text,
        "reasoning": str(msg.get("reasoning_content") or msg.get("reasoning") or "").strip(),
        "model": str(resp.get("model") or model),
        "usage": resp.get("usage") or {},
    }


POB2_COPY_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "pob_test_replacement",
            "description": "将目标 BD 指定槽位替换成你设计的平替装备（给定英文基底与词缀列表），用本地 PoB2 引擎实算替换后的 ΔDPS%、ΔEHP%、攻速与红线是否断裂。",
            "parameters": {
                "type": "object",
                "properties": {
                    "slot": {"type": "string", "description": "装备槽位名，如 Weapon 1 / Helmet / Body Armour / Gloves / Boots / Amulet / Ring 1 / Belt"},
                    "baseType": {"type": "string", "description": "PoE2 英文基底名，如 Gemini Bow / Siphoning Wand / Sekhema Sandals"},
                    "rarity": {"type": "string", "enum": ["RARE", "UNIQUE", "MAGIC"], "default": "RARE"},
                    "name": {"type": "string", "description": "装备名称（暗金需准确英文名）"},
                    "mods": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "英文词缀列表（标准 PoE2 格式，如 '+4 to Level of all Lightning Spell Skills', 'Gain 25% of Damage as Extra Cold Damage', '+90 to maximum Life'）",
                    },
                },
                "required": ["slot", "baseType", "mods"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pob_probe_affixes",
            "description": "并行测算 1~6 条未在预扫描表中的自定义/冷门词缀对当前 BD 的真实 ΔDPS% 和 ΔEHP% 边际贡献。",
            "parameters": {
                "type": "object",
                "properties": {
                    "modLines": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "最多 6 条标准英文 PoE2 词缀",
                    },
                },
                "required": ["modLines"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pob_verify_copy_plan",
            "description": "将你拟定的整套预算平替装备一次性替换进目标 BD 跑 PoB2 终稿实算验收，核查预计可达成原 BD 的 DPS%/EHP% 及四抗/精魂/三属性红线是否全绿。",
            "parameters": {
                "type": "object",
                "properties": {
                    "replacements": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "slot": {"type": "string"},
                                "baseType": {"type": "string"},
                                "rarity": {"type": "string"},
                                "name": {"type": "string"},
                                "mods": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["slot", "baseType", "mods"],
                        },
                        "description": "需要替换验证的槽位平替列表",
                    },
                },
                "required": ["replacements"],
            },
        },
    },
]


def _call_chat_messages(
    cfg: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    http_post: Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]] | None = None,
    timeout: float = 150.0,
) -> dict[str, Any]:
    url = _chat_completions_url(str(cfg.get("baseUrl") or ""))
    model = str(cfg.get("model") or "").strip()
    if not model:
        raise ValueError("请先填写大模型名称（Model）")
    headers = _build_request_headers(cfg, url)
    payload: dict[str, Any] = {
        "model": model,
        "temperature": float(cfg.get("temperature") or 0.3),
        "messages": messages,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    post_fn = http_post or _default_http_post
    resp = post_fn(url, headers, payload, timeout)
    choices = resp.get("choices") or []
    if not choices:
        raise RuntimeError("大模型未返回任何结果（choices 为空）")
    msg = choices[0].get("message") or {}
    content = msg.get("content")
    if isinstance(content, list):
        content = "\n".join(str(part.get("text") or "") for part in content if isinstance(part, dict)).strip()
    return {
        "message": msg,
        "text": str(content or "").strip(),
        "reasoning": str(msg.get("reasoning_content") or msg.get("reasoning") or "").strip(),
        "tool_calls": msg.get("tool_calls") or [],
        "model": str(resp.get("model") or model),
        "usage": resp.get("usage") or {},
    }


def _execute_pob2_tool(
    tool_name: str,
    raw_args: str | dict[str, Any],
    pob_sandbox: dict[str, Any],
) -> tuple[dict[str, Any], str, bool]:
    """执行单个 PoB2 沙盒工具调用，返回 (result_dict, human_summary, is_bundle_verification)。"""
    from backend.domain import stat_sensitivity

    if isinstance(raw_args, str):
        try:
            args = json.loads(raw_args) if raw_args.strip() else {}
        except Exception:
            args = {}
    elif isinstance(raw_args, dict):
        args = raw_args
    else:
        args = {}

    norm_xml = str(pob_sandbox.get("norm_xml") or "")
    base_sim = pob_sandbox.get("base_sim") or {}
    pool = pob_sandbox.get("pool")
    if not norm_xml or pool is None:
        return {"error": "PoB2 沙盒引擎未就绪"}, "PoB2 沙盒不可用", False

    try:
        if tool_name == "pob_test_replacement":
            slot = str(args.get("slot") or "").strip()
            base_type = str(args.get("baseType") or "").strip()
            mods = [str(m) for m in (args.get("mods") or [])] if isinstance(args.get("mods"), list) else []
            res = stat_sensitivity.sandbox_test_replacement(
                norm_xml,
                base_sim,
                pool,
                slot=slot,
                base_type=base_type,
                mods=mods,
                rarity=str(args.get("rarity") or "RARE"),
                name=str(args.get("name") or f"Test {base_type}"),
            )
            summary = f"{slot} 换装实测：DPS {res['dpsPctOfTarget']}% ({res['deltaDpsPct']:+.1f}%) · EHP {res['ehpPctOfTarget']}%"
            if res["redlines"]:
                summary += f" · 红线：{', '.join(res['redlines'][:2])}"
            return res, summary, False

        if tool_name == "pob_probe_affixes":
            mod_lines = [str(m) for m in (args.get("modLines") or [])] if isinstance(args.get("modLines"), list) else []
            res = stat_sensitivity.sandbox_probe_affixes(norm_xml, base_sim, pool, mod_lines=mod_lines)
            cnt = len(res.get("results") or [])
            return res, f"完成 {cnt} 条自定义词缀 PoB2 灵敏度实测", False

        if tool_name == "pob_verify_copy_plan":
            reps = args.get("replacements") if isinstance(args.get("replacements"), list) else []
            res = stat_sensitivity.sandbox_verify_copy_plan(norm_xml, base_sim, pool, replacements=reps)
            slots_cnt = len(res.get("replacedSlots") or [])
            summary = (
                f"整套 {slots_cnt} 件平替验收：预计达成原 BD {res['dpsPctOfTarget']}% DPS / {res['ehpPctOfTarget']}% EHP"
                + (" · 红线全绿" if res["allGreen"] else f" · 红线警告：{', '.join(res['redlines'][:3])}")
            )
            return res, summary, True

        return {"error": f"未知工具：{tool_name}"}, f"忽略未知工具 {tool_name}", False
    except Exception as exc:
        return {"error": str(exc)}, f"{tool_name} 实测失败：{exc}", False


def _extract_delta_text(delta_val: Any) -> str:
    if isinstance(delta_val, str):
        return delta_val
    if isinstance(delta_val, list):
        return "".join(str(p.get("text") or "") for p in delta_val if isinstance(p, dict))
    return ""


def stream_chat_completion(
    cfg: dict[str, Any],
    system_prompt: str,
    user_prompt: str,
    *,
    messages_override: list[dict[str, Any]] | None = None,
    idle_timeout: float = 120.0,
) -> Iterator[dict[str, Any]]:
    """以 OpenAI 兼容 SSE 流式（stream: true）调用大模型，逐块产出 reasoning / delta 事件。"""
    url = _chat_completions_url(str(cfg.get("baseUrl") or ""))
    model = str(cfg.get("model") or "").strip()
    if not model:
        raise ValueError("请先填写大模型名称（Model）")
    headers = _build_request_headers(cfg, url)
    headers["Accept"] = "text/event-stream, application/json"

    payload = {
        "model": model,
        "temperature": float(cfg.get("temperature") or 0.3),
        "stream": True,
        "messages": messages_override or [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        resp = urllib.request.urlopen(req, timeout=idle_timeout)
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="ignore")[:300]
        raise RuntimeError(f"大模型接口返回 HTTP {exc.code}：{err_body}") from exc
    except Exception as exc:
        raise RuntimeError(f"连接大模型接口失败：{exc}") from exc

    resolved_model = model
    ctype = (resp.headers.get("Content-Type") or "").lower()

    try:
        # 兼容极少数忽略 stream:true、直接返回整段 JSON 的网关
        if "application/json" in ctype and "text/event-stream" not in ctype:
            raw = resp.read().decode("utf-8", errors="replace")
            obj = json.loads(raw)
            resolved_model = str(obj.get("model") or model)
            choices = obj.get("choices") or []
            msg = (choices[0].get("message") or {}) if choices else {}
            r_txt = _extract_delta_text(msg.get("reasoning_content") or msg.get("reasoning"))
            c_txt = _extract_delta_text(msg.get("content"))
            if r_txt:
                yield {"type": "reasoning", "delta": r_txt, "model": resolved_model}
            if c_txt:
                yield {"type": "delta", "delta": c_txt, "model": resolved_model}
            return

        for raw_line in resp:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                continue
            data_str = line[5:].strip()
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
            except Exception:
                continue
            if chunk.get("model"):
                resolved_model = str(chunk["model"])
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or choices[0].get("message") or {}
            r_txt = _extract_delta_text(delta.get("reasoning_content") or delta.get("reasoning"))
            c_txt = _extract_delta_text(delta.get("content"))
            if r_txt:
                yield {"type": "reasoning", "delta": r_txt, "model": resolved_model}
            if c_txt:
                yield {"type": "delta", "delta": c_txt, "model": resolved_model}
            if choices[0].get("finish_reason") in ("stop", "length", "content_filter"):
                break
    except Exception as exc:
        raise RuntimeError(f"流式读取大模型中断：{exc}") from exc
    finally:
        try:
            resp.close()
        except Exception:
            pass


def _stream_chat_step(
    cfg: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    idle_timeout: float = 120.0,
) -> Iterator[dict[str, Any]]:
    """支持工具调用的原生 SSE 流式单轮调用：实时推送 reasoning / delta，并在结束时产出 step_done（含完整 tool_calls）。"""
    url = _chat_completions_url(str(cfg.get("baseUrl") or ""))
    model = str(cfg.get("model") or "").strip()
    if not model:
        raise ValueError("请先填写大模型名称（Model）")
    headers = _build_request_headers(cfg, url)
    headers["Accept"] = "text/event-stream, application/json"

    payload: dict[str, Any] = {
        "model": model,
        "temperature": float(cfg.get("temperature") or 0.3),
        "stream": True,
        "messages": messages,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        resp = urllib.request.urlopen(req, timeout=idle_timeout)
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="ignore")[:300]
        raise RuntimeError(f"大模型接口返回 HTTP {exc.code}：{err_body}") from exc
    except Exception as exc:
        raise RuntimeError(f"连接大模型接口失败：{exc}") from exc

    resolved_model = model
    ctype = (resp.headers.get("Content-Type") or "").lower()
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_acc: dict[int, dict[str, Any]] = {}

    def _assemble_tools() -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for idx in sorted(tool_acc.keys()):
            item = tool_acc[idx]
            fn = item.get("function") or {}
            if not fn.get("name"):
                continue
            out.append({
                "id": item.get("id") or f"call_{idx}_{fn.get('name')}",
                "type": item.get("type") or "function",
                "function": {
                    "name": str(fn.get("name") or ""),
                    "arguments": "".join(fn.get("arg_parts") or []),
                },
            })
        return out

    try:
        if "application/json" in ctype and "text/event-stream" not in ctype:
            raw = resp.read().decode("utf-8", errors="replace")
            obj = json.loads(raw)
            resolved_model = str(obj.get("model") or model)
            choices = obj.get("choices") or []
            msg = (choices[0].get("message") or {}) if choices else {}
            r_txt = _extract_delta_text(msg.get("reasoning_content") or msg.get("reasoning"))
            c_txt = _extract_delta_text(msg.get("content"))
            tcalls = msg.get("tool_calls") or []
            if r_txt:
                reasoning_parts.append(r_txt)
                yield {"type": "reasoning", "delta": r_txt, "model": resolved_model}
            if c_txt:
                text_parts.append(c_txt)
                if not tcalls:
                    yield {"type": "delta", "delta": c_txt, "model": resolved_model}
            yield {
                "type": "step_done",
                "model": resolved_model,
                "text": "".join(text_parts).strip(),
                "reasoning": "".join(reasoning_parts).strip(),
                "tool_calls": tcalls if isinstance(tcalls, list) else [],
            }
            return

        for raw_line in resp:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                continue
            data_str = line[5:].strip()
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
            except Exception:
                continue
            if chunk.get("model"):
                resolved_model = str(chunk["model"])
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or choices[0].get("message") or {}
            r_txt = _extract_delta_text(delta.get("reasoning_content") or delta.get("reasoning"))
            if r_txt:
                reasoning_parts.append(r_txt)
                yield {"type": "reasoning", "delta": r_txt, "model": resolved_model}

            tc_deltas = delta.get("tool_calls")
            if isinstance(tc_deltas, list) and tc_deltas:
                for pos, tcd in enumerate(tc_deltas):
                    if not isinstance(tcd, dict):
                        continue
                    idx = int(tcd.get("index")) if isinstance(tcd.get("index"), int) else pos
                    slot = tool_acc.setdefault(
                        idx,
                        {"id": "", "type": "function", "function": {"name": "", "arg_parts": []}},
                    )
                    if tcd.get("id"):
                        slot["id"] = str(tcd["id"])
                    if tcd.get("type"):
                        slot["type"] = str(tcd["type"])
                    fn_d = tcd.get("function") or {}
                    if isinstance(fn_d, dict):
                        if fn_d.get("name"):
                            slot["function"]["name"] += str(fn_d["name"])
                        if fn_d.get("arguments"):
                            slot["function"]["arg_parts"].append(str(fn_d["arguments"]))

            c_txt = _extract_delta_text(delta.get("content"))
            if c_txt:
                text_parts.append(c_txt)
                if not tool_acc:
                    yield {"type": "delta", "delta": c_txt, "model": resolved_model}

            if choices[0].get("finish_reason") in ("stop", "length", "content_filter", "tool_calls"):
                break

        yield {
            "type": "step_done",
            "model": resolved_model,
            "text": "".join(text_parts).strip(),
            "reasoning": "".join(reasoning_parts).strip(),
            "tool_calls": _assemble_tools(),
        }
    except Exception as exc:
        raise RuntimeError(f"流式读取大模型中断：{exc}") from exc
    finally:
        try:
            resp.close()
        except Exception:
            pass


def build_target_digest(
    profile: dict[str, Any],
    *,
    patch_dict: dict[str, Any] | None = None,
    compact_for_copy: bool = False,
) -> dict[str, Any]:
    """把目标 BD 压缩为高信噪比「带官方原文」事实包（保留 Ward/LevelReq/天赋与宝石原文/Config/消融/未解析词条）。"""
    build = profile.get("build") or {}
    char = build.get("character") or {}
    tree = build.get("passiveTree") or {}
    pos = profile.get("positioning") or {}
    sim = profile.get("simulation")
    facts = profile.get("engineFacts") or {}
    pdict = patch_dict if patch_dict is not None else patch_dict_updater.load_patch_dict()

    def compact_item(it: dict[str, Any]) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "slot": it.get("slot"),
            "rarity": it.get("rarity"),
            "name": it.get("name"),
            "baseType": it.get("baseType"),
            "mods": (it.get("mods") or [])[:12],
        }
        if it.get("itemLevel"):
            entry["itemLevel"] = it["itemLevel"]
        if it.get("levelReq"):
            entry["levelReq"] = it["levelReq"]
        if it.get("socketCount"):
            entry["socketCount"] = int(it["socketCount"])
        if it.get("corrupted"):
            entry["corrupted"] = True
        props = it.get("properties")
        if isinstance(props, dict) and props:
            entry["properties"] = props
        runes = it.get("socketedRunes") or []
        if runes:
            entry["socketedRunes"] = list(runes)
        # 附加深度词条分析出的天赋联动 / 符文孔位上限 / 专属词池 / 特殊基底提示
        try:
            deep = affix_analyzer.analyze_item_deeply(it, build)
            hints: list[str] = []
            if deep.get("isSpeedWeapon"):
                hints.append("攻速/位移工具武器组")
            if deep.get("requireBaseType") and deep.get("baseTypeReason"):
                hints.append(f"必保基底: {deep['baseTypeReason']}")
            if deep.get("passiveSynergies"):
                hints.append(f"天赋联动: {', '.join(deep['passiveSynergies'][:3])}")
            sa = deep.get("socketAnalysis") or {}
            if sa.get("exceedsCraftableSockets"):
                hints.append(f"超工匠石孔位上限({sa['socketCount']}/{sa['naturalMaxSockets']}孔，需卓越底子或瓦尔多槽)")
            if sa.get("hasSocketEffectMod"):
                hints.append("含镶嵌物效果增幅(必须配多孔与高阶符文)")
            if sa.get("runeUnlockedPools"):
                hints.append(f"符文解锁专属词池: {'/'.join(sa['runeUnlockedPools'])}")
            if hints:
                entry["synergyHints"] = hints
        except Exception:
            pass
        return entry

    def compact_node(n: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {"name": n.get("name")}
        stats = n.get("stats") or []
        if stats:
            out["stats"] = stats[:4]
        return out

    # 技能组绝不截断主技能，同时从词典补全每个独特主动/辅助宝石的官方说明与关键数值
    raw_groups = list(build.get("skillGroups") or [])
    raw_groups.sort(key=lambda sg: (0 if sg.get("isMain") else 1, int(sg.get("index") or 999)))
    seen_primary_names: set[str] = set()
    compact_groups: list[dict[str, Any]] = []
    gem_glossary: dict[str, dict[str, Any]] = {}
    unknown_gems: list[str] = []

    for sg in raw_groups:
        gems = sg.get("gems") or []
        if not gems:
            continue
        first_name = (gems[0].get("name") or "").strip().lower()
        is_single_mirror = len(gems) == 1 and not sg.get("isMain") and first_name in seen_primary_names
        if is_single_mirror:
            continue
        if first_name:
            seen_primary_names.add(first_name)
        def _fmt_gem_entry(g: dict[str, Any]) -> str:
            extras: list[str] = []
            if g.get("count"):
                extras.append(f"×{g['count']}")
            if g.get("skillStageCount"):
                extras.append(f"stage={g['skillStageCount']}")
            if g.get("skillPart"):
                extras.append(f"part={g['skillPart']}")
            suffix = ("," + ",".join(extras)) if extras else ""
            return f"{g.get('name')}({g.get('level')}/{g.get('quality')}%{suffix})"

        group_entry: dict[str, Any] = {
            "isMain": bool(sg.get("isMain")),
            "weaponSet": sg.get("weaponSet") or "",
            "gems": [_fmt_gem_entry(g) for g in gems],
        }
        if sg.get("includeInFullDPS"):
            group_entry["includeInFullDPS"] = True
        compact_groups.append(group_entry)
        for g in gems:
            gname = (g.get("name") or "").strip()
            if not gname or gname in gem_glossary:
                continue
            if compact_for_copy and len(gem_glossary) >= 18:
                continue
            info = patch_dict_updater.lookup_gem_info(gname, pdict)
            if info:
                desc_txt = str(info.get("d") or "")
                max_desc = 110 if compact_for_copy else 200
                if len(desc_txt) > max_desc:
                    desc_txt = desc_txt[: max_desc - 3] + "..."
                g_out: dict[str, Any] = {"desc": desc_txt}
                if info.get("c"):
                    g_out["stats"] = info["c"][:4] if compact_for_copy else info["c"]
                gem_glossary[gname] = g_out
            elif gname not in unknown_gems:
                unknown_gems.append(gname)
        if len(compact_groups) >= 16:
            break

    # 紧凑面板：优先使用实算面板，否则仅提取核心 XML 面板项，避免塞入上百条冗余数字
    if sim:
        stats_compact = dict(sim)
    else:
        xml_s = char.get("stats") or {}
        keep_keys = (
            "CombinedDPS", "TotalDPS", "FullDPS", "AverageDamage", "Speed",
            "CritChance", "PreEffectiveCritChance", "CritMultiplier", "CritBifurcates",
            "TotalEHP", "Life", "LifeUnreserved", "EnergyShield", "Ward", "Mana",
            "Spirit", "SpiritUnreserved", "Evasion", "EvadeChance", "DeflectChance",
            "DeflectionRating", "Armour", "EffectiveBlockChance",
            "FireResist", "ColdResist", "LightningResist", "ChaosResist",
            "PhysicalMaximumHitTaken", "ChaosMaximumHitTaken",
            "EffectiveMovementSpeedMod", "ManaCost", "ManaPerSecondCost", "ManaRegenRecovery",
            "Str", "Dex", "Int", "ReqStr", "ReqDex", "ReqInt",
        )
        stats_compact = {k: round(float(xml_s[k]), 2) for k in keep_keys if k in xml_s}

    all_notable_names = [n.get("name") for n in (tree.get("notables") or []) if n.get("name")]

    digest: dict[str, Any] = {
        "character": {
            "class": char.get("className"),
            "ascendancy": char.get("ascendancy"),
            "level": char.get("level"),
            "archetype": pos.get("archetype"),
            "mainSkillName": facts.get("mainSkillName") or (sim or {}).get("mainSkillName") or "",
            "mainSkillLevel": facts.get("mainSkillLevel") or (sim or {}).get("mainSkillLevel") or 0,
            "activeTotemLimit": facts.get("activeTotemLimit") or (sim or {}).get("activeTotemLimit") or 0,
            "activeWeaponSet": (build.get("weaponSets") or {}).get("activeWeaponSet") or facts.get("activeWeaponSet") or 1,
        },
        "stats": stats_compact,
        "config": build.get("config") or {},
        "passiveTree": {
            "treeVersion": tree.get("treeVersion") or "",
            "totalAllocated": tree.get("totalAllocated"),
            "keystones": [compact_node(n) for n in (tree.get("keystones") or [])],
            "ascendancyNodes": [compact_node(n) for n in (tree.get("ascendancyNodes") or [])],
            "anointedNotables": tree.get("anointedNotables") or [],
            "weaponSet1Notables": [compact_node(n) for n in (tree.get("weaponSet1Notables") or [])],
            "weaponSet2Notables": [compact_node(n) for n in (tree.get("weaponSet2Notables") or [])],
            "notables": all_notable_names,
        },
        "equippedItems": [compact_item(it) for it in (build.get("equippedItems") or [])],
        "jewels": [compact_item(j) for j in (build.get("jewels") or [])],
        "flasksAndCharms": [compact_item(f) for f in (build.get("flasksAndCharms") or [])],
        "skillGroups": compact_groups,
        "gemGlossary": gem_glossary,
    }
    if unknown_gems:
        digest["unknownGemsInDict"] = unknown_gems

    if facts:
        non_empty_skills = [
            s for s in (facts.get("allSkillsDps") or [])
            if s.get("isMain")
            or s.get("isTravel")
            or float(s.get("dps") or 0) > 0
            or float(s.get("avgHit") or 0) > 0
            or float(s.get("speedWs1") or 0) > 0
            or float(s.get("speedWs2") or 0) > 0
            or float(s.get("cooldown") or 0) > 0
        ]
        digest["engineFacts"] = {
            "metricsVersion": facts.get("metricsVersion") or 1,
            "effectiveDps": facts.get("effectiveDps"),
            "totalTotemDps": facts.get("totalTotemDps"),
            "dpsSource": facts.get("dpsSource"),
            "skillMechanics": facts.get("skillMechanics") or {},
            "mobility": facts.get("mobility") or {},
            "offense": facts.get("offense") or {},
            "defense": facts.get("defense") or {},
            "sustain": facts.get("sustain") or {},
            "pobAssumptions": facts.get("pobAssumptions") or {},
            "weaponSetStats": facts.get("weaponSetStats") or {},
            "allSkillsDps": non_empty_skills[:14],
            "spiritAudit": facts.get("spiritAudit") or [],
            "itemImpacts": [
                {
                    "slot": x.get("slot"),
                    "name": x.get("name"),
                    "dpsLossPct": x.get("dpsLossPct"),
                    "ehpLossPct": x.get("ehpLossPct"),
                    "spiritLoss": x.get("spiritLoss"),
                    "maxResDrop": x.get("maxResDrop"),
                    "attrBroken": bool(x.get("attrBroken")),
                    "note": x.get("note") or "",
                }
                for x in (facts.get("itemImpacts") or [])
            ],
            "unparsedMods": (facts.get("unparsedMods") or [])[:30],
        }

    dossier = profile.get("ablationDossier")
    asc_ablation = (
        dossier.get("ascendancy")
        if isinstance(dossier, dict) and isinstance(dossier.get("ascendancy"), dict)
        else (profile.get("ascendancyAblation") if isinstance(profile.get("ascendancyAblation"), dict) else None)
    )
    if asc_ablation:
        digest["ascendancyAblation"] = {
            "ascendancy": asc_ablation.get("ascendancy"),
            "whyChosenSummary": asc_ablation.get("whyChosenSummary"),
            "totalImpact": asc_ablation.get("totalImpact") or {},
            "nodes": [
                {
                    "name": n.get("name"),
                    "role": n.get("roleLabel"),
                    "importanceTier": n.get("importanceTier"),
                    "isolated": n.get("isolated") or {},
                    "branch": n.get("branch") or {},
                    "unlocksNotables": n.get("unlocksNotables") or [],
                    "grantedSkill": n.get("grantedSkill") or "",
                    "selectionReason": n.get("selectionReason") or "",
                }
                for n in (asc_ablation.get("nodes") or [])[:8]
            ],
        }

    if isinstance(dossier, dict):
        core_socketed: list[dict[str, Any]] = []
        for x in list(dossier.get("items") or []):
            ri = x.get("runeImpact") if isinstance(x.get("runeImpact"), dict) else {}
            for si in ri.get("socketedItems") or []:
                if not isinstance(si, dict):
                    continue
                if (
                    si.get("tier") == "must"
                    or float(si.get("dpsLossPct") or 0) >= 2.0
                    or float(si.get("ehpLossPct") or 0) >= 3.0
                    or float(si.get("maxResDrop") or 0) >= 10.0
                    or si.get("grantsKeystone")
                    or si.get("isUnparsed")
                ):
                    core_socketed.append({
                        "slot": x.get("slot"),
                        "item": x.get("name"),
                        "name": si.get("name"),
                        "type": si.get("runeTypeLabel") or si.get("runeType"),
                        "purpose": si.get("purposeLabel"),
                        "dpsLossPct": si.get("dpsLossPct"),
                        "ehpLossPct": si.get("ehpLossPct"),
                        "maxResDrop": si.get("maxResDrop"),
                        "grantsKeystone": si.get("grantsKeystone") or "",
                        "effects": (si.get("effects") or [])[:2],
                        "bondedActive": bool(si.get("bondedActive")),
                        "estDivine": si.get("estimatedDivine"),
                    })
        digest["ablationLedger"] = {
            "totalEstimatedDivine": dossier.get("totalEstimatedDivine"),
            "copyFoundation": dossier.get("copyFoundation") or {},
            "skillDefenseSummary": (dossier.get("skillDefenseSummary") or [])[:8],
            "coreSocketedItems": core_socketed[:12],
            "skillImpacts": [
                {
                    "mainGem": s.get("mainGemName"),
                    "isMain": bool(s.get("isMain")),
                    "role": s.get("roleLabel"),
                    "dpsLossPct": s.get("dpsLossPct"),
                    "ehpLossPct": s.get("ehpLossPct"),
                    "physMaxHitLossPct": s.get("physMaxHitLossPct"),
                    "maxResDrop": s.get("maxResDrop"),
                    "evadeDrop": s.get("evadeDrop"),
                    "deflectDrop": s.get("deflectDrop"),
                    "lifeRecoveryDrop": s.get("lifeRecoveryDrop"),
                    "spiritSaved": s.get("spiritSaved"),
                    "defenseLayers": (s.get("defenseAnalysis") or {}).get("defenseLayers") or [],
                    "maxDowngrade2020LossPct": s.get("maxDowngrade2020LossPct"),
                    "topSupports": [
                        {
                            "name": g.get("name"),
                            "dpsLossPct": g.get("dpsLossPct"),
                            "ehpLossPct": g.get("ehpLossPct"),
                            "lifeRecoveryDrop": g.get("lifeRecoveryDrop"),
                            "defenseLayers": g.get("defenseLayers") or [],
                            "role": g.get("roleLabel"),
                            "dg2020LossPct": g.get("downgrade2020DpsLossPct"),
                        }
                        for g in (s.get("gems") or [])
                        if g.get("isSupport")
                    ][:5],
                }
                for s in (dossier.get("skills") or [])[:12]
            ],
            "itemAffixPurposes": [
                {
                    "slot": x.get("slot"),
                    "name": x.get("name"),
                    "role": x.get("roleLabel"),
                    "copyTier": x.get("copyTier"),
                    "estDivine": (x.get("cost") or {}).get("estimatedDivine"),
                    "dpsLossPct": x.get("compensatedDpsLossPct") if x.get("compensatedDpsLossPct") is not None else x.get("dpsLossPct"),
                    "ehpLossPct": x.get("compensatedEhpLossPct") if x.get("compensatedEhpLossPct") is not None else x.get("ehpLossPct"),
                    "mustAffixes": [
                        f"{a.get('line')} [{a.get('purposeLabel')}]"
                        for a in (x.get("affixes") or [])
                        if a.get("tier") == "must"
                    ][:4],
                    "socketedSummary": [
                        f"{si.get('name')} ({si.get('purposeLabel')})"
                        for si in ((x.get("runeImpact") or {}).get("socketedItems") or [])
                    ][:4],
                    "cuttableCount": (x.get("affixStats") or {}).get("cut", 0) + (x.get("affixStats") or {}).get("dead", 0),
                }
                for x in (list(dossier.get("items") or []) + list(dossier.get("jewels") or []))[:18]
            ],
        }

    rev_spec = (
        dossier.get("reverseSpec")
        if isinstance(dossier, dict) and isinstance(dossier.get("reverseSpec"), dict)
        else (profile.get("reverseSpec") if isinstance(profile.get("reverseSpec"), dict) else None)
    )
    if isinstance(rev_spec, dict) and rev_spec.get("slotSpecs"):
        compact_slots: dict[str, Any] = {}
        for s_name, s_val in (rev_spec.get("slotSpecs") or {}).items():
            if not isinstance(s_val, dict) or s_val.get("rarity") == "UNIQUE":
                continue
            compact_slots[s_name] = {
                "category": s_val.get("allowedCategory"),
                "mustLockBase": bool(s_val.get("mustLockBase")),
                "panelRole": s_val.get("panelRole"),
                "weaponArchetype": s_val.get("weaponArchetype"),
                "targetPanels": s_val.get("targetPanels") or {},
                "recommendedBases": [
                    {
                        "baseType": b.get("baseType"),
                        "avgHit": b.get("avgHit"),
                        "apsBase": b.get("apsBase"),
                        "critBase": b.get("critBase"),
                        "baseDps": b.get("baseDps"),
                        "reason": b.get("reason"),
                    }
                    for b in (s_val.get("recommendedBases") or [])[:3]
                ],
                "panelEquivalence": s_val.get("panelEquivalence"),
                "mandatoryHard": s_val.get("mandatoryHard") or [],
                "mandatoryAnyOf": s_val.get("mandatoryAnyOf") or [],
                "origOffScore": s_val.get("originalOffenseScore"),
                "origDefScore": s_val.get("originalDefenseScore"),
                "offensePool": [
                    {
                        "statId": t.get("statId"),
                        "zh": t.get("zh"),
                        "weight": t.get("weight"),
                        "onOrig": bool(t.get("onOriginalItem")),
                    }
                    for t in (s_val.get("offensePool") or [])[:8]
                ],
                "defensePool": [
                    {
                        "statId": t.get("statId"),
                        "zh": t.get("zh"),
                        "weight": t.get("weight"),
                        "isResPool": bool(t.get("isResPool")),
                    }
                    for t in (s_val.get("defensePool") or [])[:6]
                ],
            }
        digest["reverseBuildSpec"] = {
            "statSensitivity": [
                {
                    "id": s.get("id"),
                    "zh": s.get("zh"),
                    "tradeEn": s.get("tradeEn"),
                    "dpsPerUnit": s.get("dpsPctPerUnit"),
                    "ehpPerUnit": s.get("ehpPctPerUnit"),
                }
                for s in (rev_spec.get("statSensitivity") or [])[:18]
            ],
            "deadProbeIds": (rev_spec.get("deadProbeIds") or [])[:12],
            "weaponPanelAudit": rev_spec.get("weaponPanelAudit") or {},
            "globalResourceBudget": rev_spec.get("globalResourceBudget") or {},
            "slotSpecs": compact_slots,
        }
    return digest


def build_gap_digest(gap_report: dict[str, Any]) -> dict[str, Any]:
    return {
        "mySummary": gap_report.get("mySummary"),
        "targetSummary": gap_report.get("targetSummary"),
        "myStats": gap_report.get("myStats"),
        "targetStats": gap_report.get("targetStats"),
        "myRedlines": gap_report.get("myRedlines"),
        "treeDiff": gap_report.get("treeDiff"),
        "skillsDiff": gap_report.get("skillsDiff"),
        "zeroCostSteps": gap_report.get("zeroCostSteps"),
        "topSlotAndJewelUpgrades": [
            {
                "slot": u.get("slot"),
                "myItem": u.get("myItemName"),
                "targetItem": u.get("targetItemName"),
                "deltaDpsPct": u.get("deltaDpsPct"),
                "deltaEhpPct": u.get("deltaEhpPct"),
                "priceDivine": u.get("priceDivine"),
                "redlines": [r.get("message") for r in (u.get("redlines") or [])],
            }
            for u in (gap_report.get("slotUpgrades") or [])[:16]
        ],
    }


_SYSTEM_PROMPT = (
    "你是《流放之路 2》(Path of Exile 2) 资深 BD 构筑师与做装顾问。"
    "你必须严格基于下方提供的 PoB2 解析数据、官方天赋与宝石原文说明（gemGlossary / passiveTree.stats）、"
    "装备原始属性（含 Ward / Spirit / LevelReq / socketCount / 符文 / synergyHints）、Config 配置以及可选的 PoB2 引擎实算/消融/unparsedMods 进行推理，"
    "严禁臆造不存在的装备、珠宝或机制。若某词条标注在 unknownGemsInDict 中且无法确定，请明确提示需游戏内核实。"
    "分析装备词条、推荐平替与半成品时必须深挖两类联动："
    "1) 词条↔天赋/辅助联动（如 Subterfuge Mask 头盔ES转闪避、Spectral Ward/Beastial Skin 胸甲高闪避、Lead me through Grace 胸甲防值转精魂且禁用装备精魂、Crimson Power 胸甲ES转血、Feathered Fletching/投射物加速 投速转伤害、Falcon Dive/亚马逊 命中转攻速暴击、CI/血魔法/必中/火之化身 剔除死词条、攻速工具武器砍低T点伤）；"
    "2) 词条↔符文插槽联动（如单手/头手鞋2孔或胸甲/双手3孔超工匠石上限必须买卓越底子或瓦尔成品、镶嵌物效果增幅词条必须配多孔+高阶符文/灵核、Kolr's Hunt/Thrud's Might 等符文解锁 Marksman/Destruction 专属词池、是否点了 Wisdom of the Maji 决定 Bonded 词条是否生效、买完平替/半成品需补插哪颗符文）；"
    "3) 基底自带技能与传奇装备边界：Absent/Lament/Portent Amulet、Shrine/Stoic/Omen/Rattling Sceptre、Chiming Staff 等特殊基底的 Grants Skill 属于核心功能，少了会直接丢技能/光环/精魂槽，平替必须锁基底；传奇/暗金装备（UNIQUE）不可用通货/精华/半成品重做词缀，仅能直买成品（按预算挑 roll、买瓦尔额外孔或瓦尔基底、自行打满工匠孔插符文、项链可自涂油），严禁对传奇装备给出复杂做装或半成品配方。"
    "同时严格区分「真短板」与「PoB 导出假红线」。"
)

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S | re.I)


def _cache_path(state_dir: str = STATE_DIR) -> str:
    return os.path.join(state_dir, LLM_CACHE_FILENAME)


def _digest_cache_key(mode: str, digest: dict[str, Any], cfg: dict[str, Any]) -> str:
    payload = {
        "v": _STRUCTURED_PROMPT_VERSION,
        "mode": mode,
        "provider": str(cfg.get("provider") or ""),
        "model": str(cfg.get("model") or ""),
        "digest": digest,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _load_cache_entry(key: str, state_dir: str = STATE_DIR) -> dict[str, Any] | None:
    path = _cache_path(state_dir)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get(key), dict):
            return dict(data[key])
    except Exception:
        return None
    return None


def _save_cache_entry(key: str, value: dict[str, Any], state_dir: str = STATE_DIR) -> None:
    path = _cache_path(state_dir)
    data: dict[str, Any] = {}
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}
    data[key] = {**value, "cachedAt": int(time.time())}
    if len(data) > _CACHE_MAX_ENTRIES:
        ordered = sorted(data.items(), key=lambda kv: int((kv[1] or {}).get("cachedAt") or 0))
        data = dict(ordered[-_CACHE_MAX_ENTRIES:])
    try:
        os.makedirs(state_dir, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        pass


def _extract_json_object(text: str) -> dict[str, Any] | None:
    s = (text or "").strip()
    if not s:
        return None
    candidates: list[str] = []
    m = _JSON_BLOCK_RE.search(s)
    if m:
        candidates.append(m.group(1))
    if s.startswith("{") and s.endswith("}"):
        candidates.append(s)
    else:
        first, last = s.find("{"), s.rfind("}")
        if first >= 0 and last > first:
            candidates.append(s[first : last + 1])
    for cand in candidates:
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue
    return None


def split_structured_header_and_markdown(raw_text: str) -> tuple[dict[str, Any] | None, str]:
    """解析「```json ... ``` + --- + Markdown 正文」协议；也向后兼容旧版单块 JSON（含 analysisMarkdown）。"""
    s = (raw_text or "").strip()
    if not s:
        return None, ""

    m = _JSON_BLOCK_RE.search(s)
    if m:
        parsed_obj = None
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                parsed_obj = obj
        except Exception:
            parsed_obj = None
        after = s[m.end() :].lstrip()
        if after.startswith("---"):
            after = after[3:].lstrip()
        if parsed_obj is not None:
            sanitized = _sanitize_structured_target(parsed_obj)
            md = after or sanitized.get("analysisMarkdown") or ""
            return sanitized, md.strip()

    fallback_obj = _extract_json_object(s)
    if fallback_obj is not None:
        sanitized = _sanitize_structured_target(fallback_obj)
        md = sanitized.get("analysisMarkdown") or s
        return sanitized, md.strip()
    return None, s


def _sanitize_structured_target(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in ("archetype", "oneLineIdea", "defenseSystem", "analysisMarkdown"):
        v = raw.get(k)
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()

    for list_k, limit in (("scenarioTags", 6), ("coreAffixes", 7), ("pitfalls", 6)):
        v = raw.get(list_k)
        if isinstance(v, list):
            cleaned = [str(x).strip() for x in v if str(x).strip()]
            if cleaned:
                out[list_k] = list(dict.fromkeys(cleaned))[:limit]

    bt = str(raw.get("budgetTier") or "").strip().lower()
    if bt in _VALID_BUDGET_TIERS:
        out["budgetTier"] = bt

    gt = raw.get("gearTiers")
    if isinstance(gt, dict):
        tiers_clean: dict[str, list[str]] = {}
        for tk in ("mandatory", "amplifier", "luxury"):
            arr = gt.get(tk)
            if isinstance(arr, list):
                tiers_clean[tk] = list(dict.fromkeys(str(x).strip() for x in arr if str(x).strip()))[:10]
            else:
                tiers_clean[tk] = []
        if any(tiers_clean.values()):
            out["gearTiers"] = tiers_clean

    gear_notes = raw.get("coreGearNotes")
    if isinstance(gear_notes, dict):
        cleaned_notes = {
            str(k).strip(): str(v).strip()
            for k, v in gear_notes.items()
            if str(k).strip() and str(v).strip()
        }
        if cleaned_notes:
            out["coreGearNotes"] = cleaned_notes

    cr = raw.get("contentReadiness")
    if isinstance(cr, dict):
        cr_out: dict[str, Any] = {}
        if isinstance(cr.get("overallTier"), str) and cr["overallTier"].strip():
            cr_out["overallTier"] = cr["overallTier"].strip()
        og = str(cr.get("overallGrade") or "").strip().upper()
        if og in _VALID_GRADES:
            cr_out["overallGrade"] = og
        if isinstance(cr.get("summary"), str) and cr["summary"].strip():
            cr_out["summary"] = cr["summary"].strip()
        items_raw = cr.get("items")
        if isinstance(items_raw, list):
            by_id: dict[str, dict[str, str]] = {}
            for item in items_raw:
                if not isinstance(item, dict):
                    continue
                iid = str(item.get("id") or "").strip()
                g = str(item.get("grade") or "").strip().upper()
                if iid in _VALID_CONTENT_IDS and g in _VALID_GRADES:
                    by_id[iid] = {
                        "id": iid,
                        "label": str(item.get("label") or "").strip(),
                        "grade": g,
                        "verdict": str(item.get("verdict") or "").strip(),
                        "reason": str(item.get("reason") or "").strip(),
                    }
            if by_id:
                cr_out["itemsById"] = by_id
        if radar_raw := cr.get("radar"):
            if isinstance(radar_raw, list):
                radar_by_id: dict[str, dict[str, Any]] = {}
                for r in radar_raw:
                    if not isinstance(r, dict):
                        continue
                    rid = str(r.get("id") or "").strip()
                    if rid not in _RADAR_AXIS_LABELS:
                        continue
                    try:
                        sc = int(round(float(r.get("score"))))
                    except (TypeError, ValueError):
                        continue
                    sc = max(0, min(100, sc))
                    radar_by_id[rid] = {
                        "id": rid,
                        "label": str(r.get("label") or "").strip() or _RADAR_AXIS_LABELS[rid],
                        "score": sc,
                        "metric": str(r.get("metric") or "").strip()[:48],
                        "reason": str(r.get("reason") or "").strip()[:140],
                    }
                if radar_by_id:
                    cr_out["radar"] = [radar_by_id[rid] for rid in _VALID_RADAR_IDS if rid in radar_by_id]
        av = cr.get("ascendancyVerdict")
        if isinstance(av, dict) and (str(av.get("verdict") or "").strip() or isinstance(av.get("keyReasons"), list)):
            kr = [str(x).strip()[:160] for x in (av.get("keyReasons") or []) if str(x).strip()][:5]
            cr_out["ascendancyVerdict"] = {
                "ascendancy": str(av.get("ascendancy") or "").strip()[:48],
                "verdict": str(av.get("verdict") or "").strip()[:220],
                "keyReasons": kr,
                "tradeoff": str(av.get("tradeoff") or "").strip()[:180],
            }
        if cr_out:
            out["contentReadiness"] = cr_out

    return out


def merge_ai_positioning(
    base_positioning: dict[str, Any],
    structured: dict[str, Any],
    *,
    model: str = "",
    engine_metrics_summary: str = "",
) -> dict[str, Any]:
    """将校验后的 AI 结构化理解无损融合进 BuildPositioning；评级与六维雷达图完全由 AI 结合 PoB2 引擎实算数据产出。"""
    merged = dict(base_positioning)
    for k in ("archetype", "oneLineIdea", "defenseSystem", "scenarioTags", "budgetTier", "coreAffixes", "pitfalls", "gearTiers"):
        if structured.get(k):
            merged[k] = structured[k]

    ai_cr = structured.get("contentReadiness")
    if isinstance(ai_cr, dict) and (ai_cr.get("overallGrade") or ai_cr.get("itemsById") or ai_cr.get("radar")):
        prev_cr = merged.get("contentReadiness") if isinstance(merged.get("contentReadiness"), dict) else {}
        prev_by_id = {
            str(it.get("id") or ""): it
            for it in (prev_cr.get("items") or [])
            if isinstance(it, dict) and it.get("id")
        }
        by_id = ai_cr.get("itemsById") or {}
        items_out: list[dict[str, Any]] = []
        for cid in _VALID_CONTENT_IDS:
            ov = by_id.get(cid)
            prev_it = prev_by_id.get(cid)
            label_zh, label_en = _CONTENT_READINESS_META[cid]
            if ov:
                g = ov["grade"]
                v_zh, v_en = _DEFAULT_GRADE_VERDICT.get(g, ("可打", "Viable"))
                items_out.append({
                    "id": cid,
                    "label": ov.get("label") or label_zh,
                    "grade": g,
                    "verdict": ov.get("verdict") or v_zh,
                    "reason": ov.get("reason") or "",
                    "en": {
                        "label": label_en,
                        "verdict": v_en,
                        "reason": ov.get("reason") or "",
                    },
                })
            elif prev_it:
                items_out.append(prev_it)

        radar_out = ai_cr.get("radar") if isinstance(ai_cr.get("radar"), list) and ai_cr["radar"] else prev_cr.get("radar")

        og = str(ai_cr.get("overallGrade") or prev_cr.get("overallGrade") or "").strip().upper()
        if og not in _VALID_GRADES and items_out:
            og = items_out[0]["grade"]
        if og not in _VALID_GRADES and isinstance(radar_out, list) and radar_out:
            avg_sc = sum(int(x.get("score") or 0) for x in radar_out) / max(1, len(radar_out))
            og = "S" if avg_sc >= 88 else ("A" if avg_sc >= 75 else ("B" if avg_sc >= 60 else ("C" if avg_sc >= 45 else "D")))
        if og in _VALID_GRADES:
            new_cr: dict[str, Any] = {
                "overallTier": str(ai_cr.get("overallTier") or prev_cr.get("overallTier") or f"{og} 级构筑").strip(),
                "overallGrade": og,
                "confidence": "ai",
                "summary": str(ai_cr.get("summary") or prev_cr.get("summary") or "").strip(),
                "items": items_out,
                "evaluatedAt": int(time.time() * 1000),
            }
            if isinstance(radar_out, list) and radar_out:
                new_cr["radar"] = radar_out
            av_out = ai_cr.get("ascendancyVerdict") or prev_cr.get("ascendancyVerdict")
            if isinstance(av_out, dict) and (av_out.get("verdict") or av_out.get("keyReasons")):
                new_cr["ascendancyVerdict"] = av_out
            if model:
                new_cr["model"] = model
            elif prev_cr.get("model"):
                new_cr["model"] = prev_cr["model"]
            if engine_metrics_summary:
                new_cr["engineMetricsSummary"] = engine_metrics_summary
            elif prev_cr.get("engineMetricsSummary"):
                new_cr["engineMetricsSummary"] = prev_cr["engineMetricsSummary"]
            merged["contentReadiness"] = new_cr

    merged["analyzedBy"] = "ai"
    return merged


_RATING_RUBRIC_INSTRUCTIONS = (
    "【PoE2 全难度达标即满分原则 & PoB2 引擎校准硬准则（严禁静态猜测或被单发面板误导，严格对照 PoB2 实算与 TTK/复数/双态魔力/分层防御求解器打分）】\n"
    "★ 一、核心评分哲学——「能力五维饱和满分制，性价比独立拉开差距，彻底取消单点一票否决」：\n"
    "  • 前 5 维实战能力（clearSpeed / bossing / mobility / survivability / sustain）以「同时稳通当前 PoE2 版本全部最高难度内容（8 词 T16、高层/200%迷雾、裂隙/祭坛、单人满难 T4/Uber 顶峰 Boss，并列要求、必须同时满足而非二选一）」为 100 分天花板。\n"
    "  • 只要一套 BD 已经稳定达到或超过全难度天花板（例如：攻坚能在 1~3 秒瞬秒单人 Uber 巅峰甚至轻松打穿 6 人满队 6x 血量 Boss；生存同时扛稳 8 词 T16 且高难赛季玩法不暴毙；清图全屏瞬清零停顿），就必须果断给 **95~100 满分**！严禁搞数值无限内卷，也**严禁因静态蓝耗警告或裸物理一击线而一票否决伤害、清图与总评**。\n"
    "  • 第 6 维「造价性价比（costEfficiency）」是核心决策区分轴：中低造价摸到全难度满分给高分（85~100）；上千 D / 数千 D 镜级严重溢出造价才换来同样全难度满分的，前五维照样给满分，但性价比必须客观压低（15~45）；同时注意识别「元槽位/规则破坏暗金」（Voices / Morior / Runeseeker's Call / Temporalis / Mageblood / The Taming）的槽位扩展与词缀解放价值，不孤立贬低其真实作用。\n\n"
    "★ 二、必须排查的 PoB2 五大经典评估盲区（引擎已预计算并附在数据包中，严禁再犯以下误判）：\n"
    "  1) **单发面板 vs 复数击中/连招总伤**：优先核查 `skillMechanics.multiHitAudit` 与 `offense.ttkAnchors`！若 `pobDisplaysSingleHitOnly = true`（如旋龙 Twister / 电球 Spark / 电球闪电等 PoB2 仅算 1 发单次命中），**严禁拿原始单发低面板判刮痧**，必须按复数波次（0.66s 重置冷却 × 靠墙弹射 + Barrage 独立重复波次 + Whirling Slash 满阶消耗 + Salvo 印记 + Rakiata 反转抗性）后的 `effectiveDpsRepresentative` 与各靶标 TTK（含 6 人满队 Uber ~135M HP、6 人 200% 迷雾 15x 金怪）评判！能轻松打 6 人高难度的 BD，攻坚与清图直接 **95~100 满分**。\n"
    "  2) **节拍蓝耗与双态魔力净值**：优先核查 `sustain.manaSustainAudit`！节拍爆发型（`CADENCE_BURST`，如 3次1级回旋斩→弹幕→龙卷风）不能按满攻速连发算耗蓝；图腾（`AUTONOMOUS_MULTI`）严禁把 Archmage 蓝耗套在图腾自动施法上；刷图必须计入攻击偷蓝 + 击杀回 % 魔力 + Mana Remnants；Boss 战只要 `bossManaVerdict = PASS`（蓝池+药剂可撑 ≥ Boss TTK × 1.5），就是不缺蓝，严禁误报缺蓝扣分！\n"
    "  3) **双武器套位移与精魂独立校验**：机动看 `mobility.travelSkills`（取 Set 1/Set 2 最高有效位移速率，如 1 级回旋斩/暴风乱舞高 APS、Temporalis 零 CD 闪现、闪打、骑鸟移速），**严禁因鞋子 0 移速或步行移速低扣分**；精魂独立看 `spiritUnreservedSet1` 与 `spiritUnreservedSet2`，防止切套短杖造成假超载误判。\n"
    "  4) **分层防御双范式（不拿坦克裸物理线一刀切闪避偏转流）**：优先核查 `defense.defenseAudit`（含 `paradigm`、偏转 40% 减伤后的 `deflectedPhysMaxHit`/`deflectedEleMaxHit`、`wardBuffer`、`deflectionCapProgressPct` 与 `skillAndGearDefenseLayers`）：\n"
    "     • 硬吃坦克/万盾 CI/MoM 范式：看高有效池 + 护甲(含0.5护甲套用元素)/格挡/高最大抗/裸一击线；\n"
    "     • 高规避+偏转+技能恢复+控场速杀范式：高闪避（≥75%）+ 高偏转（Deflect ≥70%~95%，触发直接减伤 40%）+ 鬼舞/风舞者/结界/药水回盾/猎首/法血 + 全屏致盲冰冻与 1~2 秒瞬秒跳过机制，只要池子与抗性扎实且同时稳过 8 词 T16 与高难赛季玩法不暴毙，同样给 **95~100 满分**！\n"
    "  5) **10 大技能形态通用校核（不被单一流派局限）**：弩箭算装填周期 DPS；猛击看前摇/战吼增幅/余震；蓄力看 `stageRampSec` 暖机；DoT 看 `dotSharePct`/中毒叠层上限与地面覆盖；图腾看上限数+放置速度+血量；召唤看上限数+精魂承载+生存；元技能触发看 300 能量阈值/暴击/蓝耗自洽；变身看层数维持成本与净回复。\n\n"
    "★ 三、PoE2 底层物理防幻觉硬约束（推理与写理由时必须遵守）：\n"
    "  • 基础爆伤为 +100%（200%总伤，非PoE1的150%）；元素穿透无法低于 0%；携带反转抗性的 Rakiata's Flow 时严禁同时叠曝晒/降抗诅咒（冲突反降伤）；\n"
    "  • 转伤只吃最终伤害类型；0.5 已移除瞬吸且单资源仅 1 个偷取实例，不可假设单刀瞬吸满血；\n"
    "  • 护甲公式为 AR/(AR+10×DMG) 且 0.5 支持护甲套用元素伤；投射物默认不霰弹（0.66s 重置/Barrage 独立重复/二次范围爆炸除外），返还优先级最低且单体上限 2.0x。\n\n"
    "★ 四、六维 0~100 饱和锚点（每维附 1 条极简 PoB2 实算指标 metric <=20字 + 1 句依据 reason）：\n"
    "1. clearSpeed（清图效率 0~100）：\n"
    "   - 95~100（全难度满分）：8 词 T16 与 200% 迷雾/裂隙（含 6 人高密图）全屏/跨屏瞬清、零停顿（清图覆盖/弹射/连锁/爆破拉满，TTK ≤ 0.5~1s）；\n"
    "   - 78~94：单人 T15~T16 流畅高效清图，偶有补刀或轻微铺垫/装填/插杆间隔；55~77：范围偏窄或节奏慢；<55：漏怪或 T15 刮痧。\n"
    "2. bossing（攻坚爆发 0~100）：以 `offense.ttkAnchors` 为核心锚点（单人 Uber 灰烬 ~22.6M、神性 ~15M、T4 泽什特 ~32M、6人满队 Uber ~135M）：\n"
    "   - 95~100（全难度满分）：单人满难顶峰 TTK ≤ 2~3 秒（有效爆发 ≥ 10M~15M+）或能轻松碾压 6 人满队巅峰 Boss（TTK ≤ 3~6 秒），无压力跳过阶段机制；\n"
    "   - 78~94：单人 T4 稳健击杀（3M~10M 有效 DPS，TTK 3~12s）；55~77：稳通 T0~T2、打 T4 吃力；<55：<1M 或断档严重。\n"
    "3. mobility（机动位移 0~100）：双轨取最优（`max(位移技能APS/CDR, 步行/骑鸟移速)`）：\n"
    "   - 95~100（全难度满分）：极速无缝位移（Set1/Set2 攻速位移技 ≥ 3.5~5+ 次/s，或 Blink CD ≤ 0.5s，或无限闪打，或移速 ≥ +55% 边走边打）；\n"
    "   - 78~94：流畅高机动（位移技 2.0~3.5 次/s 或移速 +35%~+55%）；55~77：常规及格；<55：低移速且无高频位移或硬直极重。\n"
    "4. survivability（生存硬度 0~100）：必须**同时满足**「8 词 T16」与「高难赛季玩法（高层迷雾/裂隙/祭坛/T4 顶峰）」均稳刷不暴毙（并列条件，缺一不可）：\n"
    "   - 95~100（全难度满分）：按其自身防御范式（硬坦/万盾CI/MoM 或 高闪避+高偏转+鬼舞/结界/猎首/控场瞬秒）已同时扛稳 8 词 T16 与高难赛季玩法不暴毙，即给满分不内卷；\n"
    "   - 78~94：仅稳其中之一，极端 8 词多增伤或高波迷雾偶有暴毙风险；55~77：常规 T15 够用但单系抗性/池子有明显短板；<55：脆皮易猝死。\n"
    "5. sustain（续航手感 0~100）：以 `sustain.manaSustainAudit`（双态魔力 PASS/WARN）+ 血/盾/技能端恢复 + 真假精魂/属性红线 + 按键舒适度综合评定：\n"
    "   - 95~100（全难度满分）：刷图态与 Boss 态均 PASS 不断档、技能/药剂恢复闭环自洽、无真红线、循环顺手；\n"
    "   - 78~94：整体顺畅，偶需注意药剂/残片/节拍；55~77：多键繁琐或持久战蓝耗紧绷；<55：真实精魂超载/属性不足/严重断蓝。\n"
    "6. costEfficiency（造价性价比 0~100 — 独立决策轴）：严格权衡 `totalEstimatedDivine` 与核心暗金/多孔底子/血脉宝石门槛：\n"
    "   - 88~100：≤ 30D~80D 即达成强力终局成型；68~87：80D~300D 投入强度线性匹配；45~67：300D~1200D 后期高配档；\n"
    "   - 15~44：1200D~数千 D 镜级/严重溢价溢出档（前五维满但普通玩家不应照单全抄，客观给低分拉开辨识度；未核价则按绑定天价暗金保守给分并注明）。\n"
    "7. 同时给出 5 个终局场景（items）独立分级，每个 reason 必须引用 PoB2 实算与 TTK/防御/续航数据。\n"
    "8. **升华选择原因消融评判（`ascendancyVerdict`）**：必须严格对照 `ascendancyAblation`（整套升华消融 ΔDPS/ΔEHP/Δ一击线/Δ移速 + 每个升华大点的孤立净贡献 vs 分支解锁贡献 + 专属技能/防暴击/降施法减速/品质特效等非面板机制），明确评判该 BD 为何选择这个升华而非其他升华、哪 2~3 个节点最核心、是否存在纯过路前置点或换升华风险！\n"
    "9. **装备插槽物品 & 技能端防御核查**：评估输出与生存时，必须把 `coreSocketedItems`（如头盔插槽遗物赋予 Pain Attunement、胸甲神像+符文 Bonded 联动、高阶灵核）与 `skillDefenseBreakdown`（如 Virtuous Barrier / Ghost Dance / Wind Dancer / Charge Regulation / Purity+Vitality+Cannibalism / Frost Wall / Blind / Knockback）的实算贡献一并计入，严禁只看裸装备词条或漏算技能端防御层！\n"
)


def build_rating_digest(
    profile: dict[str, Any],
    *,
    patch_dict: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """专用于「角色能力评测 (mode=rate)」的高信噪比 7 维数据包：突出 PoB2 全量实算机制 + 升华消融 + 插槽核心物 + 技能防御层。"""
    full = build_target_digest(profile, patch_dict=patch_dict)
    facts = profile.get("engineFacts") or {}
    sim = profile.get("simulation") or {}
    dossier = profile.get("ablationDossier") if isinstance(profile.get("ablationDossier"), dict) else {}
    ledger = full.get("ablationLedger") if isinstance(full.get("ablationLedger"), dict) else {}

    core_uniques = [
        {
            "slot": it.get("slot"),
            "name": it.get("name"),
            "baseType": it.get("baseType"),
            "mods": (it.get("mods") or [])[:6],
            **({"socketedRunes": it["socketedRunes"][:6]} if it.get("socketedRunes") else {}),
        }
        for it in (
            list(full.get("equippedItems") or [])
            + list(full.get("jewels") or [])
            + list(full.get("flasksAndCharms") or [])
        )
        if str(it.get("rarity") or "").upper() == "UNIQUE"
    ][:14]

    # 仅保留关键稀有装备的联动提示与属性（如 Ward/ES/多孔），避免 150 条普通黄装词缀稀释 PoB2 实算焦点
    rare_highlights = [
        {
            "slot": it.get("slot"),
            "baseType": it.get("baseType"),
            **({"properties": it["properties"]} if it.get("properties") else {}),
            **({"synergyHints": it["synergyHints"]} if it.get("synergyHints") else {}),
            **({"socketedRunes": it["socketedRunes"][:6]} if it.get("socketedRunes") else {}),
        }
        for it in (full.get("equippedItems") or [])
        if str(it.get("rarity") or "").upper() != "UNIQUE"
        and (it.get("properties") or it.get("synergyHints") or it.get("socketedRunes"))
    ][:10]

    core_socketed = list(ledger.get("coreSocketedItems") or [])
    if not core_socketed:
        for it in (profile.get("build") or {}).get("equippedItems") or []:
            for rname in it.get("runeEntries") or []:
                info = rune_index.lookup_socketed_item(rname, slot=str(it.get("slot") or ""), base_type=str(it.get("baseType") or ""))
                if info:
                    core_socketed.append({
                        "slot": it.get("slot"),
                        "item": it.get("name"),
                        "name": info["name"],
                        "type": info["runeTypeLabel"],
                        "effects": (info.get("mods") or [])[:2],
                    })

    return {
        "character": full.get("character") or {},
        "ascendancyAblation": full.get("ascendancyAblation") or {},
        "skillMechanics": facts.get("skillMechanics") or {},
        "mobility": facts.get("mobility") or {},
        "offense": facts.get("offense") or {},
        "defense": {
            **(facts.get("defense") or {}),
            "skillDefenseBreakdown": list(ledger.get("skillDefenseSummary") or []),
        },
        "sustain": {
            **(facts.get("sustain") or {}),
            "spiritAudit": facts.get("spiritAudit") or [],
        },
        "pobAssumptionsAndBlindSpots": {
            **(facts.get("pobAssumptions") or {}),
            "unparsedMods": (facts.get("unparsedMods") or [])[:25],
        },
        "weaponSetStats": facts.get("weaponSetStats") or {},
        "buildSkeletonAndCost": {
            "totalEstimatedDivine": dossier.get("totalEstimatedDivine"),
            "totalAllocatedPassives": (full.get("passiveTree") or {}).get("totalAllocated"),
            "keystones": (full.get("passiveTree") or {}).get("keystones") or [],
            "ascendancyNodes": (full.get("passiveTree") or {}).get("ascendancyNodes") or [],
            "anointedNotables": (full.get("passiveTree") or {}).get("anointedNotables") or [],
            "coreUniques": core_uniques,
            "rareHighlights": rare_highlights,
            "coreSocketedItems": core_socketed[:12],
            "skillGroups": full.get("skillGroups") or [],
            "gemGlossary": full.get("gemGlossary") or {},
            **({"unknownGemsInDict": full["unknownGemsInDict"]} if full.get("unknownGemsInDict") else {}),
        },
        "rawSimFallback": sim if not facts.get("offense") else {},
    }


def build_rating_prompt(digest: dict[str, Any]) -> str:
    """专用于「PoB2 引擎实算后由 AI 快速精准评测角色能力」的紧凑 JSON Prompt（含升华消融评判）。"""
    return (
        "请严格基于下方 PoB2 引擎实算的多维角色能力数据包（含升华天赋逐点/整体消融实算、装备插槽核心镶嵌物、技能端防御层拆解、技能阶段/分段/持续/投速/范围、位移速度、输出与 TTK、五系一击线与回复、PoB 假设审计与未解析盲区、构筑骨架与造价），"
        "对这套 PoE2 构筑进行客观、专业、分场景区分的终局能力评级，并基于升华消融数据评判该 BD 选择此升华的核心原因。\n"
        f"{_RATING_RUBRIC_INSTRUCTIONS}\n"
        "请只输出一个紧凑合法的 ```json 代码块（不要输出任何多余解释或 Markdown 正文），字段如下：\n"
        "```json\n"
        "{\n"
        '  "archetype": "简短构筑名（如：古灵使徒 / 双武器组旋风斩+旋龙）",\n'
        '  "oneLineIdea": "一句话讲透核心输出循环、技能形态/机动方式与真实防御体系（<=90字）",\n'
        '  "defenseSystem": "一句话概括全部真实防御层与回复手段（含技能端防御层与五系一击线特征）",\n'
        '  "scenarioTags": ["最多6个高信息密度标签，如升华、位移方式、技能覆盖/投速特征、输出量级、防御体系"],\n'
        '  "budgetTier": "league-start | mid | high | mirror 四选一",\n'
        '  "contentReadiness": {\n'
        '    "overallTier": "一句话终局定位（如：五维拉满 / 镜级极速清图与攻坚档）",\n'
        '    "overallGrade": "S | A | B | C | D",\n'
        '    "summary": "综合评价（引用 PoB2 实算 DPS、位移速度、持续/投速/阶段/范围、五系一击线、技能防御层与短板）",\n'
        '    "ascendancyVerdict": {\n'
        '      "ascendancy": "当前升华名",\n'
        '      "verdict": "一句话讲透该 BD 为何选此升华（引用升华整体/核心大点消融 ΔDPS/ΔEHP 与不可替代机制）",\n'
        '      "keyReasons": ["核心原因1：引用具体升华大点与消融实算数据", "核心原因2：引用防御/手感/机制收益"],\n'
        '      "tradeoff": "可选：指出前置过渡点或换成其他升华会损失的核心短板"\n'
        '    },\n'
        '    "radar": [\n'
        '      {"id": "clearSpeed", "label": "清图效率", "score": 98, "metric": "投速×2.3 · 弹射+持续5.4s", "reason": "引用技能形态、清图DPS与覆盖率说明（达全难度天花板直接给95~100）"},\n'
        '      {"id": "bossing", "label": "攻坚爆发", "score": 98, "metric": "109M DPS (Pinnacle)", "reason": "结合技能形态修正、enemyIsBoss与未解析增伤说明（秒T4即95~100）"},\n'
        '      {"id": "mobility", "label": "机动位移", "score": 96, "metric": "旋风斩 9.4/s · +18%移速", "reason": "引用位移技能实算攻速/CD与步行/施法移速说明"},\n'
        '      {"id": "survivability", "label": "生存硬度", "score": 95, "metric": "EHP 48k · 鬼舞/风舞者/屏障", "reason": "按8词T16与高难赛季玩法不暴毙标准引用五系一击线、池子、抗性、技能防御层与规避层说明"},\n'
        '      {"id": "sustain", "label": "续航手感", "score": 92, "metric": "回蓝/偷取/精魂自洽", "reason": "引用回复、耗蓝/装填/触发自洽与按键循环手感说明"},\n'
        '      {"id": "costEfficiency", "label": "造价性价比", "score": 30, "metric": "已核 ~7500D 镜级溢出", "reason": "独立权衡已核总造价与核心门槛，指出是否严重溢价"}\n'
        "    ],\n"
        '    "items": [\n'
        '      {"id": "t15_mapping", "grade": "S", "verdict": "碾压速通", "reason": "引用位移速度/位移技频率、投速/持续/范围覆盖与清图DPS说明"},\n'
        '      {"id": "juiced_t15_delirium", "grade": "S", "verdict": "碾压速通", "reason": "引用清图覆盖、DPS、混抗、物理/元素一击线与技能防御回复说明"},\n'
        '      {"id": "pinnacle_t0_t2", "grade": "S", "verdict": "碾压速通", "reason": "引用单体爆发/阶段暖机/冷却循环与承伤说明"},\n'
        '      {"id": "pinnacle_t4_uber", "grade": "S", "verdict": "碾压速通", "reason": "结合enemyIsBoss/满层假设/未解析增伤与五系一击线评判T4表现"},\n'
        '      {"id": "trials_sekhema_chaos", "grade": "A", "verdict": "稳健推荐", "reason": "引用机动位移、规避/偏转/护盾回复与混抗容错说明"}\n'
        "    ]\n"
        "  }\n"
        "}\n"
        "```\n\n"
        f"PoB2 引擎实算数据包 JSON：\n{json.dumps(digest, ensure_ascii=False)}"
    )


def build_target_essence_prompt(digest: dict[str, Any]) -> str:
    return (
        "请对下方 PoE2 目标 BD 做一次【深度本质拆解】，严格按以下两段格式输出（不要在开头写多余寒暄）：\n\n"
        f"{_RATING_RUBRIC_INSTRUCTIONS}\n"
        "第一段：输出一个紧凑合法的 ```json 代码块（不要在 JSON 里塞长正文），字段如下：\n"
        "```json\n"
        "{\n"
        '  "archetype": "简短构筑名（如：古灵使徒 / 双武器组旋风斩+旋龙）",\n'
        '  "oneLineIdea": "一句话讲透核心循环、联动本质与真实防御体系（<=90字）",\n'
        '  "defenseSystem": "一句话概括全部真实防御层（含血/ES/闪避/偏转/Ward/升华特殊层）",\n'
        '  "scenarioTags": ["最多6个高信息密度标签"],\n'
        '  "budgetTier": "league-start | mid | high | mirror 四选一",\n'
        '  "coreAffixes": ["按真实重要性列出5~7个核心词缀/机制方向"],\n'
        '  "gearTiers": {\n'
        '    "mandatory": ["🔴机制锁死件：缺它跑不转的装备/暗金名"],\n'
        '    "amplifier": ["🟡核心放大件：主力伤害/精魂放大装备或珠宝名"],\n'
        '    "luxury": ["🟢顶配奢侈件：后期冲天花板、前期可直接平替的装备名"]\n'
        "  },\n"
        '  "pitfalls": ["最多6条真正有价值的抄作业暗坑，明确区分真红线、隐藏刚需门槛与PoB未解析/假红线"],\n'
        '  "contentReadiness": {\n'
        '    "overallTier": "一句话终局定位",\n'
        '    "overallGrade": "S | A | B | C | D",\n'
        '    "summary": "综合评价（必须结合 PoB2 实算 DPS、位移速度/位移技频率、持续/投速/阶段、五系一击线、技能防御层与未解析增伤）",\n'
        '    "ascendancyVerdict": {\n'
        '      "ascendancy": "当前升华名",\n'
        '      "verdict": "一句话总结为何选此升华（结合 ascendancyAblation 实算与机制）",\n'
        '      "keyReasons": ["核心原因1（含具体升华点与消融实算数据）", "核心原因2"],\n'
        '      "tradeoff": "可选：换升华损失或前置过渡点说明"\n'
        '    },\n'
        '    "radar": [\n'
        '      {"id": "clearSpeed", "label": "清图效率", "score": 90, "metric": "关键实算指标", "reason": "..."},\n'
        '      {"id": "bossing", "label": "攻坚爆发", "score": 90, "metric": "关键实算指标", "reason": "..."},\n'
        '      {"id": "mobility", "label": "机动位移", "score": 90, "metric": "关键实算指标", "reason": "..."},\n'
        '      {"id": "survivability", "label": "生存硬度", "score": 78, "metric": "关键实算指标", "reason": "..."},\n'
        '      {"id": "sustain", "label": "续航手感", "score": 82, "metric": "关键实算指标", "reason": "..."},\n'
        '      {"id": "costEfficiency", "label": "造价性价比", "score": 45, "metric": "关键实算指标", "reason": "..."}\n'
        "    ],\n"
        '    "items": [\n'
        '      {"id": "t15_mapping", "grade": "S", "verdict": "碾压速通", "reason": "..."},\n'
        '      {"id": "juiced_t15_delirium", "grade": "S", "verdict": "碾压速通", "reason": "..."},\n'
        '      {"id": "pinnacle_t0_t2", "grade": "S", "verdict": "碾压速通", "reason": "..."},\n'
        '      {"id": "pinnacle_t4_uber", "grade": "S", "verdict": "碾压速通", "reason": "..."},\n'
        '      {"id": "trials_sekhema_chaos", "grade": "A", "verdict": "稳健推荐", "reason": "..."}\n'
        "    ]\n"
        "  }\n"
        "}\n"
        "```\n\n"
        "第二段：紧接一行 `---`，然后用清晰的中文 Markdown 按以下 5 个小节流式输出深度本质拆解：\n"
        "### 1. ⚙️ 核心输出循环与隐藏联动门槛\n"
        "（讲清主副技能完整操作链、双武器组分工，并重点排查隐藏硬门槛：例如特殊 Ward/血量/精魂消耗、空副手基石、常驻药剂触发天赋、低级高品工具技、鞋子无移速原因与位移技能速度等）\n"
        "### 2. 📈 伤害乘区解剖与 PoB2 面板盲区\n"
        "（拆出 3~5 条真实生效的独立伤害乘区；明确指出哪些装备/宝石未被 PoB2 解析或未在 Config 勾选，解释实战强度与面板差距）\n"
        "### 3. 🛡️ 防御体系（含技能端防御层）与真假红线甄别\n"
        "（完整拆清所有装备+天赋+技能端防御层与回复来源，如鬼舞/风舞者/屏障/冰墙/抗性光环挂载回复辅助/致盲/击退等；明确区分必须修补的真短板 vs PoB 导出导致的假红线）\n"
        "### 4. 🌳 天赋树、升华选择原因（附消融实算）与珠宝骨架\n"
        "（必须结合 ascendancyAblation 的整体与逐点孤立/分支消融数据，讲透为什么选这个升华以及各升华大点、基石、WS1/WS2 分支、涂油与每颗珠宝的不可替代作用）\n"
        "### 5. 💎 装备三档分级、插槽核心镶嵌物与半成品逻辑\n"
        "（按「🔴机制锁死 / 🟡核心放大 / 🟢顶配奢侈」拆清每件关键装备的核心词缀与插槽核心物品如遗物/神像/灵核/符文；明确指出哪些词条配合特定天赋/辅助、哪些依赖符文插槽/超工匠石孔位上限/Bonded联动/符文词池，以及推荐半成品时应锁哪 1~2 条最难出词缀）\n\n"
        f"PoB2 带原文事实包 JSON：\n{json.dumps(digest, ensure_ascii=False)}"
    )


def _sanitize_structured_copy_plan(
    raw: dict[str, Any],
    *,
    copy_context: dict[str, Any] | None = None,
    league: str = "Standard",
    profile: dict[str, Any] | None = None,
    pob_verification: dict[str, Any] | None = None,
    tool_trace: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    raw_list = raw.get("shoppingList") or raw.get("items") or []
    if not isinstance(raw_list, list):
        raw_list = []
    items = trade_url.enrich_copy_plan_items(raw_list, league=league, profile=profile)
    summary = str(raw.get("budgetSummary") or raw.get("summary") or "").strip()
    if not items and not summary:
        return None
    ctx = copy_context or {}
    budget_val = ctx.get("budgetDivine")
    lvl_val = ctx.get("characterLevel")
    out: dict[str, Any] = {
        "budgetSummary": summary,
        "budgetDivine": float(budget_val) if isinstance(budget_val, (int, float)) else None,
        "characterLevel": int(lvl_val) if isinstance(lvl_val, (int, float)) else None,
        "league": league or "Standard",
        "items": items,
    }
    if isinstance(pob_verification, dict):
        out["pobVerification"] = pob_verification
    if tool_trace:
        out["toolTrace"] = tool_trace
    return out


def split_copy_structured_and_markdown(raw_text: str) -> tuple[dict[str, Any] | None, str]:
    """解析 copy/consult 模式的「```json ... ``` + --- + Markdown 正文」结构。"""
    s = (raw_text or "").strip()
    if not s:
        return None, ""
    m = _JSON_BLOCK_RE.search(s)
    if m:
        parsed_obj = None
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                parsed_obj = obj
        except Exception:
            parsed_obj = None
        if parsed_obj is not None:
            before = s[: m.start()].strip()
            after = s[m.end() :].lstrip()
            if after.startswith("---"):
                after = after[3:].lstrip()
            md = "\n\n".join(p for p in (before, after) if p).strip()
            return parsed_obj, md
    return None, s


def build_copy_plan_prompt(
    digest: dict[str, Any],
    copy_context: dict[str, Any],
    *,
    price_snapshot: dict[str, Any] | None = None,
    essence_summary: str = "",
) -> str:
    lvl = copy_context.get("characterLevel") or 90
    budget = copy_context.get("budgetDivine")
    owned = copy_context.get("ownedItems") or []
    note = (copy_context.get("goalNote") or "").strip()
    target_lvl = int(((digest.get("character") or {}).get("level")) or 100)
    missing_pts = max(0, target_lvl - int(lvl))

    level_blocked: list[str] = []
    for it in list(digest.get("equippedItems") or []) + list(digest.get("flasksAndCharms") or []):
        req = int(it.get("levelReq") or 0)
        if req > int(lvl):
            level_blocked.append(f"{it.get('slot')}: {it.get('name') or it.get('baseType')} (需 Lv.{req})")

    ctx_block = {
        "myCharacterLevel": int(lvl),
        "targetBuildLevel": target_lvl,
        "missingPassivePoints": missing_pts,
        "myBudgetDivine": budget,
        "alreadyOwnedItems": owned,
        "userGoalNote": note,
        "itemsBlockedByMyLevel": level_blocked,
        "marketPrices": (price_snapshot or {}).get("prices") or {},
    }
    extra = f"\n前序本质拆解要点：\n{essence_summary[:1800]}\n" if essence_summary else ""
    has_rev = isinstance(digest.get("reverseBuildSpec"), dict) and bool((digest.get("reverseBuildSpec") or {}).get("slotSpecs"))
    rev_guidance = (
        "★ 必须遵守【BD 倒推灵活配装原则（严禁死板 AND 复制原件词缀！）】：\n"
        "1. 数据包中已附带由 PoB2 引擎实算倒推出的 `reverseBuildSpec`（含全局有效词缀边际收益 `statSensitivity`、已证实 0 收益的 `deadProbeIds`、武器面板偏好 `weaponPanelAudit`、全身红线账本 `globalResourceBudget` 及每个槽位基于全词缀池生成的等价加权池 `slotSpecs`）。\n"
        "2. 每个稀有/黄装槽位，**不要把原件上的词缀写成死板的 AND 列表**！请用四段式灵活蓝图表达需求：\n"
        "   - **面板硬底线 (`equipmentFloors`)**：遵循 `weaponArchetype` 与 `targetPanels`——常规吃攻速攻击武器看 `dps`/`pdps`/`edps` + `aps`/`crit`；**不吃攻速的单发大伤武器（`attack_hit_no_aps`，如流血/点燃/重击/冷却）严禁设 `dps`/`pdps`/`aps`，改用单发均伤 `damage`（纯物理可加 `edps_max` 防元素武器混入），弩可加 `reload_time`（装填上限）**；挂件武器（`stat_stick_weapon`）不设任何武器伤害底线；攻速工具武器只看 `aps`；防具看 `es`/`ev`/`ar`/`ward`；结合 `recommendedBases` 给出等价底材建议；\n"
        "   - **硬绑定 (`mandatoryAffixes`)**：仅填真正不可替代的硬机制（如基底自带技能、精魂刚需）；\n"
        "   - **等价核心门槛 (`mandatoryAnyOf`)**：OR 关系（如法系武器 `[+4 全法术等级 OR +4 闪电法术等级]` 满足任意一条即可）；\n"
        "   - **攻/防分离等价加权池 (`offenseTargetRatio` / `defenseTargetRatio` 或自定义 `offensePool` / `defensePool`)**：**OR 加权求和关系**！凡是对该 BD 有效的词缀（如法系额外伤/法伤/对应元素伤/施速/暴击、攻击点伤/攻速/暴伤/投速/命中、三抗互换池等）都能互相折抵达标，彻底不拘泥于原 BD 原件词缀！\n"
        "   - 跨槽位统筹抗性与属性：例如把项链/手套的抗性挪到腰带/戒指时，只需给项链 `defenseTargetRatio: 0`，把戒指/腰带 `defenseTargetRatio` 提高到 `0.85`！\n"
        "3. 若当前接口已挂载 `pob_test_replacement` / `pob_probe_affixes` / `pob_verify_copy_plan` 工具，请善用工具（最多 2~3 轮）验证关键平替件与整套预算方案的 PoB2 预期 DPS/EHP 与抗性/精魂/属性红线，若红线告警请修正方案后再输出最终结果。\n\n"
        if has_rev
        else ""
    )
    return (
        "玩家已经看懂目标 BD，现在需要你结合【玩家自身等级、当前造价预算(Divine)、已有装备】给出【基于 BD 机制倒推、可直接落地加权搜货】的定制抄作业落地方案。\n"
        f"{rev_guidance}"
        "严格按以下两段格式输出（不要在开头写多余寒暄）：\n\n"
        "第一段：先输出一个紧凑合法的 ```json 代码块，供前端自动生成【四段式加权搜货蓝图 + 一键直达官方市集】快捷卡片（跳过玩家已拥有的装备，按购买优先级输出 4~10 个最关键部位/珠宝）：\n"
        "```json\n"
        "{\n"
        '  "budgetSummary": "一句话概括当前预算分配策略与跨部位取舍重点（<=80字）",\n'
        '  "shoppingList": [\n'
        "    {\n"
        '      "slot": "Weapon 1 / Helmet / Body Armour / Gloves / Boots / Amulet / Ring 1 / Belt / Jewel 等原始英文槽位名",\n'
        '      "action": "buy_unique | buy_finished | buy_budget | craft_semi | buy_base | skip 六选一",\n'
        '      "priority": 1,\n'
        '      "itemName": "暗金英文原名（黄装填清晰目标描述或基底英文名）",\n'
        '      "baseType": "装备英文基底名（如 Gemini Bow / Siphoning Wand / Unset Ring，仅需同大类时填原基底名并把 lockBase 设 false）",\n'
        '      "rarity": "UNIQUE 或 RARE",\n'
        '      "budgetDivine": 4.0,\n'
        '      "minIlvl": 81,\n'
        '      "minSockets": 2,\n'
        '      "corrupted": false,\n'
        '      "lockBase": false,\n'
        '      "reason": "一句话说明该部位当前预算的面板底线、等价词缀思路与可砍点（<=60字）",\n'
        '      "equipmentFloors": {"dps": 480, "aps": 1.60},\n'
        '      "mandatoryAffixes": [{"text": "+30 to Spirit", "zh": "精魂", "min": 30}],\n'
        '      "mandatoryAnyOf": [{"label": "法术或闪电等级≥4", "minCount": 1, "affixes": [{"text": "+4 to Level of all Spell Skills", "zh": "全法术等级", "min": 4}, {"text": "+4 to Level of all Lightning Spell Skills", "zh": "闪电法术等级", "min": 4}]}],\n'
        '      "offenseTargetRatio": 0.72,\n'
        '      "defenseTargetRatio": 0.55,\n'
        '      "offensePool": [{"statId": "优先直接复用 reverseBuildSpec.slotSpecs 中对应 statId，或写标准英文 text", "zh": "额外闪电伤害", "weight": 0.45}],\n'
        '      "minOffenseScore": 35.0,\n'
        '      "defensePool": [{"statId": "explicit.stat_3372524247", "zh": "火焰抗性", "weight": 0.22}],\n'
        '      "minDefenseScore": 12.0,\n'
        '      "cutAffixes": ["本预算可砍掉的词条1", "词条2"],\n'
        '      "socketTip": "可选：孔位/符文/涂油提示"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "```\n"
        "注意：\n"
        "- 对黄装平替（`buy_budget` / `buy_finished`）：优先直接使用 `equipmentFloors` + `mandatoryAnyOf` + `offenseTargetRatio`/`defenseTargetRatio`（后端会自动把 `reverseBuildSpec.slotSpecs` 里该槽位**全部合法有效等价词缀**按 PoB2 权重编译为攻防双 Weighted Sum OR 查询！仅当你希望自定义微调词池或无 `reverseBuildSpec` 时再手写 `offensePool`/`defensePool`）；\n"
        "- 绝不要把 3~4 条普通伤害/抗性词缀塞进 `mandatoryAffixes` 做死板 AND；`mandatoryAffixes` 只放缺它机制就失效的硬绑定项；\n"
        "- 传奇/暗金装备（UNIQUE）严禁使用 `craft_semi` 或 `buy_base`（暗金只能 `buy_unique` 直买，按需挑 roll/瓦尔孔/插符文/涂油），且无需填 `offensePool`/`equipmentFloors`；\n"
        "- `lockBase` 仅在特殊基底刚需（如 Ward 底子、Unset Ring、Absent/Lament/Portent Amulet、Shrine/Stoic Sceptre、Chiming Staff）或买半成品/白底时填 `true`，普通黄装武器/防具填 `false`（后端会自动锁定同武器/防具大类，确保不令技能失效又不筛空）。\n\n"
        "第二段：紧接一行 `---`，然后用清晰中文 Markdown 分 5 个部分输出详细抄作业落地方案：\n"
        "### 1. 💰 当前预算逐笔分配与跨部位资源统筹\n"
        "（明确预算分配，并说明抗性/精魂/三属性在哪些部位凑齐最省钱；若调用了 PoB2 沙盒验收，引用整套预期达成 %DPS / %EHP）\n"
        "### 2. 🔄 武器与核心稀有件倒推规格（面板底线 + 等价 OR 词缀池）\n"
        "（逐件讲清楚：只要满足什么面板底线和哪些等价词缀组合即可，不必死抄原件词缀；说明天赋/符文联动与超孔位/半成品策略）\n"
        "### 3. 🚨 绝不能砍的机制红线与低配变通\n"
        "（重点防踩坑：哪些机制门槛哪怕低预算也必须保？若买不起某门槛件，必须同步改哪颗辅助宝石/天赋才能避免技能失效？）\n"
        f"### 4. 🌳 等级门槛拦截与少 {missing_pts} 点天赋裁剪建议\n"
        "（针对穿不上的高需求装备给过渡件；并指明比目标少的天赋点优先从哪几个非核心大点裁掉、必须保住哪些核心基石/升华/武器组节点）\n"
        "### 5. 🚀 下一档预算升级阶梯\n"
        "（按性价比列出攒到下一档预算后最值得优先升级的前 3~5 件装备/珠宝顺序）\n\n"
        f"玩家抄作业条件 JSON：\n{json.dumps(ctx_block, ensure_ascii=False)}\n"
        f"{extra}\n"
        f"目标 BD 带原文与倒推灵敏度事实包 JSON：\n{json.dumps(digest, ensure_ascii=False)}"
    )


def build_consult_prompt(copy_context: dict[str, Any]) -> str:
    lvl = copy_context.get("characterLevel") or 80
    budget = copy_context.get("budgetDivine") or 5
    ff = copy_context.get("freeformQuery") or {}
    asc = (ff.get("ascendancy") or "").strip() or "未指定"
    idea = (ff.get("skillIdea") or "").strip() or "未指定"
    note = (copy_context.get("goalNote") or "").strip()
    owned = copy_context.get("ownedItems") or []
    return (
        "玩家目前没有现成的 PoB2 分享码，希望按以下自身条件直接获取《流放之路 2》构筑起步与抄作业建议：\n"
        f"- 意向职业 / 升华：{asc}\n"
        f"- 意向核心技能 / 玩法：{idea}\n"
        f"- 当前角色等级：Lv.{lvl}\n"
        f"- 当前可用预算：{budget} Divine\n"
        f"- 已有核心件：{', '.join(owned) if owned else '无'}\n"
        f"- 额外需求：{note or '无'}\n\n"
        "请严格按两段格式输出（不要多余寒暄）：\n"
        "第一段：输出紧凑合法的 ```json 代码块（含 budgetSummary 与 4~6 件核心起步装备 shoppingList，每件含 slot/action/priority/itemName/baseType/rarity/budgetDivine/lockBase/reason/selectedAffixes[{text,zh,min,required}]），text 用标准 PoE2 英文词缀以便前端一键直达官方市集。\n"
        "第二段：紧接一行 `---`，用清晰中文 Markdown 分 5 块输出实战可落地的构筑方案：\n"
        "### 1. ⚙️ 核心机制与技能连法骨架（主技能 + 辅助 + 精魂光环/工具组）\n"
        "### 2. 💰 当前预算装备与词缀分配（区分必买启动件 vs 平替黄装必保词缀）\n"
        "### 3. 🛡️ 防御路线与抗性/精魂/属性红线提醒\n"
        "### 4. 🌳 核心升华顺序、关键基石与当前等级天赋重心\n"
        "### 5. 🚀 后续攒到下一档预算的升级方向\n"
        "（注意：若涉及最新赛季变动较快的冷门数值，请明确提醒以游戏内实际描述为准，切勿编造虚假数值。）"
    )


def stream_analyze_with_llm(
    mode: str,
    *,
    profile: dict[str, Any] | None = None,
    gap_report: dict[str, Any] | None = None,
    copy_context: dict[str, Any] | None = None,
    price_snapshot: dict[str, Any] | None = None,
    essence_summary: str = "",
    league: str = "Standard",
    cfg: dict[str, Any] | None = None,
    state_dir: str = STATE_DIR,
    use_cache: bool = True,
    pob_sandbox: dict[str, Any] | None = None,
    http_post: Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]] | None = None,
) -> Iterator[dict[str, Any]]:
    """统一流式分析生成器：产出 meta / tool_status / reasoning / delta / structured / done / error 事件。"""
    active_cfg = cfg or load_llm_config(state_dir)
    if mode != "test" and not active_cfg.get("enabled"):
        yield {"type": "error", "error": "尚未启用大模型分析（请先在「AI 设置」开启开关并配置 API Key）"}
        return

    active_league = (league or (price_snapshot or {}).get("league") or "Standard").strip() or "Standard"
    model_name = str(active_cfg.get("model") or "")
    cache_key: str | None = None

    if mode == "target":
        if not profile:
            yield {"type": "error", "error": "缺少目标 BD 解析数据"}
            return
        digest = build_target_digest(profile)
        cache_key = _digest_cache_key("target", digest, active_cfg)
        if use_cache:
            cached = _load_cache_entry(cache_key, state_dir=state_dir)
            if cached and cached.get("text"):
                yield {"type": "meta", "model": cached.get("model") or model_name, "cached": True, "mode": mode}
                if cached.get("structured"):
                    merged_pos = (
                        merge_ai_positioning(
                            profile["positioning"],
                            cached["structured"],
                            model=str(cached.get("model") or model_name),
                            engine_metrics_summary=_format_engine_metrics_summary(profile),
                        )
                        if isinstance(profile.get("positioning"), dict)
                        else None
                    )
                    yield {"type": "structured", "structured": cached["structured"], "positioning": merged_pos}
                else:
                    merged_pos = profile.get("positioning")
                if cached.get("reasoning"):
                    yield {"type": "reasoning", "delta": cached["reasoning"]}
                yield {"type": "delta", "delta": cached["text"]}
                yield {
                    "type": "done",
                    "text": cached["text"],
                    "reasoning": cached.get("reasoning") or "",
                    "model": cached.get("model") or model_name,
                    "cached": True,
                    "structured": cached.get("structured"),
                    "positioning": merged_pos,
                }
                return
        user_prompt = build_target_essence_prompt(digest)
    elif mode == "copy":
        if not profile:
            yield {"type": "error", "error": "缺少目标 BD 解析数据"}
            return
        digest = build_target_digest(profile, compact_for_copy=True)
        ctx = copy_context or {}
        cache_key = _digest_cache_key(
            "copy",
            {"digest": digest, "ctx": ctx, "league": active_league, "prices": (price_snapshot or {}).get("prices") or {}},
            active_cfg,
        )
        if use_cache:
            cached = _load_cache_entry(cache_key, state_dir=state_dir)
            if cached and cached.get("text"):
                yield {"type": "meta", "model": cached.get("model") or model_name, "cached": True, "mode": mode}
                cached_copy_plan = cached.get("copyPlan")
                if not cached_copy_plan and isinstance(cached.get("structured"), dict):
                    cached_copy_plan = _sanitize_structured_copy_plan(
                        cached["structured"],
                        copy_context=ctx,
                        league=active_league,
                        profile=profile,
                    )
                if cached_copy_plan:
                    yield {
                        "type": "structured",
                        "structured": cached.get("structured") or cached_copy_plan,
                        "copyPlan": cached_copy_plan,
                    }
                if cached.get("reasoning"):
                    yield {"type": "reasoning", "delta": cached["reasoning"]}
                yield {"type": "delta", "delta": cached["text"]}
                yield {
                    "type": "done",
                    "text": cached["text"],
                    "reasoning": cached.get("reasoning") or "",
                    "model": cached.get("model") or model_name,
                    "cached": True,
                    "structured": cached.get("structured"),
                    "copyPlan": cached_copy_plan,
                }
                return
        user_prompt = build_copy_plan_prompt(digest, ctx, price_snapshot=price_snapshot, essence_summary=essence_summary)
    elif mode == "consult":
        user_prompt = build_consult_prompt(copy_context or {})
    elif mode == "gap":
        if not gap_report:
            yield {"type": "error", "error": "缺少自身 vs 目标差距诊断报告"}
            return
        digest = build_gap_digest(gap_report)
        user_prompt = (
            "请对比「我的 BD」与「目标 BD」的实算差距报告，给出可执行的追赶路线：\n"
            "### 1. 🆓 先处理哪些 0 成本或低成本问题（天赋、技能连法、抗性/精魂红线）\n"
            "### 2. 💎 按性价比排最值得优先更换的前 3~5 件装备或珠宝（说明 ΔDPS/ΔEHP 与红线风险）\n"
            "### 3. 🎯 总结当前阶段最卡脖子的一环与下一步目标\n\n"
            f"差距报告 JSON：\n{json.dumps(digest, ensure_ascii=False)}"
        )
    else:
        yield {"type": "error", "error": f"不支持的流式分析模式：{mode}"}
        return

    yield {"type": "meta", "model": model_name, "cached": False, "mode": mode}

    raw_buf: list[str] = []
    reasoning_buf: list[str] = []
    resolved_model = model_name
    header_emitted = False
    md_sent_len = 0
    structured_obj: dict[str, Any] | None = None
    merged_pos: dict[str, Any] | None = None
    copy_plan_obj: dict[str, Any] | None = None
    pob_verification: dict[str, Any] | None = None
    tool_trace: list[dict[str, Any]] = []
    has_structured_header = mode in ("target", "copy", "consult")

    # copy 模式 + 具备本地 PoB2 沙盒时：先运行受控多轮 PoB2 工具调用环（全程原生 SSE 流式防超时，最多 2 轮；不支持 tools 时平滑回退直出）
    tool_messages: list[dict[str, Any]] | None = None
    prefetched_final_text: str | None = None
    streamed_final_in_tool_loop = False
    enable_sandbox = bool((copy_context or {}).get("enablePobSandbox", True))
    if mode == "copy" and pob_sandbox and pob_sandbox.get("pool") is not None and enable_sandbox:
        msgs: list[dict[str, Any]] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        max_rounds = 2
        tool_loop_start = time.time()
        tool_loop_failed = False

        def _push_content_piece(piece: str) -> list[dict[str, Any]]:
            nonlocal header_emitted, md_sent_len, structured_obj, copy_plan_obj
            emitted: list[dict[str, Any]] = []
            raw_buf.append(piece)
            full_so_far = "".join(raw_buf)
            if not header_emitted:
                m = _JSON_BLOCK_RE.search(full_so_far)
                if m:
                    try:
                        parsed = json.loads(m.group(1))
                        if isinstance(parsed, dict):
                            structured_obj = parsed
                            copy_plan_obj = _sanitize_structured_copy_plan(
                                parsed,
                                copy_context=copy_context,
                                league=active_league,
                                profile=profile,
                                pob_verification=pob_verification,
                                tool_trace=tool_trace,
                            )
                            if copy_plan_obj:
                                emitted.append({
                                    "type": "structured",
                                    "structured": structured_obj,
                                    "copyPlan": copy_plan_obj,
                                })
                    except Exception:
                        pass
                    header_emitted = True
                    after = full_so_far[m.end() :].lstrip()
                    if after.startswith("---"):
                        after = after[3:].lstrip()
                    if after:
                        md_sent_len = len(after)
                        emitted.append({"type": "delta", "delta": after})
                elif len(full_so_far) > 4800 and "```" not in full_so_far[:250]:
                    header_emitted = True
                    md_sent_len = len(full_so_far)
                    emitted.append({"type": "delta", "delta": full_so_far})
            else:
                m = _JSON_BLOCK_RE.search(full_so_far)
                if m:
                    after = full_so_far[m.end() :].lstrip()
                    if after.startswith("---"):
                        after = after[3:].lstrip()
                else:
                    after = full_so_far
                if len(after) > md_sent_len:
                    new_chunk = after[md_sent_len:]
                    md_sent_len = len(after)
                    emitted.append({"type": "delta", "delta": new_chunk})
            return emitted

        for round_idx in range(max_rounds):
            if round_idx > 0 and (time.time() - tool_loop_start) > 75.0:
                msgs.append({
                    "role": "user",
                    "content": "沙盒实测已完成，请现在立刻严格按前述两段格式（```json 蓝图 + --- + 5段中文 Markdown）输出最终定制抄作业方案，不要再调用工具。",
                })
                tool_messages = msgs
                break

            yield {
                "type": "tool_status",
                "stage": "thinking",
                "round": round_idx + 1,
                "summary": f"第 {round_idx + 1}/{max_rounds} 轮：AI 正在流式规划方案并决定是否调用 PoB2 沙盒实测...",
            }

            step: dict[str, Any] = {}
            round_streamed_deltas = False
            try:
                if http_post is not None:
                    step = _call_chat_messages(active_cfg, msgs, tools=POB2_COPY_TOOLS, http_post=http_post, timeout=150.0)
                    if step.get("model"):
                        resolved_model = str(step["model"])
                    if step.get("reasoning"):
                        reasoning_buf.append(step["reasoning"])
                        yield {"type": "reasoning", "delta": step["reasoning"]}
                else:
                    for sev in _stream_chat_step(active_cfg, msgs, tools=POB2_COPY_TOOLS, idle_timeout=120.0):
                        if sev.get("model"):
                            resolved_model = str(sev["model"])
                        if sev["type"] == "reasoning":
                            reasoning_buf.append(sev["delta"])
                            yield {"type": "reasoning", "delta": sev["delta"]}
                        elif sev["type"] == "delta":
                            round_streamed_deltas = True
                            for out_ev in _push_content_piece(sev["delta"]):
                                yield out_ev
                        elif sev["type"] == "step_done":
                            step = sev
            except Exception as tool_exc:
                err_txt = str(tool_exc)
                # 如果已经跑过工具实测，直接带实测结论进入无工具流式终稿；否则平滑回退为无工具流式直出
                if tool_trace:
                    yield {
                        "type": "tool_status",
                        "stage": "done",
                        "round": round_idx + 1,
                        "summary": f"⚠️ 第 {round_idx + 1} 轮工具调用中断（{err_txt[:80]}），保留已测数据切换流式直出终稿...",
                    }
                    msgs.append({
                        "role": "user",
                        "content": "沙盒实测已完成，请现在立刻严格按前述两段格式（```json 蓝图 + --- + 5段中文 Markdown）输出最终定制抄作业方案，不要再调用工具。",
                    })
                    tool_messages = msgs
                else:
                    yield {
                        "type": "tool_status",
                        "stage": "done",
                        "round": round_idx + 1,
                        "summary": f"ℹ️ 工具模式回退为高速流式直出（{err_txt[:80]}）...",
                    }
                    tool_loop_failed = True
                break

            tcalls = step.get("tool_calls") or []
            if not tcalls:
                if round_streamed_deltas:
                    streamed_final_in_tool_loop = True
                else:
                    prefetched_final_text = str(step.get("text") or "").strip()
                break

            # 模型发起了 tool_calls，清理本轮可能暂存的过渡前缀文本
            if round_streamed_deltas:
                raw_buf.clear()
                header_emitted = False
                md_sent_len = 0
                structured_obj = None
                copy_plan_obj = None

            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": step.get("text") or "",
                "tool_calls": tcalls,
            }
            msgs.append(assistant_msg)

            for tc in tcalls[:4]:
                fn = tc.get("function") or {}
                tname = str(fn.get("name") or "")
                targs = fn.get("arguments") or "{}"
                tid = str(tc.get("id") or f"call_{round_idx}_{tname}")
                yield {
                    "type": "tool_status",
                    "stage": "running",
                    "round": round_idx + 1,
                    "tool": tname,
                    "summary": f"⚙️ AI 正在调用本地 PoB2 沙盒执行 `{tname}`...",
                }
                res_obj, human_sum, is_bundle = _execute_pob2_tool(tname, targs, pob_sandbox)
                trace_entry = {
                    "round": round_idx + 1,
                    "tool": tname,
                    "summary": human_sum,
                    "ok": "error" not in res_obj,
                }
                tool_trace.append(trace_entry)
                if is_bundle and "error" not in res_obj:
                    pob_verification = res_obj
                yield {
                    "type": "tool_status",
                    "stage": "done",
                    "round": round_idx + 1,
                    "tool": tname,
                    "summary": human_sum,
                    "result": res_obj if is_bundle else None,
                }
                msgs.append({
                    "role": "tool",
                    "tool_call_id": tid,
                    "content": json.dumps(res_obj, ensure_ascii=False),
                })

            if round_idx == max_rounds - 1:
                msgs.append({
                    "role": "user",
                    "content": "沙盒实测已完成，请现在立刻严格按前述两段格式（```json 蓝图 + --- + 5段中文 Markdown）输出最终定制抄作业方案，不要再调用工具。",
                })
                tool_messages = msgs

        if tool_loop_failed:
            prefetched_final_text = None
            tool_messages = None
            streamed_final_in_tool_loop = False

    try:
        if streamed_final_in_tool_loop:
            stream_iter: Iterator[dict[str, Any]] = iter([])
        elif prefetched_final_text is not None:
            stream_iter = iter([{"type": "delta", "delta": prefetched_final_text, "model": resolved_model}])
        elif http_post is not None and tool_messages is not None:
            final_step = _call_chat_messages(active_cfg, tool_messages, tools=None, http_post=http_post, timeout=150.0)
            resolved_model = str(final_step.get("model") or resolved_model)
            stream_iter = iter([{"type": "delta", "delta": final_step.get("text") or "", "model": resolved_model}])
        else:
            if mode == "copy":
                yield {
                    "type": "tool_status",
                    "stage": "thinking",
                    "summary": "✍️ 正在流式生成最终抄作业蓝图与实战指南...",
                }
            stream_iter = stream_chat_completion(
                active_cfg,
                _SYSTEM_PROMPT,
                user_prompt,
                messages_override=tool_messages,
                idle_timeout=120.0,
            )

        for ev in stream_iter:
            if ev.get("model"):
                resolved_model = str(ev["model"])
            if ev["type"] == "reasoning":
                reasoning_buf.append(ev["delta"])
                yield {"type": "reasoning", "delta": ev["delta"]}
                continue

            if ev["type"] == "delta":
                piece = ev["delta"]
                raw_buf.append(piece)
                if not has_structured_header:
                    yield {"type": "delta", "delta": piece}
                    continue

                # target / copy / consult 模式：等待轻量 JSON 头部闭合后立即推送 structured 卡片/市集清单，再流式推送 --- 之后的 Markdown 正文
                full_so_far = "".join(raw_buf)
                if not header_emitted:
                    m = _JSON_BLOCK_RE.search(full_so_far)
                    if m:
                        try:
                            parsed = json.loads(m.group(1))
                            if isinstance(parsed, dict):
                                if mode == "target":
                                    structured_obj = _sanitize_structured_target(parsed)
                                    merged_pos = (
                                        merge_ai_positioning(
                                            profile["positioning"],
                                            structured_obj,
                                            model=resolved_model,
                                            engine_metrics_summary=_format_engine_metrics_summary(profile),
                                        )
                                        if profile and isinstance(profile.get("positioning"), dict)
                                        else None
                                    )
                                    yield {"type": "structured", "structured": structured_obj, "positioning": merged_pos}
                                else:
                                    structured_obj = parsed
                                    copy_plan_obj = _sanitize_structured_copy_plan(
                                        parsed,
                                        copy_context=copy_context,
                                        league=active_league,
                                        profile=profile,
                                        pob_verification=pob_verification,
                                        tool_trace=tool_trace,
                                    )
                                    if copy_plan_obj:
                                        yield {
                                            "type": "structured",
                                            "structured": structured_obj,
                                            "copyPlan": copy_plan_obj,
                                        }
                        except Exception:
                            pass
                        header_emitted = True
                        after = full_so_far[m.end() :].lstrip()
                        if after.startswith("---"):
                            after = after[3:].lstrip()
                        if after:
                            md_sent_len = len(after)
                            yield {"type": "delta", "delta": after}
                    elif len(full_so_far) > (4800 if mode in ("copy", "consult") else 2200) and "```" not in full_so_far[:250]:
                        # 模型未按 JSON 头部格式输出时直接透传 Markdown，避免无响应
                        header_emitted = True
                        md_sent_len = len(full_so_far)
                        yield {"type": "delta", "delta": full_so_far}
                else:
                    m = _JSON_BLOCK_RE.search(full_so_far)
                    if m:
                        after = full_so_far[m.end() :].lstrip()
                        if after.startswith("---"):
                            after = after[3:].lstrip()
                    else:
                        after = full_so_far
                    if len(after) > md_sent_len:
                        new_chunk = after[md_sent_len:]
                        md_sent_len = len(after)
                        yield {"type": "delta", "delta": new_chunk}
    except Exception as exc:
        yield {"type": "error", "error": str(exc)}
        return

    full_raw = "".join(raw_buf).strip()
    full_reasoning = "".join(reasoning_buf).strip()

    if mode == "target":
        parsed_struct, final_md = split_structured_header_and_markdown(full_raw)
        if parsed_struct and not structured_obj:
            structured_obj = parsed_struct
            merged_pos = (
                merge_ai_positioning(
                    profile["positioning"],
                    structured_obj,
                    model=resolved_model,
                    engine_metrics_summary=_format_engine_metrics_summary(profile),
                )
                if profile and isinstance(profile.get("positioning"), dict)
                else None
            )
            yield {"type": "structured", "structured": structured_obj, "positioning": merged_pos}
        if len(final_md) > md_sent_len:
            yield {"type": "delta", "delta": final_md[md_sent_len:]}
        if cache_key and final_md:
            _save_cache_entry(
                cache_key,
                {
                    "text": final_md,
                    "reasoning": full_reasoning,
                    "structured": structured_obj,
                    "model": resolved_model,
                },
                state_dir=state_dir,
            )
        yield {
            "type": "done",
            "text": final_md,
            "reasoning": full_reasoning,
            "model": resolved_model,
            "cached": False,
            "structured": structured_obj,
            "positioning": merged_pos,
        }
    elif mode in ("copy", "consult"):
        parsed_copy_raw, final_md = split_copy_structured_and_markdown(full_raw)
        if parsed_copy_raw and not copy_plan_obj:
            structured_obj = parsed_copy_raw
            copy_plan_obj = _sanitize_structured_copy_plan(
                parsed_copy_raw,
                copy_context=copy_context,
                league=active_league,
                profile=profile,
                pob_verification=pob_verification,
                tool_trace=tool_trace,
            )
            if copy_plan_obj:
                yield {"type": "structured", "structured": structured_obj, "copyPlan": copy_plan_obj}
        if len(final_md) > md_sent_len:
            yield {"type": "delta", "delta": final_md[md_sent_len:]}
        if cache_key and (final_md or copy_plan_obj):
            _save_cache_entry(
                cache_key,
                {
                    "text": final_md,
                    "reasoning": full_reasoning,
                    "structured": structured_obj,
                    "copyPlan": copy_plan_obj,
                    "model": resolved_model,
                },
                state_dir=state_dir,
            )
        yield {
            "type": "done",
            "text": final_md,
            "reasoning": full_reasoning,
            "model": resolved_model,
            "cached": False,
            "structured": structured_obj,
            "copyPlan": copy_plan_obj,
        }
    else:
        if cache_key and full_raw:
            _save_cache_entry(
                cache_key,
                {"text": full_raw, "reasoning": full_reasoning, "model": resolved_model},
                state_dir=state_dir,
            )
        yield {
            "type": "done",
            "text": full_raw,
            "reasoning": full_reasoning,
            "model": resolved_model,
            "cached": False,
        }


def _format_engine_metrics_summary(profile: dict[str, Any]) -> str:
    facts = profile.get("engineFacts") or {}
    sim = profile.get("simulation") or {}
    parts: list[str] = []
    off = facts.get("offense") or {}
    mh = (facts.get("skillMechanics") or {}).get("multiHitAudit") or {}
    dps = float(off.get("totalTotemDps") or off.get("effectiveDps") or sim.get("dps") or 0)
    eff_rep = float(mh.get("effectiveDpsRepresentative") or 0)
    if mh.get("pobDisplaysSingleHitOnly") and eff_rep > dps * 1.5:
        raw_s = f"{dps / 1_000_000:.2f}M" if dps >= 1_000_000 else f"{dps / 1_000:.0f}k"
        eff_s = f"{eff_rep / 1_000_000:.1f}M" if eff_rep >= 1_000_000 else f"{eff_rep / 1_000:.0f}k"
        parts.append(f"单发面板 {raw_s} → 复数实战 ~{eff_s}")
    elif dps >= 1_000_000:
        parts.append(f"DPS {dps / 1_000_000:.2f}M")
    elif dps >= 1_000:
        parts.append(f"DPS {dps / 1_000:.1f}k")
    elif dps > 0:
        parts.append(f"DPS {dps:.0f}")
    p6_ttk = ((off.get("ttkAnchors") or {}).get("targets") or {}).get("party6_uber_boss", {}).get("effectiveTtkSec")
    if isinstance(p6_ttk, (int, float)) and 0 < p6_ttk <= 10.0:
        parts.append(f"6人Uber TTK ~{p6_ttk:.1f}s")
    mob = facts.get("mobility") or {}
    mob_sum = str(mob.get("primaryTravelSummary") or sim.get("mobilitySummary") or "").strip()
    if mob_sum:
        parts.append(mob_sum)

    main_m = (facts.get("skillMechanics") or {}).get("mainSkill") or {}
    arch_labels = {
        "roaming_bouncing_proj": "游走弹跳投射物",
        "direct_proj": "直击投射物",
        "crossbow_grenade": "弩箭/手雷",
        "melee_strike": "高频近战",
        "slam_warcry": "重击猛击",
        "channel_stage": "蓄力/阶段",
        "dot_ailment": "持续/异常",
        "totem": "图腾部署",
        "minion": "召唤军团",
        "trigger_meta": "元技能触发",
    }
    arch_key = str(main_m.get("archetype") or sim.get("skillArchetype") or "").strip()
    if arch_key in arch_labels:
        parts.append(arch_labels[arch_key])
    dur = float(main_m.get("durationSec") or sim.get("duration") or 0)
    if dur > 0:
        parts.append(f"持续 {dur:.2f}s")
    ps = float(main_m.get("projSpeedMod") or sim.get("projSpeedMod") or 0)
    if ps > 0 and abs(ps - 1.0) >= 0.03:
        parts.append(f"投速 ×{ps:.2f}")
    pcnt = int(main_m.get("projCount") or sim.get("projectileCount") or 0)
    if pcnt > 1:
        parts.append(f"{pcnt}投射物")
    if main_m.get("bouncesOffTerrain"):
        parts.append("墙体弹射")
    aoe_r = float(main_m.get("aoeRadiusMetres") or sim.get("aoeRadius") or 0)
    if aoe_r > 0:
        parts.append(f"范围 {aoe_r:.1f}m")
    mag = int(main_m.get("magazineSize") or 0)
    rel = float(main_m.get("reloadTimeSec") or 0)
    if mag > 0 or rel > 0:
        parts.append(f"弹匣 {mag}/装填 {rel:.2f}s" if mag > 0 and rel > 0 else (f"装填 {rel:.2f}s" if rel > 0 else f"弹匣 {mag}"))
    tot_lim = int(main_m.get("activeTotemLimit") or sim.get("activeTotemLimit") or 0)
    if tot_lim > 0:
        parts.append(f"图腾×{tot_lim}")
    min_lim = int(main_m.get("activeMinionLimit") or 0)
    if min_lim > 0:
        parts.append(f"召唤×{min_lim}")
    st_cnt = int(main_m.get("skillStageCount") or sim.get("skillStageCount") or 0)
    st_max = int(main_m.get("skillStageMax") or sim.get("skillStageMax") or 0)
    if st_cnt > 0:
        parts.append(f"阶段 {st_cnt}/{st_max}" if st_max > 0 else f"阶段 {st_cnt}")

    dfn = facts.get("defense") or {}
    ehp = float(dfn.get("ehp") or sim.get("ehp") or 0)
    phys_hit = float(dfn.get("physMaxHit") or sim.get("physMaxHit") or 0)
    if ehp >= 1_000:
        parts.append(f"EHP {ehp / 1_000:.1f}k" + (f" / 物一击 {phys_hit / 1_000:.1f}k" if phys_hit >= 1_000 else ""))
    boss_preset = str((facts.get("pobAssumptions") or {}).get("enemyIsBoss") or sim.get("enemyIsBoss") or "").strip()
    if boss_preset and boss_preset != "None":
        parts.append(f"目标:{boss_preset}")
    return " | ".join(parts)


def rate_target_with_ai(
    profile: dict[str, Any],
    *,
    cfg: dict[str, Any] | None = None,
    state_dir: str = STATE_DIR,
    http_post: Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]] | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    """结合 PoB2 引擎 7 维实算数据（技能阶段/持续/投速/范围、位移速度、输出、防御一击线、PoB 假设等），由 AI 快速输出精准评级。"""
    active_cfg = cfg or load_llm_config(state_dir)
    if not active_cfg.get("enabled"):
        raise ValueError("尚未启用大模型分析（请先在「AI 设置」开启开关并配置 API Key）")

    digest = build_rating_digest(profile)
    cache_key = _digest_cache_key("rate", digest, active_cfg)
    model_name = str(active_cfg.get("model") or "")
    metrics_summary = _format_engine_metrics_summary(profile)

    if use_cache:
        cached = _load_cache_entry(cache_key, state_dir=state_dir)
        if cached and isinstance(cached.get("structured"), dict):
            cached_model = str(cached.get("model") or model_name)
            merged_pos = (
                merge_ai_positioning(
                    profile["positioning"],
                    cached["structured"],
                    model=cached_model,
                    engine_metrics_summary=metrics_summary,
                )
                if isinstance(profile.get("positioning"), dict)
                else None
            )
            return {
                "structured": cached["structured"],
                "positioning": merged_pos,
                "model": cached_model,
                "cached": True,
                "engineMetricsSummary": metrics_summary,
            }

    prompt = build_rating_prompt(digest)
    resolved_model = model_name
    raw_text = ""
    if http_post is not None:
        resp = call_chat_completion(active_cfg, _SYSTEM_PROMPT, prompt, http_post=http_post, timeout=60.0)
        raw_text = str(resp.get("text") or "")
        resolved_model = str(resp.get("model") or model_name)
    else:
        buf: list[str] = []
        for ev in stream_chat_completion(active_cfg, _SYSTEM_PROMPT, prompt):
            if ev.get("model"):
                resolved_model = str(ev["model"])
            if ev["type"] == "delta":
                buf.append(ev["delta"])
        raw_text = "".join(buf).strip()

    structured, _ = split_structured_header_and_markdown(raw_text)
    if not isinstance(structured, dict) or not structured.get("contentReadiness"):
        raise RuntimeError("大模型未返回合法的结构化评级 JSON（缺少 contentReadiness）")

    if use_cache and http_post is None:
        _save_cache_entry(
            cache_key,
            {
                "structured": structured,
                "model": resolved_model,
                "engineMetricsSummary": metrics_summary,
            },
            state_dir=state_dir,
        )

    merged_pos = (
        merge_ai_positioning(
            profile["positioning"],
            structured,
            model=resolved_model,
            engine_metrics_summary=metrics_summary,
        )
        if isinstance(profile.get("positioning"), dict)
        else None
    )
    return {
        "structured": structured,
        "positioning": merged_pos,
        "model": resolved_model,
        "cached": False,
        "engineMetricsSummary": metrics_summary,
    }


def analyze_target_structured(
    profile: dict[str, Any],
    *,
    cfg: dict[str, Any] | None = None,
    state_dir: str = STATE_DIR,
    http_post: Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]] | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    """非流式兼容入口：有 http_post（单元测试）时走 call_chat_completion，生产环境复用流式聚合彻底防超时。"""
    active_cfg = cfg or load_llm_config(state_dir)
    if not active_cfg.get("enabled"):
        raise ValueError("尚未启用大模型分析（请先在「AI 设置」开启开关并配置 API Key）")

    if http_post is None:
        final_done: dict[str, Any] | None = None
        for ev in stream_analyze_with_llm(
            "target",
            profile=profile,
            cfg=active_cfg,
            state_dir=state_dir,
            use_cache=use_cache,
        ):
            if ev["type"] == "error":
                raise RuntimeError(ev["error"])
            if ev["type"] == "done":
                final_done = ev
        if not final_done:
            raise RuntimeError("大模型未返回完整结果")
        return {
            "text": final_done.get("text") or "",
            "reasoning": final_done.get("reasoning") or "",
            "structured": final_done.get("structured"),
            "model": final_done.get("model") or str(active_cfg.get("model") or ""),
            "usage": {},
            "cached": bool(final_done.get("cached")),
        }

    digest = build_target_digest(profile)
    cache_key = _digest_cache_key("target", digest, active_cfg)
    prompt = build_target_essence_prompt(digest)
    resp = call_chat_completion(active_cfg, _SYSTEM_PROMPT, prompt, http_post=http_post, timeout=90.0)
    structured, display_text = split_structured_header_and_markdown(resp["text"])
    result = {
        "text": display_text or resp["text"],
        "reasoning": resp.get("reasoning") or "",
        "structured": structured,
        "model": resp["model"],
        "usage": resp.get("usage") or {},
        "cached": False,
    }
    if use_cache and http_post is None and structured:
        _save_cache_entry(cache_key, result, state_dir=state_dir)
    return result


def analyze_with_llm(
    mode: str,
    *,
    profile: dict[str, Any] | None = None,
    gap_report: dict[str, Any] | None = None,
    copy_context: dict[str, Any] | None = None,
    price_snapshot: dict[str, Any] | None = None,
    essence_summary: str = "",
    league: str = "Standard",
    cfg: dict[str, Any] | None = None,
    state_dir: str = STATE_DIR,
    pob_sandbox: dict[str, Any] | None = None,
    http_post: Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    active_cfg = cfg or load_llm_config(state_dir)
    if mode != "test" and not active_cfg.get("enabled"):
        raise ValueError("尚未启用大模型分析（请先在「AI 设置」开启开关并配置 API Key）")

    if mode == "test":
        return call_chat_completion(
            active_cfg,
            "你是接口连通性检测助手。",
            "请简短回复“PoE2 Workbench 大模型连接正常”。",
            http_post=http_post,
            timeout=25.0,
        )

    if mode == "rate":
        if not profile:
            raise ValueError("缺少目标 BD 解析数据")
        return rate_target_with_ai(
            profile,
            cfg=active_cfg,
            state_dir=state_dir,
            http_post=http_post,
        )

    if mode == "target":
        if not profile:
            raise ValueError("缺少目标 BD 解析数据")
        return analyze_target_structured(
            profile,
            cfg=active_cfg,
            state_dir=state_dir,
            http_post=http_post,
        )

    if (http_post is None or (mode == "copy" and pob_sandbox is not None)) and mode in ("copy", "consult", "gap"):
        final_done: dict[str, Any] | None = None
        for ev in stream_analyze_with_llm(
            mode,
            profile=profile,
            gap_report=gap_report,
            copy_context=copy_context,
            price_snapshot=price_snapshot,
            essence_summary=essence_summary,
            league=league,
            cfg=active_cfg,
            state_dir=state_dir,
            pob_sandbox=pob_sandbox,
            http_post=http_post,
        ):
            if ev["type"] == "error":
                raise RuntimeError(ev["error"])
            if ev["type"] == "done":
                final_done = ev
        if not final_done:
            raise RuntimeError("大模型未返回任何结果")
        return {
            "text": final_done.get("text") or "",
            "reasoning": final_done.get("reasoning") or "",
            "model": final_done.get("model") or str(active_cfg.get("model") or ""),
            "usage": {},
            "cached": bool(final_done.get("cached")),
            "structured": final_done.get("structured"),
            "copyPlan": final_done.get("copyPlan"),
        }

    if mode == "copy":
        if not profile:
            raise ValueError("缺少目标 BD 解析数据")
        prompt = build_copy_plan_prompt(
            build_target_digest(profile),
            copy_context or {},
            price_snapshot=price_snapshot,
            essence_summary=essence_summary,
        )
        resp = call_chat_completion(active_cfg, _SYSTEM_PROMPT, prompt, http_post=http_post)
        raw_struct, md_text = split_copy_structured_and_markdown(resp.get("text") or "")
        copy_plan = (
            _sanitize_structured_copy_plan(
                raw_struct,
                copy_context=copy_context,
                league=league or (price_snapshot or {}).get("league") or "Standard",
                profile=profile,
            )
            if raw_struct
            else None
        )
        return {
            **resp,
            "text": md_text or resp.get("text") or "",
            "structured": raw_struct,
            "copyPlan": copy_plan,
        }

    if mode == "consult":
        prompt = build_consult_prompt(copy_context or {})
        resp = call_chat_completion(active_cfg, _SYSTEM_PROMPT, prompt, http_post=http_post)
        raw_struct, md_text = split_copy_structured_and_markdown(resp.get("text") or "")
        copy_plan = (
            _sanitize_structured_copy_plan(
                raw_struct,
                copy_context=copy_context,
                league=league or (price_snapshot or {}).get("league") or "Standard",
                profile=profile,
            )
            if raw_struct
            else None
        )
        return {
            **resp,
            "text": md_text or resp.get("text") or "",
            "structured": raw_struct,
            "copyPlan": copy_plan,
        }

    if mode == "gap":
        if not gap_report:
            raise ValueError("缺少自身 vs 目标差距诊断报告")
        digest = build_gap_digest(gap_report)
        prompt = (
            "请对比「我的 BD」与「目标 BD」的实算差距报告，给出可执行的追赶路线：\n"
            "1. 先处理哪些 0 成本或低成本问题（天赋、技能连法、抗性/精魂红线）；\n"
            "2. 按性价比排最值得优先更换的前 3~5 件装备或珠宝（说明原因和红线风险）；\n"
            "3. 总结当前阶段最卡脖子的一环是什么。\n\n"
            f"差距报告 JSON：\n{json.dumps(digest, ensure_ascii=False)}"
        )
        return call_chat_completion(active_cfg, _SYSTEM_PROMPT, prompt, http_post=http_post)

    raise ValueError(f"不支持的分析模式：{mode}")
