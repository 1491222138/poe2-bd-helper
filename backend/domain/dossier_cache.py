"""目标 BD 全量消融账本的分片持久化缓存模块（按 buildHash 单文件分片 + 旧总表自动迁移）。"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from typing import Any

from backend.domain.common import safe_num as _num
from backend.engine import pob_parser, runefix
from backend.market.price_store import STATE_DIR

DOSSIER_CACHE_FILENAME = "ablation_dossier_cache.json"
DOSSIER_SHARD_DIRNAME = "dossier_cache"
DOSSIER_VERSION = "v4-socketed-items-ascendancy-skill-defense"
_CACHE_MAX_ENTRIES = 16


def _cache_path(state_dir: str = STATE_DIR) -> str:
    return os.path.join(state_dir, DOSSIER_CACHE_FILENAME)


def _shard_dir(state_dir: str = STATE_DIR) -> str:
    return os.path.join(state_dir, DOSSIER_SHARD_DIRNAME)


def _shard_path(bhash: str, state_dir: str = STATE_DIR) -> str:
    safe_hash = re.sub(r"[^a-zA-Z0-9_-]", "", str(bhash or ""))[:32] or "unknown"
    return os.path.join(_shard_dir(state_dir), f"dossier_{safe_hash}.json")


def _price_signature(
    price_snapshot: dict[str, Any] | None,
    price_overrides: dict[str, float] | None,
    *,
    league: str = "",
    ninja_index: dict[str, Any] | None = None,
) -> str:
    p_ts = (price_snapshot or {}).get("updatedAt") or 0
    p_cnt = len((price_snapshot or {}).get("prices") or {}) + len((price_snapshot or {}).get("entries") or [])
    n_cnt = int((ninja_index or {}).get("itemCount") or len((ninja_index or {}).get("byName") or {}))
    n_ts = ((ninja_index or {}).get("updatedAt") or 0) if n_cnt > 0 else 0
    n_rate = round(float((ninja_index or {}).get("exaltPerDivine") or 0.0), 2) if n_cnt > 0 else 0.0
    ov = sorted((str(k), round(float(v), 3)) for k, v in (price_overrides or {}).items() if isinstance(v, (int, float)))
    return f"{league}|{p_ts}:{p_cnt}|{n_ts}:{n_cnt}:{n_rate}|{ov}"


def compute_build_hash(norm_xml: str) -> str:
    return hashlib.sha256(norm_xml.encode("utf-8")).hexdigest()[:24]


def _base_cache_key(norm_xml: str) -> str:
    """消融底座只绑定消融逻辑版本与规范化后的 PoB2 XML 哈希，与赛季/实时物价彻底解耦。"""
    return f"{DOSSIER_VERSION}:{compute_build_hash(norm_xml)}"


def _dossier_cache_key(
    norm_xml: str,
    price_snapshot: dict[str, Any] | None = None,
    price_overrides: dict[str, float] | None = None,
) -> str:
    return _base_cache_key(norm_xml)


def _is_compatible_cached_dossier(entry: Any, bhash: str) -> bool:
    if not isinstance(entry, dict) or entry.get("buildHash") != bhash:
        return False
    if not isinstance(entry.get("items"), list) or not isinstance(entry.get("skills"), list):
        return False
    if not isinstance(entry.get("summary"), dict):
        return False
    return entry.get("version") == DOSSIER_VERSION


def _load_base_cache(norm_xml: str, state_dir: str = STATE_DIR) -> tuple[dict[str, Any] | None, bool]:
    """读取本地持久化的目标 BD 消融底座（优先单文件分片，兼容旧单文件总缓存并自动迁移）。
    返回 (dossier, migrated_from_legacy)。
    """
    bhash = compute_build_hash(norm_xml)
    spath = _shard_path(bhash, state_dir)
    if os.path.isfile(spath):
        try:
            with open(spath, encoding="utf-8") as f:
                direct = json.load(f)
            if _is_compatible_cached_dossier(direct, bhash):
                return direct, False
        except Exception:
            pass

    path = _cache_path(state_dir)
    if not os.path.isfile(path):
        return None, False
    stable_key = _base_cache_key(norm_xml)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None, False
        direct = data.get(stable_key)
        if _is_compatible_cached_dossier(direct, bhash):
            return copy.deepcopy(direct), True
        matches = [v for v in data.values() if _is_compatible_cached_dossier(v, bhash)]
        if matches:
            matches.sort(key=lambda x: int(x.get("generatedAt") or 0), reverse=True)
            return copy.deepcopy(matches[0]), True
    except Exception:
        return None, False
    return None, False


def _load_cache(key: str, state_dir: str = STATE_DIR) -> dict[str, Any] | None:
    if ":" in str(key or ""):
        bhash = str(key).split(":", 1)[1]
        spath = _shard_path(bhash, state_dir)
        if os.path.isfile(spath):
            try:
                with open(spath, encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    return loaded
            except Exception:
                pass
    path = _cache_path(state_dir)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get(key), dict):
            return copy.deepcopy(data[key])
    except Exception:
        return None
    return None


def get_cached_dossier_by_build(
    code_or_xml: str,
    *,
    state_dir: str = STATE_DIR,
) -> dict[str, Any] | None:
    """供 AI 顾问 / 抄作业 / 市集直达模块按 PoB2 码快速读取已缓存的消融剖析账本（不触发重算）。"""
    try:
        xml = pob_parser.decode_pob(code_or_xml)
        norm_xml, _ = runefix.normalize_runes(xml)
    except Exception:
        return None
    cached, _ = _load_base_cache(norm_xml, state_dir=state_dir)
    return cached


def get_cached_base_sim(
    code_or_xml: str,
    *,
    state_dir: str = STATE_DIR,
    require_mobility_fields: bool = True,
) -> dict[str, Any] | None:
    """读取目标 BD 本地缓存的基准引擎模拟结果，供首页秒开免冷启动。"""
    cached = get_cached_dossier_by_build(code_or_xml, state_dir=state_dir)
    if isinstance(cached, dict) and isinstance(cached.get("baseSim"), dict) and cached["baseSim"].get("dps") is not None:
        base_sim = dict(cached["baseSim"])
        if require_mobility_fields and int(_num(base_sim.get("metricsVersion"), 0)) < 5:
            return None
        return base_sim
    return None


def _prune_shard_cache(sdir: str) -> None:
    try:
        entries: list[tuple[float, str]] = []
        for fn in os.listdir(sdir):
            if not (fn.startswith("dossier_") and fn.endswith(".json")):
                continue
            fp = os.path.join(sdir, fn)
            try:
                entries.append((os.path.getmtime(fp), fp))
            except OSError:
                continue
        if len(entries) > _CACHE_MAX_ENTRIES:
            entries.sort(key=lambda x: x[0])
            for _, old_fp in entries[: len(entries) - _CACHE_MAX_ENTRIES]:
                try:
                    os.remove(old_fp)
                except OSError:
                    pass
    except Exception:
        pass


def _save_cache(key: str, dossier: dict[str, Any], state_dir: str = STATE_DIR) -> None:
    """按 buildHash 单文件分片持久化消融底座，避免缓存命中时反复读写 16 份全量 JSON。"""
    bhash = str(dossier.get("buildHash") or "")
    if not bhash and isinstance(key, str) and ":" in key:
        bhash = key.split(":", 1)[1]
    if bhash:
        sdir = _shard_dir(state_dir)
        os.makedirs(sdir, exist_ok=True)
        spath = _shard_path(bhash, state_dir)
        tmp = f"{spath}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dossier, f, ensure_ascii=False)
        os.replace(tmp, spath)
        _prune_shard_cache(sdir)
        return

    os.makedirs(state_dir, exist_ok=True)
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
    data[str(key)] = dossier
    if len(data) > _CACHE_MAX_ENTRIES:
        ordered = sorted(data.items(), key=lambda kv: int((kv[1] or {}).get("generatedAt") or 0))
        for old_k, _ in ordered[: len(data) - _CACHE_MAX_ENTRIES]:
            data.pop(old_k, None)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def update_cached_base_sim(
    code_or_xml: str,
    base_sim: dict[str, Any],
    *,
    state_dir: str = STATE_DIR,
) -> bool:
    """在不重跑全量消融的前提下，将最新单次 PoB2 实算（含位移速度等）回写进本地底座缓存。"""
    if not isinstance(base_sim, dict) or base_sim.get("dps") is None:
        return False
    try:
        xml = pob_parser.decode_pob(code_or_xml)
        norm_xml, _ = runefix.normalize_runes(xml)
    except Exception:
        return False
    cached, _ = _load_base_cache(norm_xml, state_dir=state_dir)
    if not isinstance(cached, dict):
        return False
    cached["baseSim"] = dict(base_sim)
    _save_cache(_base_cache_key(norm_xml), cached, state_dir=state_dir)
    return True
