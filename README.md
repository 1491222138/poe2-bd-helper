# PoE2 Workbench

Path of Exile 2 构筑拆解与升级决策工作台：

- **浏览器扩展（`extension/`）**：浏览 `poe.ninja` / `pobb.in` 时快速拆解目标 BD 的**核心思路、核心词缀、核心装备、核心天赋与 BD 定位**，并借浏览器环境按需查市集价、一键推送到桌面端。
- **本地桌面端（`desktop/`）**：以目标 BD 为标杆，详细分析**自身 BD** 的 0 成本提升、逐件装备差距与核心词缀拆解，并按预算给出「买成品 / 买平替 / 买半成品加工」的决策建议。
- **统一本地引擎（`backend/`）**：跨平台常驻 LuaJIT + PoB2 计算底座（`127.0.0.1:8788`），提供 50+ 项完整面板与天赋树节点解析。

详细架构见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 双电脑协作约定

1. **代码走 Git 私有库**（本目录放在本地非 OneDrive 路径）。
2. **449MB PoB2 引擎资产留在 OneDrive 或本机固定目录**，每台电脑复制 `.env.example` 为 `.env.local`，填写 `POB2_ROOT` 指向本机路径即可。

## 快速验证（Stage 1）

```bash
# 1. 校验 PoB2 引擎资产完整性
python3 backend/engine/check_engine.py

# 2. 运行全部单元测试与 Mac/Win/Linux LuaJIT 实机冒烟测试
python3 -m unittest discover -s backend/tests -v

# 3. 启动本地统一服务（127.0.0.1:8788）
python3 -m backend.api.server
```
