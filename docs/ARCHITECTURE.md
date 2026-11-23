# PoE2 Workbench 架构设计文档（v1.0）

## 1. 产品定位与核心分工

本项目由**浏览器扩展**与**本地桌面端**协同组成，底层共享**唯一本地计算引擎与业务内核**，彻底消除旧版双服务分裂（8787 / 8788）与单文件膨胀问题。

| 端 | 核心定位 | 负责做什么 | 刻意不做什么 |
|---|---|---|---|
| **浏览器扩展**<br>（Chrome / Edge MV3） | **目标 BD 侦察兵 + 市集查价员** | 1. 抓取/解码 `poe.ninja` / `pobb.in` / PoB2 码<br>2. 拆解目标 BD 的**核心思路、核心装备、核心词缀、核心天赋、BD 定位**<br>3. 借助真实浏览器环境按需查官方市集价（防 429 熔断）<br>4. 一键推送目标 BD 与价格给桌面端；响应桌面端的市集搜货跳转 | 不运行重型多变体模拟；不做复杂的多档位预算推演与做装模拟 |
| **本地桌面端**<br>（Tauri 2 + React + TS） | **自身 BD 追赶 & 造价决策台** | 1. 管理「我的 BD」+「目标 BD」+「预算档位」<br>2. 0 成本提升诊断（天赋差距 / 技能连法与品质 / 抗性精魂红线）<br>3. 装备逐件试穿与核心词缀拆解（必保 vs 可降配 vs 溢价）<br>4. 按预算决策：直接买成品 / 买平替 / 买半成品简单加工（V1）→ 未来扩展完整做装树 | 不直接爬虫请求官方交易站（规避 Cloudflare / 403 / 429） |
| **统一本地后端**<br>（Python + 常驻 LuaJIT） | **唯一算力与数据内核** | 1. 常驻 LuaJIT Worker 池（Mac / Windows / Linux 跨平台）<br>2. PoB2 完整解析（含天赋树大点/升华）与 50+ 项面板抽取<br>3. 差距诊断、词缀微扰、造价路线、做装期望计算<br>4. 拉取 `poe.ninja` 通货/精华汇率快照 | 无云端依赖，仅监听 `127.0.0.1:8788` |

---

## 2. 双电脑协作与仓库布局约定

解决两台电脑（如 Mac + Windows/WSL）协同开发与大体积引擎资产共享问题：

- **代码仓库（走 Git 私有库，不放 OneDrive）**
  - Mac 路径：`/Users/moyu/Desktop/poe2-workbench`（纯本地，不放 OneDrive）
  - Windows/WSL 路径：`~/repos/poe2-workbench`
  - 通过 GitHub / Gitee 私有仓库同步源码、文档、类型契约与测试，避免 OneDrive 同步 `.git`、`node_modules/`、`target/` 产生冲突与路径损坏。
- **只读大体积引擎资产（留在 OneDrive 或本机独立目录，不入 Git）**
  - `PathOfBuilding-PoE2`（~449MB，含 12 处 Lua 5.1 语法兼容修复）继续保留在 OneDrive（或本机固定路径）。
  - 每台电脑在仓库根目录放一个不入 Git 的 `.env.local`，仅配置一行：
    ```env
    POB2_ROOT=/Users/moyu/Library/CloudStorage/OneDrive-个人/poe2-ninja-analyzer/pob2-engine/PathOfBuilding-PoE2
    ```
  - 仓库内保留 `pob2-engine.lock`（21KB），启动/CI 时自动校验引擎资产未被意外破坏。

---

## 3. 目录结构

```text
poe2-workbench/
├── docs/
│   └── ARCHITECTURE.md          # 本架构文档
├── packages/
│   └── contracts/               # 双端共享 TS 类型 & JSON Schema
│       ├── build-profile.ts     # 目标 BD 定位摘要契约
│       ├── price-snapshot.ts    # 市集价格快照契约
│       ├── gap-report.ts        # 自身 vs 目标全景差距契约
│       └── craft-decision.ts    # 买 vs 半成品加工决策契约（预留策略扩展口）
├── backend/                     # 唯一本地后端（127.0.0.1:8788）
│   ├── engine/
│   │   ├── worker_pool.py       # 常驻弹性 LuaJIT Worker 池（跨平台 RSS 监控 + 回收）
│   │   ├── persistent_worker.lua# 常驻脚本（内置 lua-utf8 垫片 + 50+ 全量面板）
│   │   ├── pob_parser.py        # PoB2 XML 解析（装备/珠宝/技能/天赋树大点与升华）
│   │   ├── tree_index.py        # 从 PoB2 Data/Tree 导出并缓存节点名称/类型索引
│   │   ├── xml_mutator.py       # 换装 / 换天赋 / 换技能 / 脱装补偿 / 词缀微扰
│   │   └── runefix.py           # 圣物词条前缀修复
│   ├── domain/
│   │   ├── build_profile.py     # 目标 BD 定位提取（规则 + 可选引擎权重 + 可选 AI 润色）
│   │   ├── gap_analyzer.py      # 0 成本诊断 + 装备试穿 + 核心/可砍词缀拆解
│   │   ├── budget_planner.py    # 多预算档位最优升级路径（0-1 背包 + 阶段排序）
│   │   └── crafting/            # 插件化做装决策模块（V1 轻量，后续可扩展）
│   │       ├── base.py          # Strategy 抽象接口
│   │       ├── simple_finish.py # V1：已有半成品补崇高 / 单发精华定向期望估算
│   │       ├── affix_pool.py    # RePoE 全槽位词缀池 + PoB2 精华表（修复旧版槽位硬编码）
│   │       └── compare.py       # 买成品 vs 买半成品加工 vs 白底重做同屏对比
│   ├── market/
│   │   ├── ninja_rates.py       # poe.ninja 通货/精华汇率快照（本地 TTL 缓存）
│   │   └── price_store.py       # 扩展推送价格的持久化存储
│   ├── api/
│   │   ├── server.py            # HTTP + SSE 事件推送（目标 BD/价格到达实时通知桌面端）
│   │   └── auth.py              # Origin 白名单 + 本地 X-Auth-Token 鉴权
│   └── tests/                   # pytest 自动化测试
├── extension/                   # 浏览器扩展（Vite + TypeScript + React，MV3）
│   ├── manifest.json
│   └── src/
│       ├── content/             # poe.ninja 页面抓取
│       ├── market/              # 防 429 市集查价器 + tradeBuilder（支持生成平替/半成品查询）
│       ├── bridge/              # 127.0.0.1:8788 心跳检测 / 推送 / poe2wb:// 兜底唤醒
│       └── sidepanel/           # 目标 BD 定位卡片 + 查价控制 + 发送桌面端
└── desktop/                     # 桌面工作台
    ├── web/                     # Vite + TypeScript + React
    │   └── src/
    │       ├── pages/
    │       │   ├── Overview.tsx # 我的 BD vs 目标 BD 总览 + 0 成本优先清单
    │       │   ├── Upgrade.tsx  # 装备逐件试穿 + 词缀拆解 + 预算滑块路线
    │       │   └── Craft.tsx    # 买成品 vs 买半成品加工对比（V1 轻量估算台）
    │       └── components/
    └── src-tauri/               # Tauri 2 外壳（托盘 + 托管后端 + poe2wb:// Deep Link）
```

---

## 4. 双端打通协议（扩展 ↔ 桌面端）

### 4.1 通信通道
1. **主力通道**：`http://127.0.0.1:8788`（REST API）+ `/events`（SSE 实时推送）。
   - 桌面端启动时自动拉起后端并订阅 `/events`。
   - 扩展心跳探测 `/health`：在线时亮绿灯，支持一键推送；离线时自动降级。
2. **自动唤醒兜底**：桌面端未启动时，扩展可通过 `poe2wb://import?...`（Tauri Deep Link）或复制交接包拉起。
3. **安全边界**：仅绑定 `127.0.0.1`；校验 `Origin` 白名单（桌面端 + 扩展 ID）+ `~/.poe2-workbench/token` 令牌。

### 4.2 核心接口契约

| 端点 | 方向 | 用途 |
|---|---|---|
| `GET /health` | 双向 | 引擎状态、worker 数、版本校验指纹、汇率快照时间 |
| `GET /events` | 后端 → 桌面端 | SSE 推送：扩展刚推送了新目标 BD 或新一批市集价格，桌面端即时刷新 |
| `POST /builds/target` | 扩展 → 桌面端 | 推送目标 PoB2 码 + 定位摘要（设为当前目标 BD） |
| `POST /builds/profile` | 扩展/桌面 → 后端 | 完整解析目标 BD（含天赋树大点/升华 + 核心装备/词缀 + 可选三轴实算） |
| `POST /prices` / `GET /prices` | 扩展 → 后端 → 桌面 | 推送/读取市集查价结果（区分 `exact` 原件 / `budget` 平替 / `semi` 半成品底材） |
| `POST /gap/analyze` | 桌面 → 后端 | 「我的 BD vs 目标 BD」全景诊断（0 成本改动 + 逐件试穿 + 抗性/精魂红线） |
| `POST /gap/slot-affixes` | 桌面 → 后端 | 单个关键稀有槽位词缀逐条剥离，标出「必保 / 增益 / 可砍」词缀 |
| `POST /roadmap` | 桌面 → 后端 | 结合预算档位生成分阶段升级路线 |
| `POST /craft/compare` | 桌面 → 后端 | V1 做装对比：输入目标词缀 + 已有半成品词缀/底子价 + 成品参考价 → 输出买 vs 加工结论 |

---

## 5. 半成品加工 V1 设计（先轻后重，预留扩展口）

考虑到完整 PoE2 半成品做装涉及庞大工艺树与自动扫货，**V1 刻意做轻、做准，不求大而全**：

- **V1 做什么**：
  1. **只靠“用户手动输入/一键去市集看一眼”获取参考价**：桌面端根据该槽位拆出的 2~3 条核心必保词缀，自动生成两条市集搜索链接：
     - 「搜成品/平替价」（3~4 条词缀）
     - 「搜半成品底子价」（仅填最难出的 2 条核心词缀 + 留空前后缀）
  2. **计算模型聚焦 2 种高频加工方式**（均复用全槽位 RePoE 权重 + PoB2 精华表 + `poe.ninja` 实时通货价）：
     - **策略 1（半成品补崇高）**：已知半成品已有词缀集合 $M_{\text{have}}$（占用的前后缀与词缀组已锁定），剩余 $k$ 个空位直点崇高，命中目标词缀集合 $M_{\text{want}}$ 中至少一条/全部的概率与期望成本。
     - **策略 2（精华定向补位）**：在半成品/蓝底上锁一条指定精华词缀 + 补齐空位的期望成本与 P80 风险成本。
  3. **同屏给出直观结论**：`成品直买价` vs `半成品底子价 + 期望加工费 (P50 / P80)`，并明确提示“炸底子风险”。
- **为 V2/V3 预留的扩展点**：
  - `backend/domain/crafting/base.py` 定义统一的 `CraftingStrategy` 接口；后续新增 **预兆（锁前/锁后/定向剥离）**、**重组器**、**扩展自动回传半成品市场底价** 时，只需新增策略类与价格源，无需改动前端框架和 API 协议。

---

## 6. 跨平台与旧资产复用清单

- **直接复用并升级**：
  - `PathOfBuilding-PoE2`（12 个 Lua 5.1 修复）+ `pob2-engine.lock`
  - `persistent_worker.lua` + `worker_manager.py`（合入旧 `server.py` 的 `lua-utf8` 垫片与 50+ 全量面板字段，修复 macOS 下无 `/proc` 导致的内存检测失效）
  - `runefix.py`、`essences.py`、`sync_repoe.py`、`backend/data/affix_library.json`
  - 扩展侧 `priceChecker.js`、`tradeBuilder.js`、`core/intent.js`、`core/rules.js`
- **彻底重构**：
  - 合并 8787 / 8788 为单一后端
  - 拆分 4094 行 `sidepanel.js` 为模块化 TypeScript + React 扩展
  - 替换单文件 `app.html` 为组件化 React 桌面前端
  - 重写 `pob_parser`（补齐天赋树节点映射）与 `crafting_sim`（支持全槽位 + 半成品已有词缀锁组）

---

## 7. 分阶段实施里程碑

1. **Stage 1 · 统一引擎底座（Mac 实测跑通）**
   - 搭建仓库骨架 + `.env.local` 配置 + `engine/` 全模块（常驻池 + `lua-utf8` 垫片 + 50+ 面板 + 天赋树节点索引 + 换装/换天赋/换技能）+ 自动化冒烟测试。
2. **Stage 2 · 浏览器扩展重构 & 双端通道打通**
   - 搭建 `extension/`（TS + React + Vite MV3）→ 目标 BD 定位卡（核心思路/装备/词缀/天赋）+ 市集查价 + 一键推送目标 BD 与价格至 `127.0.0.1:8788`。
3. **Stage 3 · 桌面端核心工作流（自身 vs 目标全景差距）**
   - 搭建 `desktop/web`（TS + React + Vite）+ SSE 实时接收扩展推送 → 0 成本提升清单 + 逐件试穿（含抗性/精魂红线）+ 关键槽位词缀重要性拆解 + 预算档位路线。
4. **Stage 4 · 轻量化买 vs 半成品加工决策台（V1）**
   - 接入 `poe.ninja` 实时通货/精华汇率 + 实现 `simple_finish.py`（半成品补崇高/精华定向）+ 自动生成“成品 vs 半成品”市集查询链接。
5. **Stage 5 · Tauri 2 桌面打包与协议唤醒**
   - 托盘托管后端进程、`poe2wb://` Deep Link 唤醒与跨平台构建配置。
