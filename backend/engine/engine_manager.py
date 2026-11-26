"""PoB2 计算内核自检、本机路径绑定与一键从 GitHub 拉取安装器。

能力：
  1. 启动自检：按 环境变量 > 用户已配置路径 > .env.local > 本软件托管目录(~/.poe2-workbench/pob2-engine) > 系统常见安装路径 扫描；
  2. 兼容 GitHub 源码布局 (src/HeadlessWrapper.lua + runtime/lua) 与官方安装版布局 (HeadlessWrapper.lua + lua)；
  3. 一键从 GitHub 下载锁定稳定版本（或 dev 最新版）Zipball，自动跳过大体积贴图/字体资源，
     仅提取计算内核脚本与数据并执行幂等 Lua 5.1 兼容补丁，零入侵用户已有 PoB2；
  4. 支持可选镜像前缀（方便国内网络加速）与线程安全安装进度查询。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from typing import Any, Callable

from backend.config import (
    REPO_ROOT,
    default_system_pob2_candidates,
    get_env,
    load_engine_config,
    managed_pob2_dir,
    pob2_root,
    pob2_src,
    save_engine_config,
)
from backend.engine.worker_pool import WorkerError, WorkerManager, find_luajit

UPSTREAM_REPO = "https://github.com/PathOfBuildingCommunity/PathOfBuilding-PoE2"
DEFAULT_PINNED_REF = "v0.23.1"
_LOCK_FILE = os.path.join(REPO_ROOT, "pob2-engine.lock")
_UA = "PoE2Workbench/1.0 (local PoB2 kernel installer)"

# 解压时自动跳过纯 GUI 贴图/图标/字体/音频大文件，将 449MB 体积压至 ~80-110MB 纯计算资产
_SKIP_EXTS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".dds",
    ".webp",
    ".tga",
    ".bmp",
    ".ico",
    ".ttf",
    ".otf",
    ".woff",
    ".woff2",
    ".mp3",
    ".ogg",
    ".wav",
    ".dll",
    ".exe",
)

_INSTALL_LOCK = threading.Lock()
_INSTALL_STATE: dict[str, Any] = {
    "status": "idle",  # idle | downloading | extracting | patching | verifying | done | error
    "progressPct": 0,
    "message": "",
    "ref": "",
    "downloadedBytes": 0,
    "totalBytes": 0,
    "extractedFiles": 0,
    "patchedFiles": 0,
    "error": "",
    "startedAt": 0,
    "finishedAt": 0,
}


def pinned_upstream_ref() -> str:
    """读取 pob2-engine.lock 中已验证通过的上游版本标签，兜底 DEFAULT_PINNED_REF。"""
    try:
        with open(_LOCK_FILE, encoding="utf-8") as f:
            data = json.load(f)
        ver = str(data.get("version") or "").strip()
        if ver:
            return ver
    except Exception:
        pass
    return DEFAULT_PINNED_REF


def get_install_state() -> dict[str, Any]:
    with _INSTALL_LOCK:
        return dict(_INSTALL_STATE)


def _set_install_state(**kwargs: Any) -> dict[str, Any]:
    with _INSTALL_LOCK:
        _INSTALL_STATE.update(kwargs)
        return dict(_INSTALL_STATE)


def reset_install_state_for_tests() -> None:
    with _INSTALL_LOCK:
        _INSTALL_STATE.update({
            "status": "idle",
            "progressPct": 0,
            "message": "",
            "ref": "",
            "downloadedBytes": 0,
            "totalBytes": 0,
            "extractedFiles": 0,
            "patchedFiles": 0,
            "error": "",
            "startedAt": 0,
            "finishedAt": 0,
        })


def inspect_pob2_dir(path: str) -> dict[str, Any]:
    """校验指定目录是否为合法 PoB2 内核，并返回布局、天赋树版本与资产完整度。"""
    expanded = os.path.abspath(os.path.expanduser(path)) if path else ""
    if not expanded or not os.path.isdir(expanded):
        return {
            "valid": False,
            "path": expanded,
            "srcDir": "",
            "layout": "missing",
            "treeVersions": [],
            "hasData": False,
            "reason": "目录不存在",
        }

    src_sub = os.path.join(expanded, "src")
    if os.path.isfile(os.path.join(src_sub, "HeadlessWrapper.lua")):
        src_dir = src_sub
        layout = "source"
    elif os.path.isfile(os.path.join(expanded, "HeadlessWrapper.lua")):
        src_dir = expanded
        layout = "installed"
    else:
        return {
            "valid": False,
            "path": expanded,
            "srcDir": "",
            "layout": "invalid",
            "treeVersions": [],
            "hasData": False,
            "reason": "未找到 HeadlessWrapper.lua（既不在根目录也不在 src/ 下）",
        }

    tree_versions: list[str] = []
    tree_dir = os.path.join(src_dir, "TreeData")
    if os.path.isdir(tree_dir):
        for entry in sorted(os.listdir(tree_dir)):
            if os.path.isfile(os.path.join(tree_dir, entry, "tree.json")):
                tree_versions.append(entry)

    has_data = os.path.isdir(os.path.join(src_dir, "Data"))
    has_runtime = os.path.isdir(os.path.join(expanded, "runtime", "lua")) or os.path.isdir(
        os.path.join(expanded, "lua")
    )

    return {
        "valid": True,
        "path": expanded,
        "srcDir": src_dir,
        "layout": layout,
        "treeVersions": tree_versions,
        "hasData": has_data,
        "hasRuntimeLua": has_runtime,
        "reason": "",
    }


def scan_local_candidates(state_dir: str | None = None) -> list[dict[str, Any]]:
    """扫描本机所有可能存在的 PoB2 安装/托管/配置目录，返回去重后的有效候选列表。"""
    raw_entries: list[tuple[str, str, str]] = []

    env_root = os.environ.get("POB2_ROOT", "").strip()
    if env_root:
        raw_entries.append(("env", "环境变量 POB2_ROOT", env_root))

    cfg = load_engine_config(state_dir)
    cfg_root = str(cfg.get("pob2Root") or "").strip()
    if cfg_root:
        raw_entries.append(("config", "工作台已绑定路径", cfg_root))

    dotenv_root = get_env("POB2_ROOT", "").strip()
    if dotenv_root and dotenv_root != env_root:
        raw_entries.append(("dotenv", ".env.local 配置路径", dotenv_root))

    raw_entries.append(("managed", "工作台一键托管目录", managed_pob2_dir(state_dir)))
    raw_entries.append(("repo", "仓库内嵌引擎目录", os.path.join(REPO_ROOT, "pob2-engine", "PathOfBuilding-PoE2")))

    for sys_path in default_system_pob2_candidates():
        raw_entries.append(("detected", "本机已安装 PoB2", sys_path))

    seen: set[str] = set()
    candidates: list[dict[str, Any]] = []
    for source_key, label, p in raw_entries:
        if not p:
            continue
        norm = os.path.abspath(os.path.expanduser(p))
        if norm in seen:
            continue
        seen.add(norm)
        info = inspect_pob2_dir(norm)
        if info["valid"]:
            candidates.append({
                "source": source_key,
                "label": label,
                **info,
            })
    return candidates


def _resolve_active_source(active_root: str, state_dir: str | None = None) -> str:
    norm = os.path.abspath(os.path.expanduser(active_root)) if active_root else ""
    if not norm:
        return "missing"
    env_direct = os.environ.get("POB2_ROOT", "").strip()
    if env_direct and os.path.abspath(os.path.expanduser(env_direct)) == norm:
        return "env"
    cfg_root = str(load_engine_config(state_dir).get("pob2Root") or "").strip()
    if cfg_root and os.path.abspath(os.path.expanduser(cfg_root)) == norm:
        return "config"
    if os.path.abspath(managed_pob2_dir(state_dir)) == norm:
        return "managed"
    dotenv_root = get_env("POB2_ROOT", "").strip()
    if dotenv_root and os.path.abspath(os.path.expanduser(dotenv_root)) == norm:
        return "dotenv"
    return "detected"


def _luajit_install_hint() -> str:
    if sys.platform == "darwin":
        return "macOS 请运行：brew install luajit（或在环境变量设置 POB2_LUAJIT）"
    if sys.platform == "win32":
        return "Windows 请将 luajit.exe 放到 PATH 或工作台根目录，或设置 POB2_LUAJIT"
    return "Linux 请安装 luajit（如 sudo apt install luajit）或设置 POB2_LUAJIT"


def detect_engine_status(state_dir: str | None = None) -> dict[str, Any]:
    """完整自检 PoB2 内核 + LuaJIT 运行时状态 + 本机候选目录 + 下载进度。"""
    active = pob2_root(state_dir=state_dir)
    info = inspect_pob2_dir(active)
    luajit_bin = find_luajit(state_dir=state_dir)
    candidates = scan_local_candidates(state_dir)
    cfg = load_engine_config(state_dir)

    pob2_found = bool(info["valid"])
    luajit_found = bool(luajit_bin)
    source = _resolve_active_source(active, state_dir) if pob2_found else "missing"

    return {
        "ready": pob2_found and luajit_found,
        "pob2Found": pob2_found,
        "luajitFound": luajit_found,
        "luajitPath": luajit_bin or "",
        "luajitHint": "" if luajit_found else _luajit_install_hint(),
        "activeRoot": info["path"] if pob2_found else active,
        "activeSrc": info["srcDir"] if pob2_found else pob2_src(active),
        "layout": info["layout"],
        "source": source,
        "treeVersions": info["treeVersions"],
        "hasData": info["hasData"],
        "installedRef": str(cfg.get("installedRef") or ""),
        "managedDir": managed_pob2_dir(state_dir),
        "candidates": candidates,
        "installState": get_install_state(),
        "pinnedVersion": pinned_upstream_ref(),
        "repoUrl": UPSTREAM_REPO,
    }


def _invalidate_data_caches() -> None:
    """切换或安装完 PoB2 内核后，清理依赖 POB2_SRC 的内存缓存。"""
    try:
        from backend.domain import weapon_bases
        weapon_bases._BASES_CACHE = None
    except Exception:
        pass
    try:
        from backend.domain.crafting import affix_pool
        affix_pool.clear_library_caches()
    except Exception:
        pass
    try:
        from backend.engine import rune_index
        rune_index.load_rune_index.cache_clear()
    except Exception:
        pass


def use_local_pob2(
    path: str,
    *,
    state_dir: str | None = None,
    runtime: Any = None,
    installed_ref: str = "",
) -> dict[str, Any]:
    """校验并绑定用户本机已有的 PoB2 目录，写入 ~/.poe2-workbench/engine_config.json。"""
    info = inspect_pob2_dir(path)
    if not info["valid"]:
        raise ValueError(f"无效的 PoB2 目录（{info['path'] or path}）：{info['reason']}")

    patch: dict[str, Any] = {
        "pob2Root": info["path"],
        "layout": info["layout"],
        "updatedAt": int(time.time() * 1000),
    }
    if installed_ref:
        patch["installedRef"] = installed_ref
    save_engine_config(patch, state_dir=state_dir)
    _invalidate_data_caches()

    # 若运行中的后端已启动旧 WorkerPool，关闭它以便下次模拟自动用新内核冷启动
    if runtime is not None:
        try:
            runtime.shutdown()
        except Exception:
            pass
        try:
            runtime.bus.publish("engine_updated", {"pob2Root": info["path"], "ready": True})
        except Exception:
            pass

    return detect_engine_status(state_dir=state_dir)


# ---------------------------------------------------------------------------
# 幂等 Lua 5.1 兼容自动补丁（仅作用于托管下载目录，绝不修改用户原版 PoB2）
# ---------------------------------------------------------------------------
_COMPAT_MARKER = "-- [poe2-workbench lua51 compat]"

_LUA51_PRELUDE = f"""{_COMPAT_MARKER}
if not table.unpack then table.unpack = unpack end
if not bit32 and bit then
  bit32 = {{
    band = bit.band, bor = bit.bor, bxor = bit.bxor, bnot = bit.bnot,
    lshift = bit.lshift, rshift = bit.rshift, arshift = bit.arshift,
  }}
end
"""


def _inject_prelude_preserving_shebang(hw_text: str) -> str:
    """将 Lua 5.1 兼容垫片插入 HeadlessWrapper.lua，保留首行 `#@` / `#!` shebang 在第 1 行。"""
    text = hw_text.lstrip("\ufeff")
    if _COMPAT_MARKER in text:
        # 自愈：若旧版补丁已把首行 `#@` / `#!` 顶到了后续行，将其还原到第 1 行
        lines = text.splitlines(keepends=True)
        for i in range(1, min(len(lines), 20)):
            if lines[i].startswith("#"):
                shebang = lines.pop(i)
                return shebang + "".join(lines)
        return hw_text

    if text.startswith("#"):
        first_line, sep, rest = text.partition("\n")
        nl = sep or "\n"
        return f"{first_line}{nl}{_LUA51_PRELUDE}\n{rest}"
    return _LUA51_PRELUDE + "\n" + text


def apply_lua51_compat_patches(src_dir: str) -> dict[str, Any]:
    """对托管下载的 PoB2 src/ 目录执行幂等 LuaJIT / Lua 5.1 兼容修补。

    修复点：
      1. HeadlessWrapper.lua 注入 table.unpack / bit32 兼容垫片（保留首行 `#@` / `#!` 不被顶到第 10 行）；
      2. 扫描核心脚本目录，将 table.unpack 调用做安全降级保护。
    """
    patched_files: list[str] = []
    hw_path = os.path.join(src_dir, "HeadlessWrapper.lua")
    if os.path.isfile(hw_path):
        with open(hw_path, encoding="utf-8", errors="replace") as f:
            hw_text = f.read()
        new_hw = _inject_prelude_preserving_shebang(hw_text)
        if new_hw != hw_text:
            with open(hw_path, "w", encoding="utf-8") as f:
                f.write(new_hw)
            patched_files.append("HeadlessWrapper.lua")

    # 扫描核心脚本目录，将仅在 Lua 5.3+ 存在的 math.tointeger 调用做安全降级保护
    for sub in ("Modules", "Classes"):
        sub_dir = os.path.join(src_dir, sub)
        if not os.path.isdir(sub_dir):
            continue
        for root, _, files in os.walk(sub_dir):
            for fname in files:
                if not fname.endswith(".lua"):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, encoding="utf-8", errors="replace") as f:
                        content = f.read()
                except OSError:
                    continue
                new_content = content
                if "table.unpack(" in new_content and "(table.unpack or unpack)(" not in new_content:
                    new_content = new_content.replace("table.unpack(", "(table.unpack or unpack)(")
                if new_content != content:
                    with open(fpath, "w", encoding="utf-8") as f:
                        f.write(new_content)
                    patched_files.append(os.path.relpath(fpath, src_dir).replace("\\", "/"))

    return {"patchedCount": len(patched_files), "patchedFiles": patched_files}


def build_github_zip_url(ref: str, *, mirror_prefix: str = "") -> str:
    """根据版本标签（v0.23.1）或分支名（dev / main）构造 GitHub Zipball 下载链接。"""
    clean_ref = (ref or pinned_upstream_ref()).strip()
    if re.match(r"^v?\d+\.\d+", clean_ref):
        raw_url = f"{UPSTREAM_REPO}/archive/refs/tags/{clean_ref}.zip"
    elif re.match(r"^[0-9a-f]{7,40}$", clean_ref, re.I):
        raw_url = f"{UPSTREAM_REPO}/archive/{clean_ref}.zip"
    else:
        raw_url = f"{UPSTREAM_REPO}/archive/refs/heads/{clean_ref}.zip"

    prefix = (mirror_prefix or "").strip()
    if not prefix:
        return raw_url
    if not prefix.endswith("/"):
        prefix += "/"
    return prefix + raw_url


def _should_extract_member(rel_path: str) -> bool:
    """仅保留 PoB2 无头计算所需的 src/**、runtime/lua/**、lua/**，跳过大体积贴图与可执行文件。"""
    norm = rel_path.replace("\\", "/").lstrip("/")
    if not norm or norm.endswith("/"):
        return False
    low = norm.lower()
    if any(low.endswith(ext) for ext in _SKIP_EXTS):
        return False
    if norm.startswith(("src/", "runtime/lua/", "lua/")):
        # 跳过纯 UI 渲染资源与导出工具测试贴图
        if norm.startswith(("src/Assets/", "src/TreeData/")) and not low.endswith((".lua", ".json")):
            return False
        return True
    if norm in ("HeadlessWrapper.lua", "manifest.xml", "changelog.txt"):
        return True
    return False


def _download_zip_with_progress(
    url: str,
    dest_path: str,
    *,
    timeout: float = 45.0,
    on_progress: Callable[[int, int], None] | None = None,
) -> int:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    total = 0
    downloaded = 0
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        cl = resp.headers.get("Content-Length")
        if cl and cl.isdigit():
            total = int(cl)
        with open(dest_path, "wb") as out:
            while True:
                chunk = resp.read(128 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                downloaded += len(chunk)
                if on_progress:
                    on_progress(downloaded, total)
    return downloaded


def install_from_github_zip(
    *,
    ref: str | None = None,
    mirror_prefix: str = "",
    state_dir: str | None = None,
    runtime: Any = None,
    downloader: Callable[[str, str, Callable[[int, int], None]], int] | None = None,
    verify_live: bool = True,
) -> dict[str, Any]:
    """从 GitHub 下载并精简安装 PoB2 计算内核到托管目录，自动打补丁并校验。"""
    use_ref = (ref or pinned_upstream_ref()).strip()
    url = build_github_zip_url(use_ref, mirror_prefix=mirror_prefix)
    target_dir = managed_pob2_dir(state_dir)

    _set_install_state(
        status="downloading",
        progressPct=2,
        message=f"正在从 GitHub 拉取 PoB2 ({use_ref})...",
        ref=use_ref,
        downloadedBytes=0,
        totalBytes=0,
        extractedFiles=0,
        patchedFiles=0,
        error="",
        startedAt=int(time.time() * 1000),
        finishedAt=0,
    )

    def _emit_bus(ev: str, data: dict[str, Any]) -> None:
        if runtime is not None and getattr(runtime, "bus", None) is not None:
            try:
                runtime.bus.publish(ev, data)
            except Exception:
                pass

    tmp_root = tempfile.mkdtemp(prefix="poe2_kernel_dl_")
    zip_path = os.path.join(tmp_root, "pob2.zip")
    stage_dir = os.path.join(tmp_root, "staged")
    os.makedirs(stage_dir, exist_ok=True)

    try:
        def _on_dl_progress(got: int, total: int) -> None:
            pct = min(68, int((got / total) * 68)) if total > 0 else min(65, 5 + got // (2 * 1024 * 1024))
            mb_got = got / (1024 * 1024)
            mb_tot = f" / {total / (1024 * 1024):.1f} MB" if total > 0 else ""
            _set_install_state(
                status="downloading",
                progressPct=max(2, pct),
                downloadedBytes=got,
                totalBytes=total,
                message=f"下载中：{mb_got:.1f} MB{mb_tot}（{use_ref}）",
            )

        if downloader is not None:
            downloader(url, zip_path, _on_dl_progress)
        else:
            _download_zip_with_progress(url, zip_path, on_progress=_on_dl_progress)

        _set_install_state(
            status="extracting",
            progressPct=70,
            message="正在精简解压计算内核脚本与数据（自动跳过大体积贴图）...",
        )

        extracted = 0
        with zipfile.ZipFile(zip_path, "r") as zf:
            members = zf.infolist()
            # 识别 GitHub zipball 顶层目录前缀（如 PathOfBuilding-PoE2-0.23.1/）
            top_prefix = ""
            for m in members:
                parts = m.filename.replace("\\", "/").split("/", 1)
                if len(parts) == 2 and parts[0]:
                    top_prefix = parts[0] + "/"
                    break

            kept_members: list[tuple[zipfile.ZipInfo, str]] = []
            for m in members:
                fname = m.filename.replace("\\", "/")
                rel = fname[len(top_prefix) :] if (top_prefix and fname.startswith(top_prefix)) else fname
                if _should_extract_member(rel):
                    kept_members.append((m, rel))

            if not kept_members:
                raise RuntimeError("压缩包内未找到 PoB2 内核脚本（src/HeadlessWrapper.lua）")

            total_kept = len(kept_members)
            stage_abs = os.path.abspath(stage_dir)
            for idx, (m, rel) in enumerate(kept_members, start=1):
                dest = os.path.abspath(os.path.join(stage_abs, rel))
                # 防止 Zip Slip 路径穿越
                if not dest.startswith(stage_abs + os.sep):
                    continue
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with zf.open(m, "r") as src_f, open(dest, "wb") as dst_f:
                    shutil.copyfileobj(src_f, dst_f)
                extracted += 1
                if idx % 200 == 0 or idx == total_kept:
                    pct = 70 + int((idx / total_kept) * 18)
                    _set_install_state(
                        status="extracting",
                        progressPct=min(88, pct),
                        extractedFiles=extracted,
                        message=f"精简解压中：{extracted}/{total_kept} 个核心文件...",
                    )

        info = inspect_pob2_dir(stage_dir)
        if not info["valid"]:
            raise RuntimeError(f"解压后的目录缺少 HeadlessWrapper.lua：{info['reason']}")

        _set_install_state(
            status="patching",
            progressPct=90,
            message="正在应用 LuaJIT / Lua 5.1 无头兼容补丁...",
        )
        patch_res = apply_lua51_compat_patches(info["srcDir"])

        # 原子替换托管目录（防止中途失败损坏已有内核）
        os.makedirs(os.path.dirname(target_dir), exist_ok=True)
        backup_dir = f"{target_dir}.bak_{os.getpid()}"
        if os.path.exists(backup_dir):
            shutil.rmtree(backup_dir, ignore_errors=True)
        had_old = os.path.isdir(target_dir)
        try:
            if had_old:
                os.replace(target_dir, backup_dir)
            shutil.move(stage_dir, target_dir)
            if had_old and os.path.isdir(backup_dir):
                shutil.rmtree(backup_dir, ignore_errors=True)
        except Exception:
            if had_old and os.path.isdir(backup_dir) and not os.path.isdir(target_dir):
                os.replace(backup_dir, target_dir)
            raise

        _set_install_state(
            status="verifying",
            progressPct=95,
            patchedFiles=patch_res["patchedCount"],
            message="正在绑定内核路径并刷新本地缓存...",
        )
        status_after = use_local_pob2(
            target_dir,
            state_dir=state_dir,
            runtime=runtime,
            installed_ref=use_ref,
        )

        # 若本机已有 LuaJIT，顺带做一次快速冒烟校验（不阻断安装本身，但明确反馈结果）
        verify_note = ""
        if verify_live and status_after["luajitFound"]:
            smoke = test_live_engine()
            if smoke["ok"]:
                verify_note = f"（LuaJIT 冒烟通过，冷启动 {smoke['bootMs']:.0f}ms）"
            else:
                verify_note = f"（已安装完毕，LuaJIT 冒烟提醒：{smoke['error']}）"

        done_msg = f"PoB2 内核 ({use_ref}) 安装完成：提取 {extracted} 个核心文件，兼容补丁 {patch_res['patchedCount']} 处{verify_note}"
        _set_install_state(
            status="done",
            progressPct=100,
            extractedFiles=extracted,
            patchedFiles=patch_res["patchedCount"],
            message=done_msg,
            finishedAt=int(time.time() * 1000),
        )
        _emit_bus("engine_updated", {"pob2Root": target_dir, "ref": use_ref, "ready": status_after["ready"]})
        return detect_engine_status(state_dir=state_dir)
    except Exception as exc:  # noqa: BLE001
        err_msg = str(exc)
        if isinstance(exc, urllib.error.URLError):
            err_msg = f"下载失败（网络不可达或超时，可尝试勾选国内镜像加速源）：{exc}"
        _set_install_state(
            status="error",
            error=err_msg,
            message=f"安装失败：{err_msg}",
            finishedAt=int(time.time() * 1000),
        )
        raise
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def start_async_install(
    *,
    ref: str | None = None,
    mirror_prefix: str = "",
    state_dir: str | None = None,
    runtime: Any = None,
) -> dict[str, Any]:
    """在后台线程启动 GitHub 拉取安装，立即返回当前进度状态供前端轮询。"""
    with _INSTALL_LOCK:
        if _INSTALL_STATE["status"] in ("downloading", "extracting", "patching", "verifying"):
            return dict(_INSTALL_STATE)
        _INSTALL_STATE.update({
            "status": "downloading",
            "progressPct": 1,
            "message": "正在初始化 GitHub 下载任务...",
            "ref": (ref or pinned_upstream_ref()).strip(),
            "error": "",
            "startedAt": int(time.time() * 1000),
            "finishedAt": 0,
        })

    def _runner() -> None:
        try:
            install_from_github_zip(
                ref=ref,
                mirror_prefix=mirror_prefix,
                state_dir=state_dir,
                runtime=runtime,
                verify_live=True,
            )
        except Exception:
            pass

    t = threading.Thread(target=_runner, name="poe2-kernel-installer", daemon=True)
    t.start()
    return get_install_state()


def _find_brew() -> str | None:
    for cand in (shutil.which("brew"), "/opt/homebrew/bin/brew", "/usr/local/bin/brew"):
        if cand and os.path.isfile(cand):
            return cand
    return None


def install_luajit(
    *,
    state_dir: str | None = None,
    runtime: Any = None,
) -> dict[str, Any]:
    """一键检测或调用系统包管理器安装 LuaJIT，并持久化路径。"""
    existing = find_luajit(state_dir=state_dir)
    if existing:
        return {
            "installed": False,
            "alreadyPresent": True,
            "luajitPath": existing,
            "message": f"本机已就绪 LuaJIT：{existing}",
            "engine": detect_engine_status(state_dir=state_dir),
        }

    cmd: list[str] | None = None
    fallback_hint = _luajit_install_hint()
    if sys.platform == "darwin":
        brew = _find_brew()
        if brew:
            cmd = [brew, "install", "luajit"]
        else:
            fallback_hint = (
                "未检测到 Homebrew。请先安装 Homebrew（/bin/bash -c \"$(curl -fsSL "
                "https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\"），"
                "再运行 brew install luajit；或直接下载 GitHub Releases 独立安装包。"
            )
    elif sys.platform == "win32":
        for tool, args in (
            ("scoop", ["install", "luajit"]),
            ("choco", ["install", "luajit", "-y"]),
        ):
            exe = shutil.which(tool)
            if exe:
                cmd = [exe, *args]
                break
        if not cmd:
            fallback_hint = (
                "Windows 推荐直接下载 GitHub Releases 独立安装包（已内置 luajit.exe），"
                "或手动安装 scoop/choco 后安装 luajit，或将 luajit.exe 放到 ~/.poe2-workbench/bin/。"
            )
    else:
        for tool, args in (
            ("apt-get", ["install", "-y", "luajit"]),
            ("dnf", ["install", "-y", "luajit"]),
            ("pacman", ["-S", "--noconfirm", "luajit"]),
        ):
            exe = shutil.which(tool)
            if exe and hasattr(os, "geteuid") and os.geteuid() == 0:
                cmd = [exe, *args]
                break

    if not cmd:
        return {
            "installed": False,
            "alreadyPresent": False,
            "luajitPath": "",
            "message": fallback_hint,
            "engine": detect_engine_status(state_dir=state_dir),
        }

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "installed": False,
            "alreadyPresent": False,
            "luajitPath": "",
            "message": f"自动安装 LuaJIT 执行失败：{exc}。{fallback_hint}",
            "engine": detect_engine_status(state_dir=state_dir),
        }

    found = find_luajit(state_dir=state_dir)
    if proc.returncode == 0 and found:
        save_engine_config({"luajitPath": found, "updatedAt": int(time.time() * 1000)}, state_dir=state_dir)
        st = detect_engine_status(state_dir=state_dir)
        if runtime is not None and getattr(runtime, "bus", None) is not None:
            try:
                runtime.bus.publish("engine_updated", {"luajitPath": found, "ready": st["ready"]})
            except Exception:
                pass
        return {
            "installed": True,
            "alreadyPresent": False,
            "luajitPath": found,
            "message": f"LuaJIT 安装成功：{found}",
            "engine": st,
        }

    err_tail = (proc.stderr or proc.stdout or "").strip()[-400:]
    return {
        "installed": False,
        "alreadyPresent": False,
        "luajitPath": found or "",
        "message": f"自动安装未完成（退出码 {proc.returncode}）：{err_tail or fallback_hint}",
        "engine": detect_engine_status(state_dir=state_dir),
    }


def test_live_engine() -> dict[str, Any]:
    """使用当前配置的 PoB2 内核 + LuaJIT 拉起一次最小 PING 冒烟测试。"""
    w = WorkerManager(timeout=20.0, startup_timeout=30.0)
    try:
        w.start()
        pong = w.ping(timeout=5.0)
        return {
            "ok": bool(pong),
            "bootMs": round(w.boot_ms or 0.0, 1),
            "rssMb": round(w.rss_mb(), 1),
            "error": "" if pong else "PING 无响应",
        }
    except WorkerError as exc:
        return {"ok": False, "bootMs": 0.0, "rssMb": 0.0, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "bootMs": 0.0, "rssMb": 0.0, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        w.close()
