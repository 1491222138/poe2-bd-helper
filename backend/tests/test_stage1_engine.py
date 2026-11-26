"""Stage 1 单元测试 + 实机常驻 LuaJIT 引擎冒烟测试（零外部依赖，stdlib unittest 可直接运行）。"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from backend.api import auth
from backend.api.server import WorkbenchRuntime, make_handler
from backend.engine import check_engine, pob_parser, runefix, tree_index, xml_mutator
from backend.engine.worker_pool import WorkerManager, WorkerPool, find_luajit

FIXTURE_POB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "sample.pob")


def _sample_code() -> str:
    with open(FIXTURE_POB, encoding="utf-8") as f:
        return f.read().strip()


class TestParserAndTreeIndex(unittest.TestCase):
    def test_parse_sample_with_passive_tree(self) -> None:
        parsed = pob_parser.parse_pob_code(_sample_code())
        self.assertGreater(len(parsed["equippedItems"]), 5)
        self.assertGreater(len(parsed["skillGroups"]), 0)

        tree = parsed["passiveTree"]
        self.assertEqual(tree["treeVersion"], "0_5")
        self.assertGreater(tree["totalAllocated"], 80)
        # 必须能解析出真实可读的升华与大点名称，而不仅是 nodeId
        self.assertGreater(len(tree["notables"]), 5)
        self.assertTrue(all(n["name"] for n in tree["notables"]))
        # PoE2 双武器组天赋点也应被区分标注
        self.assertGreater(len(tree["weaponSet1Notables"]) + len(tree["weaponSet2Notables"]), 0)


class TestXmlMutatorAndRunefix(unittest.TestCase):
    def test_ablate_and_compensate(self) -> None:
        xml = pob_parser.decode_pob(_sample_code())
        parsed = pob_parser.parse_pob_xml(xml)
        first_slot = parsed["equippedItems"][0]["slot"]
        ablated = xml_mutator.ablate_slot(xml, first_slot)
        after = pob_parser.parse_pob_xml(ablated)
        self.assertNotIn(first_slot, {i["slot"] for i in after["equippedItems"]})

    def test_swap_slot_tree_and_skills(self) -> None:
        xml = pob_parser.decode_pob(_sample_code())
        dummy_item = "Rarity: RARE\nTest Spear\nWinged Spear\nImplicits: 0\n+50 to maximum Life\n20% increased Attack Speed"
        swapped = xml_mutator.swap_slot_item(xml, "Helmet", dummy_item)
        parsed = pob_parser.parse_pob_xml(swapped)
        helm = next(i for i in parsed["equippedItems"] if i["slot"] == "Helmet")
        self.assertEqual(helm["name"], "Test Spear")

        tree_swapped = xml_mutator.swap_tree(xml, xml)
        self.assertIn("<Spec", tree_swapped)

        skills_swapped = xml_mutator.swap_skills(xml, xml)
        self.assertIn("<Skills", skills_swapped)

    def test_runefix_strips_non_bonded_only(self) -> None:
        sample = "{enchant}{rune}+12% to Fire Resistance\n{enchant}{rune}Bonded: +20% Crit\n+50 to maximum Life"
        out, fixed = runefix.normalize_runes(sample)
        self.assertEqual(fixed, 1)
        self.assertIn("+12% to Fire Resistance", out)
        self.assertIn("{enchant}{rune}Bonded:", out)

    def test_runefix_skips_native_rune_items_and_anoints(self) -> None:
        xml = (
            "<Items>"
            '<Item id="1">\n'
            "Rarity: RARE\nWrath Song\nDueling Wand\n"
            "Rune: Legacy of Lifesprig\n"
            "Implicits: 2\n"
            "{enchant}{rune}+3 to Level of all Spell Skills\n"
            "{enchant}Allocates Dominion\n"
            "</Item>"
            '<Item id="2">\n'
            "Rarity: RARE\nLegacy Helm\n"
            "Implicits: 1\n"
            "{enchant}{rune}+25% to Cold Resistance\n"
            "</Item>"
            '<Item id="3">\n'
            "Rarity: RARE\nFulgent Curio\nSapphire\n"
            "Implicits: 1\n"
            "{enchant}Corrupted Blood cannot be inflicted on you\n"
            "</Item>"
            "</Items>"
        )
        out, fixed = runefix.normalize_runes(xml)
        self.assertEqual(fixed, 1)
        # 含原生 Rune: 的装备不应被剥离前缀，避免 PoB2 0.5 双重计算
        self.assertIn("{enchant}{rune}+3 to Level of all Spell Skills", out)
        self.assertIn("{enchant}Allocates Dominion", out)
        # 纯 {enchant}（如腐化珠宝附魔）也不应被剥离
        self.assertIn("{enchant}Corrupted Blood cannot be inflicted on you", out)
        # 旧格式无 Rune: 行的装备仍按兜底剥离
        self.assertIn("\n+25% to Cold Resistance\n", out)


class TestAuthAndEngineLock(unittest.TestCase):
    def test_origin_and_token(self) -> None:
        origins = auth.allowed_origins("chrome-extension://*")
        self.assertTrue(auth.origin_allowed("http://127.0.0.1:8788", origins))
        self.assertTrue(auth.origin_allowed("chrome-extension://abcdefg", origins))
        self.assertFalse(auth.origin_allowed("https://evil.example.com", origins))
        self.assertTrue(auth.token_ok("secret-1", "secret-1"))
        self.assertFalse(auth.token_ok("wrong", "secret-1"))

    def test_engine_lock_clean(self) -> None:
        from backend.config import managed_pob2_dir, pob2_root

        if os.path.abspath(pob2_root()) == os.path.abspath(managed_pob2_dir()):
            self.skipTest("托管拉取的 PoB2 内核包含自动 Lua 5.1 兼容补丁，跳过锁文件逐字节比对")
        ok, info = check_engine.verify()
        self.assertTrue(ok, f"引擎锁校验失败：{info}")


class TestLiveLuaJITEngine(unittest.TestCase):
    """实机在当前系统（macOS / Linux / Windows）拉起常驻 LuaJIT Worker 跑真实 PoB2 计算。"""

    def test_persistent_worker_full_stats_and_ablation(self) -> None:
        self.assertIsNotNone(find_luajit(), "未找到 luajit")
        xml = pob_parser.decode_pob(_sample_code())
        xml, _ = runefix.normalize_runes(xml)

        with WorkerManager() as w:
            self.assertTrue(w.ping())
            self.assertGreater(w.rss_mb(), 10.0)  # 验证 macOS/Linux 跨平台 RSS 监控生效

            t0 = time.time()
            base = w.simulate(xml, include_audit=True)
            hot_t0 = time.time()
            # 脱掉主武器（Weapon 1 或 Weapon 1 Swap）测真实 DPS 下降
            parsed = pob_parser.parse_pob_xml(xml)
            weapon_slot = next(i["slot"] for i in parsed["equippedItems"] if "weapon" in i["slot"].lower())
            ablated_xml = xml_mutator.ablate_slot(xml, weapon_slot)
            ablated = w.simulate(ablated_xml, lock_dps_key=base["dpsSource"])
            hot_ms = (time.time() - hot_t0) * 1000.0

            self.assertGreater(base["dps"], 1000.0)
            self.assertGreater(base["ehp"], 1000.0)
            self.assertGreater(base["life"] + base["es"], 500.0)
            self.assertIn("fireRes", base)
            self.assertIn("spiritUnreserved", base)
            self.assertIn("reqStr", base)
            self.assertLess(hot_ms, 5000.0)
            print(
                f"\n[Mac 实测] boot={w.boot_ms:.0f}ms | RSS={w.rss_mb():.1f}MB | "
                f"DPS={base['dps']:,.0f} ({base['dpsSource']}) | EHP={base['ehp']:,.0f} | "
                f"Life={base['life']:.0f} ES={base['es']:.0f} | "
                f"脱掉 {weapon_slot} 后 DPS={ablated['dps']:,.0f} (热模拟耗时 {hot_ms:.0f}ms)"
            )

    def test_decode_pob_rejects_invalid_inputs_cleanly(self) -> None:
        # 5 字符占位符（如 "dummy"）应抛出友好的中文 ValueError，而非裸 binascii.Error
        with self.assertRaises(ValueError) as ctx_dummy:
            pob_parser.decode_pob("dummy")
        self.assertIn("PoB2 分享码", str(ctx_dummy.exception))

        # 网页链接应明确提示先复制分享码
        with self.assertRaises(ValueError) as ctx_url:
            pob_parser.decode_pob("https://pobb.in/abcd1234")
        self.assertIn("网页链接", str(ctx_url.exception))

        # 带 UTF-8 BOM 的有效分享码应正常解码
        xml = pob_parser.decode_pob("\ufeff" + _sample_code())
        self.assertIn("<PathOfBuilding", xml)

    def test_http_api_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rt = WorkbenchRuntime(state_dir=tmp)
            srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(rt))
            port = srv.server_address[1]
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
                    health = json.loads(r.read().decode())
                    self.assertTrue(health["ok"])
                    self.assertIn("0_5", health["treeVersions"])

                # 非法占位码（如 "dummy"）应被 400 拒绝，不会污染 target_build.json
                bad_req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/builds/target",
                    data=json.dumps({"pobCode": "dummy", "title": "Bad BD"}).encode(),
                    headers={"Content-Type": "application/json", "X-Auth-Token": rt.token},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as bad_ctx:
                    urllib.request.urlopen(bad_req, timeout=5)
                self.assertEqual(bad_ctx.exception.code, 400)
                bad_body = json.loads(bad_ctx.exception.read().decode())
                self.assertIn("PoB2 分享码", bad_body.get("error", ""))

                # 推送有效目标 BD（带令牌），隔离写入临时目录不污染 ~/.poe2-workbench
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/builds/target",
                    data=json.dumps({"pobCode": _sample_code(), "title": "Test BD"}).encode(),
                    headers={"Content-Type": "application/json", "X-Auth-Token": rt.token},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as r:
                    resp = json.loads(r.read().decode())
                    self.assertTrue(resp["ok"])
                    self.assertEqual(resp["target"]["title"], "Test BD")
            finally:
                srv.shutdown()
                srv.server_close()
                rt.shutdown()


if __name__ == "__main__":
    unittest.main()
