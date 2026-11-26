#!/usr/bin/env python3
"""跨平台一键启动脚本（内置环境自检与免 Node.js 运行支持）。

默认模式：
  1. 自检 Python 3、LuaJIT 与 PoB2 计算内核状态（macOS 若缺 LuaJIT 可一键 Homebrew 安装）；
  2. 若已存在编译好的 desktop/web/dist/，仅启动 Python 统一后端 (127.0.0.1:8788) 并直接托管网页工作台，
     运行期完全不依赖 Node.js；
  3. 若尚未构建前端静态文件且本机有 npm，自动执行一次 npm install && npm run build 后启动。

开发模式（热重载）：
  python3 scripts/start_workbench.py --dev
"""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
import webbrowser

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from backend.config import desktop_web_dist_dir, host, port  # noqa: E402
from backend.engine import engine_manager  # noqa: E402
from backend.engine.worker_pool import find_luajit  # noqa: E402

WEB_DIR = os.path.join(REPO_ROOT, "desktop", "web")
EXT_DIST = os.path.join(REPO_ROOT, "extension", "dist")


def wait_http(url: str, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.3)
    return False


def ensure_luajit(auto_install: bool) -> None:
    lj = find_luajit()
    if lj:
        print(f"✔ LuaJIT 已就绪：{lj}")
        return

    print("⚠ 未检测到 LuaJIT 运行时（PoB2 精确数值模拟需要 LuaJIT）。")
    if sys.platform == "darwin":
        brew = shutil.which("brew") or (
            "/opt/homebrew/bin/brew" if os.path.isfile("/opt/homebrew/bin/brew") else ""
        )
        if brew:
            should_install = auto_install
            if not should_install and sys.stdin.isatty():
                ans = input("  是否现在自动执行 `brew install luajit`？[Y/n]: ").strip().lower()
                should_install = ans in ("", "y", "yes")
            if should_install:
                print("  正在通过 Homebrew 安装 LuaJIT ...")
                res = engine_manager.install_luajit()
                print(f"  {res.get('message', '')}")
                return
            print("  提示：也可稍后在网页右上角「引擎自检」弹窗中点击「一键安装 LuaJIT」。")
            return

    st = engine_manager.detect_engine_status()
    if st.get("luajitHint"):
        print(f"  指引：{st['luajitHint']}")
    print("  （工作台仍会先启动，您可以在页面内查看自检状态并配置。）")


def ensure_web_dist() -> bool:
    dist_dir = desktop_web_dist_dir()
    if os.path.isfile(os.path.join(dist_dir, "index.html")):
        return True

    npm_bin = shutil.which("npm")
    if not npm_bin:
        print(
            "错误：尚未找到已构建的前端静态文件 (desktop/web/dist/)，且本机未安装 npm。\n"
            "请选择以下任一方式：\n"
            "  1. 从 GitHub Releases 下载免环境安装包；\n"
            "  2. 安装 Node.js (>= 20) 后重新运行本脚本（脚本会自动构建一次）。",
            file=sys.stderr,
        )
        return False

    node_modules = os.path.join(WEB_DIR, "node_modules")
    if not os.path.isdir(node_modules):
        print("首次从源码启动：正在安装桌面前端依赖 (npm install) ...")
        rc = subprocess.call([npm_bin, "install"], cwd=WEB_DIR)
        if rc != 0:
            print("错误：npm install 失败，请检查网络或 Node.js 版本。", file=sys.stderr)
            return False

    print("首次从源码启动：正在编译桌面前端静态资源 (npm run build) ...")
    rc = subprocess.call([npm_bin, "run", "build"], cwd=WEB_DIR)
    if rc != 0:
        print("错误：前端构建失败。", file=sys.stderr)
        return False
    return os.path.isfile(os.path.join(desktop_web_dist_dir(), "index.html"))


def main() -> None:
    parser = argparse.ArgumentParser(description="PoE2 Workbench 跨平台一键启动脚本")
    parser.add_argument("--dev", action="store_true", help="开发模式：同时启动 Vite HMR 前端 (5173)")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    parser.add_argument("--auto-install", action="store_true", help="缺少 LuaJIT 时自动尝试系统包管理器安装")
    args = parser.parse_args()

    print("=== PoE2 Workbench 环境自检 ===")
    print(f"✔ Python {sys.version.split()[0]}")
    ensure_luajit(auto_install=args.auto_install)

    eng = engine_manager.detect_engine_status()
    if eng.get("pob2Found"):
        print(f"✔ PoB2 计算内核已就绪：{eng.get('activeRoot')} ({eng.get('layout')})")
    else:
        print("ℹ 尚未检测到本机 PoB2 内核：启动后页面将自动弹出「一键从 GitHub 下载内核」向导。")

    api_host = host()
    api_port = port()
    api_base = f"http://{api_host}:{api_port}"

    procs: list[subprocess.Popen] = []

    def shutdown(*_args: object) -> None:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)

    if args.dev:
        npm_bin = shutil.which("npm")
        if not npm_bin:
            print("错误：--dev 开发模式需要 npm，请先安装 Node.js (>= 20)。", file=sys.stderr)
            sys.exit(1)
        if not os.path.isdir(os.path.join(WEB_DIR, "node_modules")):
            print("正在安装桌面前端依赖 (npm install) ...")
            subprocess.check_call([npm_bin, "install"], cwd=WEB_DIR)

        print(f"1/2 启动本地统一引擎后端 {api_base} ...")
        procs.append(subprocess.Popen([sys.executable, "-m", "backend.api.server"], cwd=REPO_ROOT))
        if not wait_http(f"{api_base}/health", timeout=15.0):
            print("警告：后端 15s 内未返回 /health，请检查终端日志。")

        print("2/2 启动桌面前端热重载 http://127.0.0.1:5173 ...")
        procs.append(subprocess.Popen([npm_bin, "run", "dev"], cwd=WEB_DIR))
        if wait_http("http://127.0.0.1:5173", timeout=20.0):
            print("✔ PoE2 Workbench (Dev) 已就绪：http://127.0.0.1:5173 （按 Ctrl+C 全部关闭）")
            if not args.no_browser:
                webbrowser.open("http://127.0.0.1:5173")
    else:
        if not ensure_web_dist():
            sys.exit(1)
        print(f"启动统一引擎与工作台服务 {api_base} ...")
        procs.append(subprocess.Popen([sys.executable, "-m", "backend.api.server"], cwd=REPO_ROOT))
        if wait_http(f"{api_base}/health", timeout=15.0):
            print(f"✔ PoE2 Workbench 已就绪：{api_base} （按 Ctrl+C 关闭）")
            if os.path.isdir(EXT_DIST):
                print(f"ℹ Chrome/Edge 扩展加载目录：{EXT_DIST}")
            if not args.no_browser:
                webbrowser.open(api_base)
        else:
            print("警告：后端 15s 内未返回 /health，请检查终端日志。")

    while True:
        if any(p.poll() is not None for p in procs):
            shutdown()
        time.sleep(0.5)


if __name__ == "__main__":
    main()
