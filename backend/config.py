"""统一配置加载：优先读取环境变量，其次读取仓库根目录的 .env.local。

不依赖第三方库，保证在全新 Python 3 环境下开箱即用。
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
from functools import lru_cache
from typing import Any

def is_frozen() -> bool:
    """是否处于 PyInstaller 等独立打包可执行文件运行态。"""
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> str:
    """返回内置只读资产根目录：源码态为仓库根目录，打包态为 PyInstaller 解压目录。"""
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            return os.path.abspath(str(meipass))
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


REPO_ROOT = bundle_root()
ENV_LOCAL_PATH = os.path.join(
    os.path.dirname(os.path.abspath(sys.executable)) if is_frozen() else REPO_ROOT,
    ".env.local",
)
WORKBENCH_STATE_DIR = os.path.join(os.path.expanduser("~"), ".poe2-workbench")
ENGINE_CONFIG_FILENAME = "engine_config.json"
MANAGED_POB2_DIRNAME = "pob2-engine"
MANAGED_BIN_DIRNAME = "bin"
_ENGINE_CFG_LOCK = threading.Lock()


def workbench_state_dir() -> str:
    return get_env("POE2_STATE_DIR", "") or WORKBENCH_STATE_DIR


def engine_config_path(state_dir: str | None = None) -> str:
    base = state_dir or workbench_state_dir()
    return os.path.join(base, ENGINE_CONFIG_FILENAME)


def managed_pob2_dir(state_dir: str | None = None) -> str:
    base = state_dir or workbench_state_dir()
    return os.path.join(base, MANAGED_POB2_DIRNAME)


def managed_bin_dir(state_dir: str | None = None) -> str:
    base = state_dir or workbench_state_dir()
    return os.path.join(base, MANAGED_BIN_DIRNAME)


def desktop_web_dist_dir() -> str:
    """返回已编译桌面前端静态资源目录（支持源码构建目录与打包资源目录）。"""
    env_dir = get_env("POE2_WEB_DIST", "").strip()
    if env_dir:
        expanded = os.path.abspath(os.path.expanduser(env_dir))
        if os.path.isfile(os.path.join(expanded, "index.html")):
            return expanded
    candidates = [
        os.path.join(REPO_ROOT, "desktop", "web", "dist"),
        os.path.join(REPO_ROOT, "web_dist"),
    ]
    if is_frozen():
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        candidates.extend([
            os.path.join(exe_dir, "web_dist"),
            os.path.join(exe_dir, "desktop", "web", "dist"),
        ])
    for cand in candidates:
        if os.path.isfile(os.path.join(cand, "index.html")):
            return cand
    return candidates[0]


def load_engine_config(state_dir: str | None = None) -> dict[str, Any]:
    path = engine_config_path(state_dir)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_engine_config(patch: dict[str, Any], state_dir: str | None = None) -> dict[str, Any]:
    path = engine_config_path(state_dir)
    with _ENGINE_CFG_LOCK:
        cur = load_engine_config(state_dir)
        cur.update(patch)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cur, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        return cur


def _has_pob2_headless(root_dir: str) -> bool:
    if not root_dir:
        return False
    return os.path.isfile(os.path.join(root_dir, "src", "HeadlessWrapper.lua")) or os.path.isfile(
        os.path.join(root_dir, "HeadlessWrapper.lua")
    )


def default_system_pob2_candidates() -> list[str]:
    """返回各操作系统上官方 Path of Building (PoE2) 常见安装路径列表。"""
    home = os.path.expanduser("~")
    cands: list[str] = []
    if sys.platform == "win32":
        for env_key in ("APPDATA", "LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(env_key)
            if base:
                cands.append(os.path.join(base, "Path of Building Community (PoE2)"))
                cands.append(os.path.join(base, "PathOfBuilding-PoE2"))
        cands.append(os.path.join(home, "Path of Building Community (PoE2)"))
    elif sys.platform == "darwin":
        cands.extend([
            os.path.join(home, "Library", "Application Support", "Path of Building Community (PoE2)"),
            "/Applications/PathOfBuilding-PoE2.app/Contents/Resources",
            "/Applications/Path of Building Community (PoE2).app/Contents/Resources",
        ])
    else:
        cands.extend([
            os.path.join(home, ".local", "share", "Path of Building Community (PoE2)"),
            os.path.join(home, "PathOfBuilding-PoE2"),
        ])
    return cands


@lru_cache(maxsize=1)
def _load_dotenv_file(path: str = ENV_LOCAL_PATH) -> dict[str, str]:
    values: dict[str, str] = {}
    if not os.path.isfile(path):
        return values
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k:
                values[k] = v
    return values


def get_env(key: str, default: str = "") -> str:
    if key in os.environ and os.environ[key] != "":
        return os.environ[key]
    return _load_dotenv_file().get(key, default)


def _detect_total_ram_gb() -> float:
    """跨平台探测本机总物理内存（GB），零第三方依赖。"""
    try:
        if sys.platform == "darwin":
            out = subprocess.check_output(
                ["sysctl", "-n", "hw.memsize"],
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=2,
            ).strip()
            if out:
                return round(int(out) / (1024 ** 3), 1)
        elif sys.platform.startswith("linux"):
            with open("/proc/meminfo", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        return round(kb / (1024 ** 2), 1)
        elif sys.platform == "win32":
            class _MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            st = _MemoryStatusEx()
            st.dwLength = ctypes.sizeof(_MemoryStatusEx)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return round(st.ullTotalPhys / (1024 ** 3), 1)
    except Exception:
        pass
    return 0.0


def recommend_worker_config(cpu_count: int, total_ram_gb: float) -> dict[str, Any]:
    """按 CPU 核数与物理内存自动给出不卡系统、不爆内存的推荐 Worker 档位。"""
    cpus = max(1, int(cpu_count or 4))
    cpu_cap = max(2, cpus // 2)

    # 每个活跃 LuaJIT Worker 峰值约 450~550MB，为系统+浏览器+游戏留足余量
    if total_ram_gb <= 0:
        ram_cap = 3
        tier = "standard"
    elif total_ram_gb <= 8.5:
        ram_cap = 2
        tier = "low-ram"
    elif total_ram_gb <= 12.5:
        ram_cap = 3
        tier = "compact"
    elif total_ram_gb <= 18.0:
        ram_cap = 4
        tier = "standard"
    elif total_ram_gb <= 26.0:
        ram_cap = 5
        tier = "performance"
    else:
        ram_cap = 6
        tier = "workstation"

    rec_max = max(2, min(6, cpu_cap, ram_cap))
    # 低内存机器更快回收突发进程（30s），常规/高配机器 60s
    rec_burst_idle = 30.0 if ram_cap <= 2 else 60.0
    return {
        "cpuCount": cpus,
        "totalRamGb": total_ram_gb,
        "tier": tier,
        "recommendedWorkers": rec_max,
        "recommendedBurstIdleSec": rec_burst_idle,
    }


@lru_cache(maxsize=1)
def hardware_profile() -> dict[str, Any]:
    return recommend_worker_config(os.cpu_count() or 4, _detect_total_ram_gb())


def pob2_root(state_dir: str | None = None) -> str:
    # 1. 显式进程环境变量优先
    env_direct = os.environ.get("POB2_ROOT", "").strip()
    if env_direct:
        return os.path.expanduser(env_direct)
    # 2. 用户在桌面端内核向导中保存的路径（本地目录或一键下载的托管内核）
    cfg = load_engine_config(state_dir)
    cfg_root = str(cfg.get("pob2Root") or "").strip()
    if cfg_root:
        expanded = os.path.expanduser(cfg_root)
        if _has_pob2_headless(expanded):
            return expanded
    # 3. 仓库根目录 .env.local 配置
    dotenv_root = _load_dotenv_file().get("POB2_ROOT", "").strip()
    if dotenv_root:
        return os.path.expanduser(dotenv_root)
    # 4. 本软件托管下载目录 ~/.poe2-workbench/pob2-engine
    managed = managed_pob2_dir(state_dir)
    if _has_pob2_headless(managed):
        return managed
    # 5. 自动发现本机已安装的 PoB2
    for cand in default_system_pob2_candidates():
        if _has_pob2_headless(cand):
            return cand
    return os.path.join(REPO_ROOT, "pob2-engine", "PathOfBuilding-PoE2")


def pob2_src(root: str | None = None) -> str:
    r = os.path.expanduser(root) if root else pob2_root()
    src_sub = os.path.join(r, "src")
    if os.path.isfile(os.path.join(src_sub, "HeadlessWrapper.lua")):
        return src_sub
    if os.path.isfile(os.path.join(r, "HeadlessWrapper.lua")):
        return r
    return src_sub


def pob2_runtime_lua_dirs(root: str | None = None) -> list[str]:
    """返回兼容 GitHub 源码结构 (runtime/lua) 与官方安装版结构 (lua) 的 Lua 模块目录列表。"""
    r = os.path.expanduser(root) if root else pob2_root()
    dirs: list[str] = []
    for rel in (os.path.join("runtime", "lua"), "lua"):
        p = os.path.join(r, rel)
        if os.path.isdir(p) and p not in dirs:
            dirs.append(p)
    if not dirs:
        dirs.append(os.path.join(r, "runtime", "lua"))
    return dirs


def host() -> str:
    return get_env("POE2_HOST", "127.0.0.1")


def port() -> int:
    return int(get_env("POE2_PORT", "8788"))


def workers_max() -> int:
    raw = get_env("POE2_WORKERS", "").strip().lower()
    if raw and raw != "auto":
        return max(1, int(raw))
    return int(hardware_profile()["recommendedWorkers"])


def workers_min() -> int:
    return max(0, int(get_env("POE2_WORKERS_MIN", "1")))


def burst_idle_seconds() -> float:
    raw = get_env("POE2_BURST_IDLE_SEC", "").strip().lower()
    if raw and raw != "auto":
        return max(0.0, float(raw))
    return float(hardware_profile()["recommendedBurstIdleSec"])


def idle_minutes() -> float:
    return max(0.0, float(get_env("POE2_IDLE_MIN", "30")))


def allowed_origins_raw() -> str:
    return get_env("POE2_ALLOWED_ORIGINS", "chrome-extension://*")
