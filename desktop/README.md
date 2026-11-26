# PoE2 Workbench 桌面端（Tauri 2 + React + TS）

## 两种运行方式

### 1. 免 Rust 一键本地工作台模式（Mac / Windows 通用）
在仓库根目录运行：
```bash
python3 scripts/start_workbench.py
```
会自动启动统一后端 (`127.0.0.1:8788`) 与桌面前端 (`http://127.0.0.1:5173`)，按 `Ctrl+C` 干净回收所有进程。

### 2. 原生 Tauri 2 桌面打包模式（托盘托管后端 + `poe2wb://` Deep Link 唤醒）

前置依赖：
- **Rust 工具链**（如尚未安装：`curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh`）
- **Tauri CLI v2**：`cargo install tauri-cli --version "^2"`

开发调试（带系统托盘 + 托管 Python 后端 + 热重载）：
```bash
cd desktop/src-tauri
cargo tauri dev
```

生产打包（macOS `.app`/`.dmg` · Windows `.msi`/`.exe` · Linux `.AppImage`/`.deb`）：
```bash
cd desktop/src-tauri
cargo tauri build
```

## Tauri 2 核心能力清单
- **系统托盘 (`TrayIconBuilder`)**：点击关闭窗口自动最小化至托盘，保持常驻 LuaJIT Worker 热态与 SSE 通道不掉线；右键托盘可随时重启后端或完全退出。
- **后端进程托管 (`backend_supervisor.rs`)**：启动时自动探测 `127.0.0.1:8788`，若未启动则自动拉起 `python3 -m backend.api.server`；退出应用时自动清理子进程，杜绝僵尸进程。
- **Deep Link 唤醒 (`poe2wb://import?...`)**：浏览器扩展在桌面端离线时可一键通过 `poe2wb://` 唤起桌面端并自动导入目标 BD（支持短码直传与长码剪贴板交接）。
