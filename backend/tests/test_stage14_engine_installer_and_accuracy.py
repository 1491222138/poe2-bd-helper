"""Stage 14 测试：PoB2 内核自检与 GitHub 精简安装器 + P1 推荐/探针准确度修复。"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from typing import Any

from backend.api.server import WorkbenchRuntime, make_handler
from backend.domain import budget_planner, gap_analyzer, stat_sensitivity
from backend.engine import engine_manager, pob_parser, xml_mutator

FIXTURE_POB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "sample.pob")


def _sample_xml() -> str:
    with open(FIXTURE_POB, encoding="utf-8") as f:
        return pob_parser.decode_pob(f.read().strip())


class TestEngineManagerAndInstaller(unittest.TestCase):
    def tearDown(self) -> None:
        engine_manager.reset_install_state_for_tests()

    def test_inspect_both_source_and_installed_layouts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            # 1. GitHub 源码布局：src/HeadlessWrapper.lua
            src_repo = os.path.join(td, "pob2-source")
            os.makedirs(os.path.join(src_repo, "src", "TreeData", "0_5"))
            os.makedirs(os.path.join(src_repo, "src", "Data"))
            os.makedirs(os.path.join(src_repo, "runtime", "lua"))
            with open(os.path.join(src_repo, "src", "HeadlessWrapper.lua"), "w", encoding="utf-8") as f:
                f.write("-- headless\n")
            with open(os.path.join(src_repo, "src", "TreeData", "0_5", "tree.json"), "w", encoding="utf-8") as f:
                f.write("{}")

            info_src = engine_manager.inspect_pob2_dir(src_repo)
            self.assertTrue(info_src["valid"])
            self.assertEqual(info_src["layout"], "source")
            self.assertEqual(info_src["treeVersions"], ["0_5"])
            self.assertTrue(info_src["hasRuntimeLua"])

            # 2. 官方 Windows/macOS 安装版布局：根目录 HeadlessWrapper.lua + lua/
            inst_repo = os.path.join(td, "pob2-installed")
            os.makedirs(os.path.join(inst_repo, "TreeData", "0_5"))
            os.makedirs(os.path.join(inst_repo, "Data"))
            os.makedirs(os.path.join(inst_repo, "lua"))
            with open(os.path.join(inst_repo, "HeadlessWrapper.lua"), "w", encoding="utf-8") as f:
                f.write("-- installed headless\n")
            with open(os.path.join(inst_repo, "TreeData", "0_5", "tree.json"), "w", encoding="utf-8") as f:
                f.write("{}")

            info_inst = engine_manager.inspect_pob2_dir(inst_repo)
            self.assertTrue(info_inst["valid"])
            self.assertEqual(info_inst["layout"], "installed")
            self.assertEqual(info_inst["srcDir"], os.path.abspath(inst_repo))
            self.assertTrue(info_inst["hasRuntimeLua"])

            # 3. 空目录应判定无效
            empty_dir = os.path.join(td, "empty")
            os.makedirs(empty_dir)
            self.assertFalse(engine_manager.inspect_pob2_dir(empty_dir)["valid"])

    def test_github_url_builder_and_selective_zip_install_with_lua51_patches(self) -> None:
        self.assertEqual(
            engine_manager.build_github_zip_url("v0.23.1"),
            "https://github.com/PathOfBuildingCommunity/PathOfBuilding-PoE2/archive/refs/tags/v0.23.1.zip",
        )
        self.assertEqual(
            engine_manager.build_github_zip_url("dev", mirror_prefix="https://gh-proxy.com"),
            "https://gh-proxy.com/https://github.com/PathOfBuildingCommunity/PathOfBuilding-PoE2/archive/refs/heads/dev.zip",
        )

        with tempfile.TemporaryDirectory() as td:
            state_dir = os.path.join(td, "state")

            def fake_downloader(_url: str, dest_zip: str, on_progress: Any) -> int:
                with zipfile.ZipFile(dest_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                    # 模拟真实 PoB2 HeadlessWrapper.lua 首行带 `#@` shebang
                    zf.writestr("PathOfBuilding-PoE2-0.23.1/src/HeadlessWrapper.lua", "#@\nlocal x = 1\n")
                    zf.writestr(
                        "PathOfBuilding-PoE2-0.23.1/src/Modules/Calc.lua",
                        "local a, b = table.unpack({1, 2})\n",
                    )
                    zf.writestr("PathOfBuilding-PoE2-0.23.1/src/TreeData/0_5/tree.json", '{"nodes":{}}')
                    zf.writestr("PathOfBuilding-PoE2-0.23.1/src/Data/Essence.lua", "return {}\n")
                    zf.writestr("PathOfBuilding-PoE2-0.23.1/runtime/lua/xml.lua", "return {}\n")
                    # 应被跳过的超大贴图与恶意路径穿越文件
                    zf.writestr("PathOfBuilding-PoE2-0.23.1/src/Assets/huge_art.png", b"\x89PNG\r\n")
                    zf.writestr("PathOfBuilding-PoE2-0.23.1/../../escape.lua", "evil\n")
                on_progress(1024, 1024)
                return 1024

            old_env = os.environ.pop("POB2_ROOT", None)
            try:
                status = engine_manager.install_from_github_zip(
                    ref="v0.23.1",
                    state_dir=state_dir,
                    downloader=fake_downloader,
                    verify_live=False,
                )
            finally:
                if old_env is not None:
                    os.environ["POB2_ROOT"] = old_env

            managed = os.path.join(state_dir, "pob2-engine")
            self.assertTrue(os.path.isfile(os.path.join(managed, "src", "HeadlessWrapper.lua")))
            self.assertTrue(os.path.isfile(os.path.join(managed, "runtime", "lua", "xml.lua")))
            self.assertFalse(os.path.exists(os.path.join(managed, "src", "Assets", "huge_art.png")))
            self.assertFalse(os.path.exists(os.path.join(td, "escape.lua")))

            # 验证 Lua 5.1 补丁已幂等写入，且首行 `#@` 仍位于第 1 行（不被顶到第 10 行导致 LuaJIT 语法错误）
            with open(os.path.join(managed, "src", "HeadlessWrapper.lua"), encoding="utf-8") as f:
                hw = f.read()
            self.assertTrue(hw.startswith("#@\n"), f"shebang 必须保持在首行，实际开头: {hw[:40]!r}")
            self.assertIn("table.unpack = unpack", hw)
            with open(os.path.join(managed, "src", "Modules", "Calc.lua"), encoding="utf-8") as f:
                calc = f.read()
            self.assertIn("(table.unpack or unpack)(", calc)

            # 二次执行应幂等（0 处重复改动）
            second = engine_manager.apply_lua51_compat_patches(os.path.join(managed, "src"))
            self.assertEqual(second["patchedCount"], 0)
            self.assertEqual(status["installState"]["status"], "done")
            self.assertEqual(status["installedRef"], "v0.23.1")

    def test_http_engine_endpoints(self) -> None:
        old_env = os.environ.pop("POB2_ROOT", None)
        try:
            with tempfile.TemporaryDirectory() as td:
                fake_pob = os.path.join(td, "my-pob2")
                os.makedirs(os.path.join(fake_pob, "src", "TreeData", "0_5"))
                with open(os.path.join(fake_pob, "src", "HeadlessWrapper.lua"), "w", encoding="utf-8") as f:
                    f.write("-- ok\n")
                with open(os.path.join(fake_pob, "src", "TreeData", "0_5", "tree.json"), "w", encoding="utf-8") as f:
                    f.write("{}")

                rt = WorkbenchRuntime(state_dir=td)
                srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(rt))
                port = srv.server_address[1]
                t = threading.Thread(target=srv.serve_forever, daemon=True)
                t.start()
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/engine/status", timeout=5) as r:
                        st = json.loads(r.read().decode("utf-8"))
                    self.assertTrue(st["ok"])
                    self.assertIn("pinnedVersion", st["engine"])

                    req = urllib.request.Request(
                        f"http://127.0.0.1:{port}/engine/use-path",
                        data=json.dumps({"path": fake_pob}).encode("utf-8"),
                        headers={"Content-Type": "application/json", "X-Auth-Token": rt.token},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=5) as r:
                        bound = json.loads(r.read().decode("utf-8"))
                    self.assertTrue(bound["ok"])
                    self.assertTrue(bound["engine"]["pob2Found"])

                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
                        health = json.loads(r.read().decode("utf-8"))
                    self.assertIn("pob2Ready", health)
                    self.assertIn("luajitFound", health)
                    self.assertIn("webUiReady", health)

                    # 验证 /engine/install-luajit 路由可正常响应（本机已有 LuaJIT 时应直接返回 alreadyPresent）
                    req_lj = urllib.request.Request(
                        f"http://127.0.0.1:{port}/engine/install-luajit",
                        data=b"{}",
                        headers={"Content-Type": "application/json", "X-Auth-Token": rt.token},
                        method="POST",
                    )
                    with urllib.request.urlopen(req_lj, timeout=10) as r:
                        lj_res = json.loads(r.read().decode("utf-8"))
                    self.assertTrue(lj_res["ok"])
                    self.assertIn("engine", lj_res)
                finally:
                    srv.shutdown()
                    srv.server_close()
                    rt.shutdown()
        finally:
            if old_env is not None:
                os.environ["POB2_ROOT"] = old_env

    def test_static_web_hosting_and_path_traversal_guard(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            dist_dir = os.path.join(td, "dist")
            assets_dir = os.path.join(dist_dir, "assets")
            os.makedirs(assets_dir)
            with open(os.path.join(dist_dir, "index.html"), "w", encoding="utf-8") as f:
                f.write("<!doctype html><title>PoE2 Workbench</title>")
            with open(os.path.join(assets_dir, "app.js"), "w", encoding="utf-8") as f:
                f.write("console.log('ok');")
            with open(os.path.join(td, "secret.txt"), "w", encoding="utf-8") as f:
                f.write("TOP_SECRET")

            old_web_dist = os.environ.get("POE2_WEB_DIST")
            os.environ["POE2_WEB_DIST"] = dist_dir
            rt = WorkbenchRuntime(state_dir=td)
            srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(rt))
            port = srv.server_address[1]
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
                    html = r.read().decode("utf-8")
                    self.assertIn("text/html", r.headers.get("Content-Type", ""))
                    self.assertIn("PoE2 Workbench", html)

                with urllib.request.urlopen(f"http://127.0.0.1:{port}/assets/app.js", timeout=5) as r:
                    js = r.read().decode("utf-8")
                    self.assertIn("javascript", r.headers.get("Content-Type", ""))
                    self.assertIn("console.log", js)

                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
                    health = json.loads(r.read().decode("utf-8"))
                    self.assertTrue(health["webUiReady"])

                # 路径穿越与不存在的 API 必须返回 404
                for bad_path in ("/%2e%2e/secret.txt", "/../secret.txt", "/api/does-not-exist"):
                    with self.assertRaises(urllib.error.HTTPError) as ctx:
                        urllib.request.urlopen(f"http://127.0.0.1:{port}{bad_path}", timeout=5)
                    self.assertEqual(ctx.exception.code, 404)
            finally:
                srv.shutdown()
                srv.server_close()
                rt.shutdown()
                if old_web_dist is None:
                    os.environ.pop("POE2_WEB_DIST", None)
                else:
                    os.environ["POE2_WEB_DIST"] = old_web_dist

    def test_find_luajit_supports_managed_bin_dir(self) -> None:
        from backend.engine import worker_pool

        with tempfile.TemporaryDirectory() as td:
            bin_dir = os.path.join(td, "bin")
            os.makedirs(bin_dir)
            exe_name = "luajit.exe" if os.name == "nt" else "luajit"
            fake_lj = os.path.join(bin_dir, exe_name)
            with open(fake_lj, "w", encoding="utf-8") as f:
                f.write("#!/bin/sh\n")

            old_env = os.environ.pop("POB2_LUAJIT", None)
            try:
                # 仅在屏蔽全局 luajit 搜索顺序前直接确认 managed_bin_dir 路径可命中
                orig_which = worker_pool.shutil.which
                worker_pool.shutil.which = lambda _name: None  # type: ignore[assignment]
                try:
                    found = worker_pool.find_luajit(state_dir=td)
                finally:
                    worker_pool.shutil.which = orig_which
                self.assertEqual(found, fake_lj)
            finally:
                if old_env is not None:
                    os.environ["POB2_LUAJIT"] = old_env


class TestP1RecommendationAndProbeAccuracy(unittest.TestCase):
    def test_dependency_lines_and_threshold_compensation_xml(self) -> None:
        raw_item = (
            "Rarity: UNIQUE\nAstramentis\nStellar Amulet\nImplicits: 1\n"
            "{enchant}{rune}15% increased Spirit\n"
            "+95 to all Attributes\n"
            "+22 to Strength and Intelligence\n"
            "10% increased Attributes\n"
            "+40 to Spirit\n"
            "+80 to maximum Life\n"
        )
        deps = xml_mutator.dependency_lines(raw_item)
        self.assertIn("15% increased Spirit", deps)
        self.assertIn("+95 to all Attributes", deps)
        self.assertIn("+22 to Strength and Intelligence", deps)
        self.assertIn("10% increased Attributes", deps)
        self.assertIn("+40 to Spirit", deps)
        self.assertNotIn("+80 to maximum Life", deps)

        xml = _sample_xml()
        comp_xml = xml_mutator.inject_threshold_compensation(xml, str_deficit=35, spirit_deficit=20)
        self.assertIn("+35 to Strength", comp_xml)
        self.assertIn("+20 to Spirit", comp_xml)

    def test_gap_threshold_compensation_off_weapon_set_and_price_basis(self) -> None:
        """验证单件试穿断属性自动补偿、副武器组独立效用评估、以及 exact > budget 价格口径对齐。"""
        tgt_xml = _sample_xml()
        tgt_parsed = pob_parser.parse_pob_xml(tgt_xml)

        # 构造我的 BD：头盔与副武器组 Weapon 1 不同于目标
        weak_helm = "Rarity: RARE\nMy Starter Helm\nAncestral Tiara\nImplicits: 0\n+45 to Strength\n+30 to maximum Life\n"
        weak_w1 = "Rarity: RARE\nMy Slow Spear\nSoaring Spear\nImplicits: 0\n10% increased Attack Speed\n"
        my_xml = xml_mutator.swap_slot_item(tgt_xml, "Helmet", weak_helm)
        my_xml = xml_mutator.swap_slot_item(my_xml, "Weapon 1", weak_w1)

        class MockDualWsPool:
            def simulate(self, xml_text: str, **_kwargs: Any) -> dict[str, Any]:
                return {
                    "dps": 100_000.0,
                    "dpsSource": "CombinedDPS",
                    "ehp": 40_000.0,
                    "moveSpeed": 20.0,
                    "activeWeaponSet": 2,
                    "hasDualWeaponSet": 1,
                    "fireRes": 75.0,
                    "coldRes": 75.0,
                    "lightningRes": 75.0,
                    "chaosRes": 25.0,
                    "spirit": 200.0,
                    "spiritUnreserved": 20.0,
                    "str": 100.0,
                    "reqStr": 90.0,
                    "dex": 180.0,
                    "reqDex": 120.0,
                    "int": 140.0,
                    "reqInt": 110.0,
                    "weaponSetStats": {
                        "ws1": {"dps": 20_000.0, "ehp": 40_000.0, "speed": 2.0},
                        "ws2": {"dps": 100_000.0, "ehp": 40_000.0, "speed": 1.6},
                    },
                }

            def simulate_batch(self, xml_list: list[str], **_kwargs: Any) -> list[dict[str, Any]]:
                out: list[dict[str, Any]] = []
                for x in xml_list:
                    base = self.simulate(x)
                    ep = {it["slot"]: it.get("name") or "" for it in pob_parser.parse_pob_xml(x).get("equippedItems") or []}
                    # 1. 试穿目标头盔（Hypnotic Star）：未补偿时断力量导致 DPS 暴跌，补偿 +25 Str 后 +25% DPS！
                    if ep.get("Helmet") == "Hypnotic Star" and ep.get("Weapon 1") == "My Slow Spear":
                        if "WorkbenchThresholdComp" in x:
                            base["dps"] = 125_000.0
                            base["ehp"] = 48_000.0
                            base["str"] = 95.0
                        else:
                            base["dps"] = 40_000.0
                            base["str"] = 65.0
                    # 2. 试穿副武器组 Weapon 1（Armageddon Edge）：主武器组 Set 2 DPS 不变，但 ws1 攻速 +35%
                    elif ep.get("Weapon 1") == "Armageddon Edge" and ep.get("Helmet") == "My Starter Helm":
                        base["weaponSetStats"]["ws1"]["speed"] = 2.7
                    out.append(base)
                return out

        price_snap = {
            "entries": [
                {"slot": "Helmet", "kind": "exact", "divineValue": 10.0},
                {"slot": "Helmet", "kind": "budget", "divineValue": 2.0},
                {"slot": "Weapon 1", "kind": "exact", "divineValue": 4.0},
            ]
        }

        report = gap_analyzer.analyze_gap(my_xml, tgt_xml, MockDualWsPool(), price_snapshot=price_snap)
        helm_step = next(s for s in report["slotUpgrades"] if s["slot"] == "Helmet")
        w1_step = next(s for s in report["slotUpgrades"] if s["slot"] == "Weapon 1")

        # 头盔：未补偿断力量，但自动补跑补偿后恢复 +25% DPS 真实潜力且保留红线警告
        self.assertAlmostEqual(helm_step["deltaDpsPct"], 25.0, places=1)
        self.assertAlmostEqual(helm_step["compensatedDeltaDpsPct"], 25.0, places=1)
        self.assertTrue(any(r["kind"] == "attribute" and r["severity"] == "fail" for r in helm_step["redlines"]))
        # 价格优先对齐 exact (10D) 而非 budget (2D)
        self.assertEqual(helm_step["priceDivine"], 10.0)
        self.assertEqual(helm_step["priceBasis"], "exact")

        # 副武器组 Weapon 1：识别 inactiveWeaponSet 并捕获 +35% 副手攻速提升，进入预算规划不被丢弃
        self.assertTrue(w1_step.get("inactiveWeaponSet"))
        self.assertAlmostEqual(w1_step["offSetSpeedDeltaPct"], 35.0, places=1)
        rm = budget_planner.plan_roadmap([], [helm_step, w1_step], budget_divine=20.0)
        chosen_slots = [s["slot"] for p in rm["phases"] for s in p["steps"]]
        self.assertIn("Helmet", chosen_slots)
        self.assertIn("Weapon 1", chosen_slots)

    def test_capped_accuracy_and_crit_use_negative_left_derivative_probes(self) -> None:
        xml = _sample_xml()
        parsed = pob_parser.parse_pob_xml(xml)
        base_sim = {
            "dps": 300_000.0,
            "ehp": 60_000.0,
            "mana": 650.0,
            "hitChance": 100.0,
            "critChance": 98.0,
            "mainSkillName": "Twister",
            "dpsSource": "CombinedDPS",
        }
        ctx = stat_sensitivity.prepare_reverse_probe_jobs(xml, parsed, base_sim, include_probes=True)
        self.assertEqual(ctx["probeDirs"].get("accuracy"), -1)
        self.assertEqual(ctx["probeDirs"].get("crit_chance"), -1)
        acc_xml = next(x for k, x in ctx["batchJobs"] if k == "probe:accuracy")
        self.assertIn("-500 to Accuracy Rating", acc_xml)

        # 模拟减去 500 命中后 DPS 跌 10%，验证左导数给出正的 dpsPctPerUnit 且不被判成 dead
        sim_results = {
            "probe:accuracy": {"dps": 270_000.0, "ehp": 60_000.0, "mana": 650.0},
            "probe:crit_chance": {"dps": 276_000.0, "ehp": 60_000.0, "mana": 650.0},
        }
        spec = stat_sensitivity.assemble_reverse_build_spec(parsed, base_sim, ctx, sim_results)
        self.assertNotIn("accuracy", spec["deadProbeIds"])
        self.assertNotIn("crit_chance", spec["deadProbeIds"])
        acc_entry = next(s for s in spec["statSensitivity"] if s["id"] == "accuracy")
        self.assertAlmostEqual(acc_entry["dpsPctPerUnit"], 0.02, places=4)


if __name__ == "__main__":
    unittest.main()
