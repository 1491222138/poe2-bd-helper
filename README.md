# PoE2 BD 助手 (PoE2 Workbench)

《流放之路 2》(Path of Exile 2) 本地构筑拆解、抄作业诊断与升级决策工作台。

- **本地桌面端（`desktop/`）**：导入「我的 BD」与「目标 BD」，基于真实 PoB2 引擎进行 0 成本体检、逐件装备试穿（含抗性 / 属性 / 精魂断档红线与自动阈值补偿、双武器组评估）、词缀敏感度剥离、天赋树精简对比与预算升级路线规划，并支持一键跳转 [PoE2 HTC](https://poe2htc.com/) 做装。
- **浏览器扩展（`extension/`，Chrome / Edge MV3）**：在 `poe.ninja` / `pobb.in` 一键拆解目标 BD 的核心机制、核心装备与词缀、核心天赋与 BD 定位，借真实浏览器环境安全查价，并实时推送至本地工作台。
- **统一本地引擎（`backend/`）**：纯 Python 3 标准库（零 `pip install`）+ 弹性常驻 LuaJIT + PoB2 无头计算池（仅监听 `127.0.0.1:8788`），支持本机已有 PoB2 自动自检复用，或**一键从 GitHub 拉取精简纯计算内核**。

详细架构见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

---

## 快速上手（普通玩家推荐）

### 方式一：下载独立安装包（免 Python / 免 Node / 免 LuaJIT）

1. 前往本仓库 **GitHub Releases** 下载对应系统的安装包：
   - **macOS (Apple Silicon)**：`PoE2 Workbench_*.dmg`
   - **Windows (x64)**：`PoE2 Workbench_*_x64-setup.exe` 或 `.msi`
   - **浏览器扩展（可选）**：`poe2-workbench-extension.zip`
2. 安装并打开 **PoE2 Workbench**；首次启动会自动弹出「**PoB2 计算内核自检与一键安装**」向导：
   - 本机已安装官方 Path of Building (PoE2) → 自动识别，点击直接复用；
   - 未安装 PoB2 → 点击 **「一键从 GitHub 下载 PoB2 内核」**（支持官方直连与国内镜像加速，自动跳过贴图/字体）。
3. （可选）解压 `poe2-workbench-extension.zip`，在 Chrome / Edge 打开 `chrome://extensions` → 开启开发者模式 → 「加载已解压的扩展程序」选择解压目录。

### 方式二：从源码直接启动

> 前置要求：**Python 3.10+**、**LuaJIT**（macOS 可自动调用 Homebrew 安装）、首次编译前端时需 **Node.js 20+**（编译一次后日常运行不再依赖 Node.js）。

```bash
git clone https://github.com/<your-username>/poe2-workbench.git
cd poe2-workbench

# macOS / Linux（内置环境自检：自动检查 LuaJIT、首次自动编译前端并启动工作台）
python3 scripts/start_workbench.py
```

- **macOS 快捷方式**：直接双击仓库根目录的 `启动工作台.command`。
- **Windows 快捷方式**：直接双击仓库根目录的 `启动工作台.bat`（未装 Python 时可自动引导 `winget` 安装）。
- 启动后浏览器自动打开 `http://127.0.0.1:8788/`，点击右上角 **引擎状态胶囊** 即可自检或一键从 GitHub 下载 PoB2 计算内核。

> 如本机已有特定路径的 PoB2，也可复制 `.env.example` 为 `.env.local` 并设置 `POB2_ROOT=/path/to/PathOfBuilding-PoE2`。

---

## 开发者命令

```bash
# 1. 运行后端全部单元测试（设置 POE2_TOKEN 避免写入用户目录）
POE2_TOKEN=test-token python3 -m unittest discover -s backend/tests -v

# 2. 桌面前端检查与构建
cd desktop/web && npm install && npm run typecheck && npm test && npm run build

# 3. 浏览器扩展检查与构建（产物在 extension/dist/）
cd extension && npm install && npm run typecheck && npm test && npm run build

# 4. 热重载开发模式（后端 8788 + Vite HMR 5173）
python3 scripts/start_workbench.py --dev

# 5. 构建带内嵌后端与 LuaJIT 的 Tauri 2 独立安装包（需 Rust + PyInstaller）
python3 -m pip install pyinstaller
python3 scripts/build_backend_sidecar.py --bundle-luajit
cd desktop && npx @tauri-apps/cli@2 build --config src-tauri/tauri.release.conf.json
```

### 自动发布安装包（GitHub Actions）

推送版本标签即可自动触发 `.github/workflows/release.yml`，云端构建 macOS `.dmg`、Windows `.exe/.msi` 与浏览器扩展 `.zip` 并发布至 GitHub Releases：

```bash
git tag v1.0.0
git push origin v1.0.0
```

---

## 开源协议、致谢与免责声明

### 开源协议
- 本项目基于 [MIT License](LICENSE) 开源。

### 致谢与第三方项目声明
- **Path of Building (PoE2)**：本项目的本地数值计算内核依赖社区开源项目 [PathOfBuildingCommunity/PathOfBuilding-PoE2](https://github.com/PathOfBuildingCommunity/PathOfBuilding-PoE2)（MIT License）。内核按需由用户关联本机已有安装或从上游仓库拉取，本仓库不二次分发其游戏美术与字体资源，相关代码与版权归 PathOfBuildingCommunity 及贡献者所有。
- **PoE2 HTC**：本项目的做装路线引导与一键跳转功能参考并链接至 [PoE2 HTC](https://poe2htc.com/)。本项目仅提供思路参考与外部跳转入口，PoE2 HTC 网站及其做装工具的版权与所有权归其原作者所有。
- **poe.ninja / pobb.in**：BD 导入解析与通货参考汇率涉及 [poe.ninja](https://poe.ninja/) 与 [pobb.in](https://pobb.in/)，相关数据与服务归各自平台所有。

### 免责声明
- 本项目为非官方第三方开源社区工具，与 **Grinding Gear Games**（*Path of Exile 2* 开发商）、**PathOfBuildingCommunity**、**PoE2 HTC**、**poe.ninja**、**pobb.in** 均无任何官方隶属、合作或背书关系。
- *Path of Exile 2* 游戏相关的所有名称、机制术语与商标版权均归 Grinding Gear Games 所有。
