"""Stage 3 后端测试：自身 vs 目标全景差距 + 单词缀剥离实算 + 0-1 背包预算路线 + HTTP 端点。"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from backend.api.server import WorkbenchRuntime, make_handler
from backend.domain import budget_planner, gap_analyzer
from backend.engine import pob_parser, xml_mutator

FIXTURE_POB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "sample.pob")


def _sample_xml() -> str:
    with open(FIXTURE_POB, encoding="utf-8") as f:
        return pob_parser.decode_pob(f.read().strip())


class TestBudgetPlannerAndRedlines(unittest.TestCase):
    def test_knapsack_beats_greedy_and_orders_phases(self) -> None:
        zero_steps = [
            {"slot": "Passive Tree", "kind": "tree", "label": "换天赋", "deltaDpsPct": 8.0, "deltaEhpPct": 5.0, "deltaMoveSpeedPct": 0.0, "redlines": [], "priceDivine": 0.0},
        ]
        # 预算 10D：
        # Item A: 6D -> +15% DPS (ROI 2.5)
        # Item B: 5D -> +12% DPS (ROI 2.4)
        # Item C: 5D -> +12% DPS (ROI 2.4)
        # 贪心会先选 A(6D) 导致剩 4D 买不了 B/C（总 +15%）；0-1 背包应选 B+C(10D, 总 +24%)！
        slot_steps = [
            {"slot": "Weapon 1", "kind": "slot_swap", "label": "A", "deltaDpsPct": 15.0, "deltaEhpPct": 0.0, "deltaMoveSpeedPct": 0.0, "redlines": [], "priceDivine": 6.0},
            {"slot": "Amulet", "kind": "slot_swap", "label": "B", "deltaDpsPct": 12.0, "deltaEhpPct": 0.0, "deltaMoveSpeedPct": 0.0, "redlines": [], "priceDivine": 5.0},
            {"slot": "Ring 1", "kind": "slot_swap", "label": "C", "deltaDpsPct": 12.0, "deltaEhpPct": 0.0, "deltaMoveSpeedPct": 0.0, "redlines": [], "priceDivine": 5.0},
            {"slot": "Helmet", "kind": "slot_swap", "label": "Unpriced", "deltaDpsPct": 7.0, "deltaEhpPct": 4.0, "deltaMoveSpeedPct": 0.0, "redlines": [], "priceDivine": None},
        ]
        rm = budget_planner.plan_roadmap(zero_steps, slot_steps, budget_divine=10.0)
        self.assertEqual(rm["spentDivine"], 10.0)
        self.assertEqual(rm["remainingDivine"], 0.0)
        # Phase 0 (8%) + B(12%) + C(12%) = 32%
        self.assertAlmostEqual(rm["expectedDeltaDpsPct"], 32.0, places=1)
        chosen_slots = [s["slot"] for p in rm["phases"] for s in p["steps"]]
        self.assertIn("Passive Tree", chosen_slots)
        self.assertIn("Amulet", chosen_slots)
        self.assertIn("Ring 1", chosen_slots)
        self.assertNotIn("Weapon 1", chosen_slots)
        self.assertEqual([d["slot"] for d in rm["deferred"]], ["Weapon 1"])
        self.assertEqual([u["slot"] for u in rm["unpriced"]], ["Helmet"])

    def test_evaluate_redlines_catches_resist_spirit_and_attr(self) -> None:
        alerts = gap_analyzer.evaluate_redlines({
            "fireRes": 52,
            "coldRes": 75,
            "lightningRes": 70,
            "chaosRes": -15,
            "chaosInoculation": 0,
            "spiritUnreserved": -10,
            "str": 80,
            "reqStr": 110,
            "dex": 150,
            "reqDex": 120,
            "int": 90,
            "reqInt": 90,
        })
        kinds = {a["kind"] for a in alerts}
        self.assertIn("resist", kinds)
        self.assertIn("spirit", kinds)
        self.assertIn("attribute", kinds)
        self.assertTrue(any(a["severity"] == "fail" and "精魂" in a["message"] for a in alerts))
        self.assertTrue(any(a["severity"] == "fail" and "力量" in a["message"] for a in alerts))


class TestStage3LiveGapApi(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.rt = WorkbenchRuntime(state_dir=self._tmp.name)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.rt))
        self.port = self.srv.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self._thread.start()

    def tearDown(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()
        self.rt.shutdown()
        self._tmp.cleanup()

    def _post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(
            f"{self.base}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Auth-Token": self.rt.token},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))

    def test_gap_analyze_slot_affixes_and_roadmap_live(self) -> None:
        tgt_xml = _sample_xml()
        tgt_parsed = pob_parser.parse_pob_xml(tgt_xml)

        # 选目标的 Body Armour 与 Ring 1（核心稀有装备）在「我的 BD」中替换为弱化白板黄装，验证真实 ΔDPS / ΔEHP 与逐词缀剥离
        body_item = next(it for it in tgt_parsed["equippedItems"] if it["slot"] == "Body Armour")
        ring_item = next(it for it in tgt_parsed["equippedItems"] if it["slot"] == "Ring 1")
        weak_body = f"Rarity: RARE\nStarter {body_item['baseType']}\n{body_item['baseType']}\nImplicits: 0\n+10 to maximum Life\n"
        weak_ring = f"Rarity: RARE\nStarter {ring_item['baseType']}\n{ring_item['baseType']}\nImplicits: 0\n+5 to maximum Life\n"
        my_xml = xml_mutator.swap_slot_item(tgt_xml, "Body Armour", weak_body)
        my_xml = xml_mutator.swap_slot_item(my_xml, "Ring 1", weak_ring)
        rare_item = body_item

        # 1. 保存我的 BD & 目标 BD
        mine_res = self._post("/builds/mine", {"pobCode": my_xml})
        self.assertTrue(mine_res["ok"])
        tgt_res = self._post("/builds/target", {"pobCode": tgt_xml})
        self.assertTrue(tgt_res["ok"])

        # 2. 全景差距诊断（不传代码，直接复用服务端存储的双码）
        gap_res = self._post("/gap/analyze", {"priceOverrides": {"Body Armour": 8.0, "Ring 1": 5.0}})
        self.assertTrue(gap_res["ok"])
        report = gap_res["report"]
        self.assertGreater(report["myStats"]["dps"], 1000)
        self.assertGreater(report["targetStats"]["dps"], 1000)
        self.assertEqual(len(report["zeroCostSteps"]), 2)

        swapped_slot = next(s for s in report["slotUpgrades"] if s["slot"] == "Body Armour")
        self.assertFalse(swapped_slot["isIdentical"])
        self.assertEqual(swapped_slot["priceDivine"], 8.0)
        self.assertGreater(swapped_slot["deltaEhpPct"], 5.0)

        # 3. 关键槽位词缀逐条剥离实算
        affix_res = self._post("/gap/slot-affixes", {"slot": "Body Armour"})
        self.assertTrue(affix_res["ok"])
        aff = affix_res["report"]
        self.assertEqual(aff["slot"], "Body Armour")
        self.assertGreater(len(aff["affixes"]), 0)
        self.assertGreater(len(aff["mustAffixes"]), 0)
        for row in aff["affixes"]:
            self.assertIn(row["tier"], ("must", "nice", "cut"))

        # 4. 预算路线规划
        rm_res = self._post("/roadmap", {
            "budgetDivine": 15.0,
            "zeroCostSteps": report["zeroCostSteps"],
            "slotUpgrades": report["slotUpgrades"],
        })
        self.assertTrue(rm_res["ok"])
        self.assertEqual(rm_res["roadmap"]["budgetDivine"], 15.0)
        self.assertGreater(rm_res["roadmap"]["spentDivine"], 0.0)
        print(
            f"\n[Stage 3 实测] slot={rare_item['slot']} | 试穿 ΔDPS={swapped_slot['deltaDpsPct']:+.1f}% "
            f"ΔEHP={swapped_slot['deltaEhpPct']:+.1f}% | 词缀剥离 {len(aff['affixes'])} 条 "
            f"(必保 {len(aff['mustAffixes'])} / 可砍 {len(aff['cuttableAffixes'])}) | 耗时 {report['elapsedMs']:.0f}ms"
        )


if __name__ == "__main__":
    unittest.main()
