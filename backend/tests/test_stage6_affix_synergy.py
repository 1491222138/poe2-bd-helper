"""Stage 6 测试：深度装备词条分析（天赋联动 + 符文插槽上限/词池/增幅 + 平替与半成品策略 + PoB2 符文消融）。"""
from __future__ import annotations

import os
import unittest

from backend.domain import affix_analyzer, gap_analyzer
from backend.engine import pob_parser, xml_mutator
from backend.engine.worker_pool import WorkerPool

FIXTURE_POB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "sample.pob")


def _sample_xml() -> str:
    with open(FIXTURE_POB, encoding="utf-8") as f:
        return pob_parser.decode_pob(f.read().strip())


class TestDeepAffixAndSocketAnalysis(unittest.TestCase):
    def test_sample_build_speed_weapon_ward_gloves_and_defensive_passives(self) -> None:
        parsed = pob_parser.parse_pob_xml(_sample_xml())
        by_slot = {it["slot"]: it for it in parsed["equippedItems"]}

        # 1. Weapon 1: 单手 2 孔超工匠石上限 + 镶嵌物效果增幅 + 攻速工具组 + Thrud's Might Destruction 词池
        w1 = affix_analyzer.analyze_item_deeply(by_slot["Weapon 1"], parsed)
        self.assertTrue(w1["isSpeedWeapon"])
        self.assertTrue(w1["socketAnalysis"]["exceedsCraftableSockets"])
        self.assertTrue(w1["socketAnalysis"]["hasSocketEffectMod"])
        self.assertEqual(w1["budgetPlan"]["requireSockets"], 2)
        self.assertEqual(w1["semiPlan"]["requireSockets"], 2)
        self.assertIn("Destruction", w1["socketAnalysis"]["runeUnlockedPools"])
        # 低档点伤不应出现在平替/半成品锁词中
        self.assertFalse(any("Adds 10 to 22" in m for m in w1["budgetPlan"]["lockAffixes"]))
        self.assertFalse(any("Adds 10 to 22" in m for m in w1["semiPlan"]["lockAffixes"]))

        # 2. Helmet: Subterfuge Mask -> 锁定总 ES 下限，且 ES 充能速率不误判为本地 ES 前缀
        helm = affix_analyzer.analyze_item_deeply(by_slot["Helmet"], parsed)
        self.assertTrue(any("Subterfuge Mask" in s for s in helm["passiveSynergies"]))
        self.assertIsNotNone(helm["defenseFloor"])
        self.assertEqual(helm["defenseFloor"]["stat"], "es")
        recharge = next(a for a in helm["affixes"] if "Recharge Rate" in a["line"])
        self.assertEqual(recharge["side"], "suffix")

        # 3. Body Armour: Spectral Ward + Beastial Skin -> 总闪避下限 + 偏转核心
        body = affix_analyzer.analyze_item_deeply(by_slot["Body Armour"], parsed)
        self.assertEqual(body["defenseFloor"]["stat"], "ev")
        self.assertFalse(body["socketAnalysis"]["exceedsCraftableSockets"])  # 胸甲 2 孔在工匠石上限内
        self.assertIsNone(body["semiPlan"]["requireSockets"])
        self.assertTrue(any("Deflection" in m for m in body["semiPlan"]["lockAffixes"]))

        # 4. Gloves: Ward 底子必保 + 投射物加速联动 + Kolr's Hunt Marksman 词池
        gloves = affix_analyzer.analyze_item_deeply(by_slot["Gloves"], parsed)
        self.assertTrue(gloves["requireBaseType"])
        self.assertIn("Marksman", gloves["socketAnalysis"]["runeUnlockedPools"])
        self.assertTrue(any("Projectile Speed" in m for m in gloves["budgetPlan"]["lockAffixes"]))

        # 5. Ring 1: Unset Ring 基底必保
        ring1 = affix_analyzer.analyze_item_deeply(by_slot["Ring 1"], parsed)
        self.assertTrue(ring1["requireBaseType"])

    def test_ci_invoker_and_bonded_rune_synergies(self) -> None:
        mock_build = {
            "character": {"className": "Monk", "ascendancy": "Invoker", "level": 95, "stats": {}},
            "passiveTree": {
                "keystones": [{"name": "Chaos Inoculation", "stats": ["Maximum Life becomes 1", "Immune to Chaos Damage"]}],
                "ascendancyNodes": [
                    {
                        "name": "Lead me through Grace...",
                        "stats": [
                            "+1 to Spirit for every 8 Item Energy Shield on Equipped Body Armour",
                            "Cannot gain Spirit from Equipment",
                        ],
                    },
                    {"name": "Wisdom of the Maji", "stats": ["Gain the benefits of Bonded modifiers on Runes and Idols"]},
                ],
                "notables": [],
                "anointedNotables": [],
            },
            "equippedItems": [],
            "jewels": [],
            "skillGroups": [],
        }
        chest = {
            "slot": "Body Armour",
            "rarity": "RARE",
            "name": "Astral Shroud",
            "baseType": "Vile Robe",
            "socketCount": 3,
            "corrupted": False,
            "properties": {"Energy Shield": "900"},
            "socketedRunes": ["Perfect Iron Rune", "Bonded: +40 to maximum Mana"],
            "mods": ["+120 to maximum Life", "+45 to Spirit", "+30% to Chaos Resistance", "110% increased Energy Shield"],
        }
        rep = affix_analyzer.analyze_item_deeply(chest, mock_build)
        roles = {a["line"]: a["role"] for a in rep["affixes"]}
        self.assertEqual(roles["+120 to maximum Life"], "dead")
        self.assertEqual(roles["+45 to Spirit"], "dead")
        self.assertEqual(roles["+30% to Chaos Resistance"], "dead")
        self.assertEqual(roles["110% increased Energy Shield"], "synergy")
        # 胸甲 3 孔 > 工匠石上限 2 孔 -> 必须锁 3 孔
        self.assertTrue(rep["socketAnalysis"]["exceedsCraftableSockets"])
        self.assertEqual(rep["semiPlan"]["requireSockets"], 3)
        self.assertTrue(rep["socketAnalysis"]["bondedActive"])

    def test_live_slot_affixes_includes_rune_ablation_and_plans(self) -> None:
        tgt_xml = _sample_xml()
        tgt_parsed = pob_parser.parse_pob_xml(tgt_xml)
        body_item = next(it for it in tgt_parsed["equippedItems"] if it["slot"] == "Body Armour")
        weak_body = f"Rarity: RARE\nStarter {body_item['baseType']}\n{body_item['baseType']}\nImplicits: 0\n+10 to maximum Life\n"
        my_xml = xml_mutator.swap_slot_item(tgt_xml, "Body Armour", weak_body)

        pool = WorkerPool(size=2, min_size=1)
        pool.start()
        try:
            rep = gap_analyzer.analyze_slot_affixes(my_xml, tgt_xml, "Body Armour", pool)
            self.assertIn("socketAnalysis", rep)
            self.assertIn("budgetPlan", rep)
            self.assertIn("semiPlan", rep)
            self.assertTrue(any("Spectral Ward" in s for s in rep["passiveSynergies"]))
            # Body Armour 插了 2x Perfect Iron Rune (+40% 防御)，拔掉符文应能测出正向 EHP 贡献
            self.assertGreater(rep["socketAnalysis"].get("runeDeltaEhpPct", 0.0), 0.0)
            self.assertTrue(any(a.get("synergyTags") for a in rep["affixes"]))
        finally:
            pool.close()


if __name__ == "__main__":
    unittest.main()
