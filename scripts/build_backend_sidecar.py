#!/usr/bin/env python3
"""将纯 Python 统一后端打包为独立免环境可执行文件，供 Tauri 2 桌面安装包内嵌使用。

用法：
    python3 scripts/build_backend_sidecar.py
    python3 scripts/build_backend_sidecar.py --bundle-luajit
    python3 scripts/build_backend_sidecar.py --luajit-path /opt/homebrew/bin/luajit

产物默认输出至：
    desktop/src-tauri/resources/backend/poe2-workbench-backend(.exe)
    desktop/src-tauri/resources/bin/luajit(.exe)   # 启用 --bundle-luajit 时
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESOURCES_ROOT = os.path.join(REPO_ROOT, "desktop", "src-tauri", "resources")
BACKEND_OUT_DIR = os.path.join(RESOURCES_ROOT, "backend")
BIN_OUT_DIR = os.path.join(RESOURCES_ROOT, "bin")
BUILD_TMP_DIR = os.path.join(REPO_ROOT, ".pyinstaller-build")


def _add_data_arg(src: str, dest: str) -> str:
    sep = ";" if sys.platform == "win32" else ":"
    return f"{src}{sep}{dest}"


def _copy_luajit(explicit_path: str | None) -> None:
    exe_name = "luajit.exe" if sys.platform == "win32" else "luajit"
    candidates = [
        explicit_path,
        os.environ.get("POB2_LUAJIT"),
        shutil.which("luajit"),
        "/opt/homebrew/bin/luajit",
        "/usr/local/bin/luajit",
        "/usr/bin/luajit",
    ]
    src = next((p for p in candidates if p and os.path.isfile(p)), None)
    if not src:
        raise FileNotFoundError("未找到本机 LuaJIT 可执行文件，无法执行 --bundle-luajit")

    os.makedirs(BIN_OUT_DIR, exist_ok=True)
    real_src = os.path.realpath(src)
    dst = os.path.join(BIN_OUT_DIR, exe_name)
    shutil.copy2(real_src, dst)
    if sys.platform != "win32":
        os.chmod(dst, 0o755)
    print(f"✔ 已内嵌 LuaJIT：{real_src} -> {dst}")

    # Windows 下若同目录存在 lua51.dll 等运行时 DLL，一并复制避免缺库
    if sys.platform == "win32":
        src_dir = os.path.dirname(real_src)
        for fname in os.listdir(src_dir):
            low = fname.lower()
            if low.endswith(".dll") and ("lua" in low or "gcc" in low or "winpthread" in low):
                shutil.copy2(os.path.join(src_dir, fname), os.path.join(BIN_OUT_DIR, fname))
                print(f"✔ 已内嵌依赖 DLL：{fname}")


def main() -> None:
    parser = argparse.ArgumentParser(description="构建 Tauri 内嵌 Python 后端 Sidecar")
    parser.add_argument("--bundle-luajit", action="store_true", help="将本机 luajit(.exe) 一并复制到 Tauri resources/bin")
    parser.add_argument("--luajit-path", default="", help="显式指定要打包的 luajit(.exe) 路径")
    parser.add_argument("--onefile", action="store_true", help="使用 PyInstaller --onefile（默认 --onedir 启动更快）")
    args = parser.parse_args()

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("错误：未安装 PyInstaller，请先运行 `python3 -m pip install pyinstaller`。", file=sys.stderr)
        sys.exit(1)

    if os.path.isdir(BACKEND_OUT_DIR):
        shutil.rmtree(BACKEND_OUT_DIR)
    os.makedirs(RESOURCES_ROOT, exist_ok=True)
    os.makedirs(BUILD_TMP_DIR, exist_ok=True)

    entry = os.path.join(REPO_ROOT, "backend", "api", "server.py")
    data_pairs = [
        (os.path.join(REPO_ROOT, "backend", "engine", "persistent_worker.lua"), os.path.join("backend", "engine")),
        (os.path.join(REPO_ROOT, "backend", "data", "affix_library.json"), os.path.join("backend", "data")),
        (os.path.join(REPO_ROOT, "backend", "data", "trade_stats_cache.json"), os.path.join("backend", "data")),
        (os.path.join(REPO_ROOT, "pob2-engine.lock"), "."),
    ]
    web_dist = os.path.join(REPO_ROOT, "desktop", "web", "dist")
    if os.path.isfile(os.path.join(web_dist, "index.html")):
        data_pairs.append((web_dist, os.path.join("desktop", "web", "dist")))

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name",
        "poe2-workbench-backend",
        "--distpath",
        os.path.join(BUILD_TMP_DIR, "dist"),
        "--workpath",
        os.path.join(BUILD_TMP_DIR, "work"),
        "--specpath",
        BUILD_TMP_DIR,
        "--paths",
        REPO_ROOT,
        "--collect-submodules",
        "backend",
    ]
    cmd.append("--onefile" if args.onefile else "--onedir")
    for src, dest in data_pairs:
        if os.path.exists(src):
            cmd.extend(["--add-data", _add_data_arg(src, dest)])
    cmd.append(entry)

    print("正在使用 PyInstaller 构建后端独立可执行文件...")
    subprocess.check_call(cmd, cwd=REPO_ROOT)

    built_dir = os.path.join(BUILD_TMP_DIR, "dist", "poe2-workbench-backend")
    if args.onefile:
        os.makedirs(BACKEND_OUT_DIR, exist_ok=True)
        exe_name = "poe2-workbench-backend.exe" if sys.platform == "win32" else "poe2-workbench-backend"
        shutil.copy2(os.path.join(BUILD_TMP_DIR, "dist", exe_name), os.path.join(BACKEND_OUT_DIR, exe_name))
    else:
        shutil.copytree(built_dir, BACKEND_OUT_DIR)

    print(f"✔ 后端 Sidecar 已输出至：{BACKEND_OUT_DIR}")

    if args.bundle_luajit or args.luajit_path:
        _copy_luajit(args.luajit_path or None)


if __name__ == "__main__":
    main()
