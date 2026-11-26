"""Stage 9 测试：基底自带技能消融与功能损失评估 + 传奇装备不做复杂做装 + 实时 poe.ninja/Trade2 核价引擎。"""
from __future__ import annotations

import os
import tempfile
import unittest

from backend.domain import ablation_dossier, affix_analyzer, trade_url
from backend.engine import pob_parser, xml_mutator
from backend.market import market_pricer
from backend.market.price_store import StateStore


_SAMPLE_GRANTED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<PathOfBuilding2>
  <Build className="Mercenary" ascendClassName="Gemling Legionnaire" level="95" mainSocketGroup="1"/>
  <Items activeItemSet="1">
    <Item id="1">
Rarity: RARE
Blood Torc
Absent Amulet
Implicits: 3
-1 Prefix Modifier allowed
-1 Suffix Modifier allowed
Grants Skill: Level 18 Eternal Rage
+50 to Spirit
+4 to Level of all Projectile Skills
    </Item>
    <Item id="2">
Rarity: UNIQUE
Sacred Flame
Shrine Sceptre
Sockets: S S
Implicits: 1
Grants Skill: Level 19 Purity of Fire
Gain 73% of Damage as Extra Fire Damage
    </Item>
    <Item id="3">
Rarity: RARE
Dire Edge
Soaring Spear
Implicits: 1
Grants Skill: Spear Throw
36% increased Attack Speed
    </Item>
    <ItemSet id="1">
      <Slot name="Amulet" itemId="1"/>
      <Slot name="Weapon 2" itemId="2"/>
      <Slot name="Weapon 1" itemId="3"/>
    </ItemSet>
  </Items>
  <Skills activeSkillSet="1">
    <SkillSet id="1">
      <Skill enabled="true" slot="">
        <Gem nameSpec="Twister" skillId="TwisterPlayer" level="20" quality="20" enabled="true"/>
      </Skill>
      <Skill enabled="true" slot="">
        <Gem nameSpec="Eternal Rage" skillId="EternalRagePlayer" level="18" quality="0" enabled="true"/>
      </Skill>
      <Skill enabled="true" slot="Amulet" source="Item:1:Blood Torc, Absent Amulet">
        <Gem nameSpec="Eternal Rage" skillId="EternalRagePlayer" level="18" quality="0" enabled="true"/>
      </Skill>
      <Skill enabled="true" slot="">
        <Gem nameSpec="Purity of Fire" skillId="PurityOfFirePlayer" level="19" quality="0" enabled="true"/>
        <Gem nameSpec="Vitality II" skillId="SupportVitalityPlayerTwo" level="1" quality="0" enabled="true"/>
        <Gem nameSpec="Clarity II" skillId="SupportClarityPlayerTwo" level="1" quality="0" enabled="true"/>
      </Skill>
      <Skill enabled="true" slot="Weapon 2" source="Item:2:Sacred Flame, Shrine Sceptre">
        <Gem nameSpec="Purity of Fire" skillId="PurityOfFirePlayer" level="19" quality="0" enabled="true"/>
      </Skill>
      <Skill enabled="true" slot="Weapon 1" source="Item:3:Dire Edge, Soaring Spear">
        <Gem nameSpec="Spear Throw" skillId="SpearThrowPlayer" level="1" quality="0" enabled="true"/>
      </Skill>
    </SkillSet>
  </Skills>
</PathOfBuilding2>
"""


class TestGrantedSkillAblationAndUniqueNoCraft(unittest.TestCase):
    def test_remove_implicit_decrements_implicits_count_and_disables_granted_skills(self) -> None:
        mut = xml_mutator.ablate_item_granted_skill(
            _SAMPLE_GRANTED_XML, "1", "Amulet", "Grants Skill: Level 18 Eternal Rage"
        )
        # Implicits: 3 应准确减为 Implicits: 2，避免下一条显性词缀 +50 to Spirit 错位为基底
        self.assertIn("Implicits: 2", mut)
        parsed = pob_parser.parse_pob_xml(mut)
        active_names = [sg["gems"][0]["name"] for sg in parsed["skillGroups"]]
        self.assertNotIn("Eternal Rage", active_names)
        self.assertIn("Purity of Fire", active_names)

        # 脱掉 Sacred Flame 同步禁用无 source 的 Purity of Fire + 辅助组
        mut2 = xml_mutator.ablate_slot_with_granted_skills(
            _SAMPLE_GRANTED_XML, "Weapon 2", ["Purity of Fire"], item_id="2"
        )
        parsed2 = pob_parser.parse_pob_xml(mut2)
        active_names2 = [sg["gems"][0]["name"] for sg in parsed2["skillGroups"]]
        self.assertNotIn("Purity of Fire", active_names2)

    def test_affix_analyzer_detects_important_base_skills_and_unique_no_craft(self) -> None:
        parsed = pob_parser.parse_pob_xml(_SAMPLE_GRANTED_XML)
        by_slot = {it["slot"]: it for it in parsed["equippedItems"]}

        # 1. Absent Amulet 自带 Eternal Rage -> 必锁基底 + 重要自带技能
        amu = affix_analyzer.analyze_item_deeply(by_slot["Amulet"], parsed)
        self.assertTrue(amu["requireBaseType"])
        self.assertIn("Eternal Rage", amu["baseTypeReason"])
        gs_aff = next(a for a in amu["affixes"] if "Eternal Rage" in a["line"])
        self.assertEqual(gs_aff["role"], "synergy")
        self.assertTrue(gs_aff["grantedSkill"]["isImportant"])

        # 2. Sacred Flame (UNIQUE, 2 孔超单手 1 孔上限) -> 重要自带技能 + 挂载 2 颗辅助 + 传奇不做复杂做装
        sf = affix_analyzer.analyze_item_deeply(by_slot["Weapon 2"], parsed)
        self.assertTrue(sf["isUnique"])
        self.assertEqual(sf["budgetPlan"]["lockAffixes"], [])
        self.assertEqual(sf["semiPlan"]["lockAffixes"], [])
        self.assertIn("瓦尔", sf["socketAnalysis"]["advice"])
        self.assertIn("无需复杂做装", sf["socketAnalysis"]["advice"])
        pof_aff = next(a for a in sf["affixes"] if "Purity of Fire" in a["line"])
        self.assertTrue(pof_aff["grantedSkill"]["isImportant"])
        self.assertEqual(len(pof_aff["grantedSkill"]["linkedSupports"]), 2)

        # 3. Soaring Spear 默认普攻 Spear Throw 未连辅助 -> 不应误判为必须锁特定矛底子
        sp = affix_analyzer.analyze_item_deeply(by_slot["Weapon 1"], parsed)
        st_aff = next(a for a in sp["affixes"] if "Spear Throw" in a["line"])
        self.assertFalse(st_aff["grantedSkill"]["isImportant"])

    def test_classify_affix_purpose_marks_functional_loss_for_granted_skills(self) -> None:
        parsed = pob_parser.parse_pob_xml(_SAMPLE_GRANTED_XML)
        by_slot = {it["slot"]: it for it in parsed["equippedItems"]}
        amu = affix_analyzer.analyze_item_deeply(by_slot["Amulet"], parsed)
        gs_aff = next(a for a in amu["affixes"] if "Eternal Rage" in a["line"])

        # 即使 PoB2 面板返回 0 差值，重要基底自带技能也绝不能丢成“无显示”，必须标出 must + functionalLoss
        cls_zero = ablation_dossier._classify_affix_purpose(
            gs_aff["line"],
            dps_loss=0.0,
            ehp_loss=0.0,
            mana_loss=0.0,
            spirit_loss=0.0,
            max_res_drop=0.0,
            attr_broken=False,
            triggered_redlines=[],
            dmeta=gs_aff,
            is_speed_weapon=False,
            slot="Amulet",
            is_unique=False,
        )
        self.assertEqual(cls_zero["tier"], "must")
        self.assertEqual(cls_zero["copyAction"], "lock")
        self.assertTrue(cls_zero.get("functionalLoss"))

    def test_trade_url_coerces_unique_craft_actions_to_buy_unique(self) -> None:
        items = trade_url.enrich_copy_plan_items(
            [
                {
                    "slot": "Body Armour",
                    "action": "craft_semi",
                    "itemName": "Morior Invictus",
                    "baseType": "Grand Regalia",
                    "rarity": "UNIQUE",
                    "reason": "test",
                }
            ],
            league="Forbidden Rites",
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["action"], "buy_unique")

    def test_market_pricer_ninja_matching_and_store_merge(self) -> None:
        parsed = pob_parser.parse_pob_xml(_SAMPLE_GRANTED_XML)
        fake_index = {
            "league": "Forbidden Rites",
            "exaltPerDivine": 440.0,
            "chaosPerDivine": 14.0,
            "itemCount": 2,
            "byName": {
                "sacred flame": {
                    "name": "Sacred Flame",
                    "baseType": "Shrine Sceptre",
                    "divineValue": 1.0,
                    "exaltedValue": 440.0,
                    "listingCount": 623,
                    "type": "UniqueWeapons",
                    "formatted": "1 D",
                    "source": "poe.ninja",
                }
            },
        }
        matched = market_pricer.match_build_ninja_prices(parsed, fake_index)
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["slot"], "Weapon 2")
        self.assertEqual(matched[0]["divineValue"], 1.0)

        with tempfile.TemporaryDirectory() as tmpdir:
            store = StateStore(state_dir=tmpdir)
            snap1 = market_pricer.merge_price_entries(store, league="Forbidden Rites", new_entries=matched, source="poe.ninja")
            self.assertEqual(snap1["prices"]["Weapon 2"], 1.0)
            # 再合并一条黄装查价，不能覆盖已有的 Weapon 2 暗金价
            snap2 = market_pricer.merge_price_entries(
                store,
                league="Forbidden Rites",
                new_entries=[{"slot": "Amulet", "kind": "budget", "divineValue": 18.5, "source": "trade"}],
                source="backend-trade",
            )
            self.assertEqual(snap2["prices"]["Weapon 2"], 1.0)
            self.assertEqual(snap2["prices"]["Amulet"], 18.5)

        # 未核价暗金不再返回过期硬编码假估价（如 80D Kalandra）
        cost_unknown = ablation_dossier._resolve_item_cost(
            {"slot": "Ring 2", "name": "Kalandra's Touch", "rarity": "UNIQUE"},
            price_snapshot=None,
            price_overrides=None,
            ninja_index=None,
        )
        self.assertIsNone(cost_unknown["estimatedDivine"])
        self.assertEqual(cost_unknown["priceSource"], "unknown")

    def test_weighted_substitute_query_uses_available_skill_weight_and_count(self) -> None:
        parsed = pob_parser.parse_pob_xml(_SAMPLE_GRANTED_XML)
        amu = next(it for it in parsed["equippedItems"] if it["slot"] == "Amulet")
        plan = trade_url.build_weighted_substitute_search(amu, league="Forbidden Rites", kind="budget", parsed_build=parsed)

        self.assertEqual(plan["mode"], "weighted")
        self.assertEqual(plan["tradeQuery"]["query"]["status"]["option"], "available")
        self.assertEqual(plan["tradeQuery"]["query"]["type"], "Absent Amulet")
        self.assertIn("Eternal Rage", plan["mandatorySkills"])
        # 浏览器直达链接必须带 skill.eternal_rage 硬锁 + type: weight 加权求和
        stats = plan["tradeQuery"]["query"]["stats"]
        self.assertTrue(any(g["type"] == "and" and any(f["id"] == "skill.eternal_rage" for f in g["filters"]) for g in stats))
        self.assertTrue(any(g["type"] == "weight" and g.get("value", {}).get("min", 0) > 0 for g in stats))
        # 本地直连查价 API 查询使用免登录 200 OK 的 count 池，绝不用 weight 触发 400
        api_stats = plan["apiSearchQuery"]["query"]["stats"]
        self.assertFalse(any(g["type"] == "weight" for g in api_stats))
        self.assertTrue(any(g["type"] == "count" for g in api_stats))

    def test_score_listing_weighted_parses_poe2_dict_mods_and_hashes(self) -> None:
        # PoE2 Trade2 fetch 返回的 explicitMods / desecratedMods 为对象列表（含 description 和 hash）
        weighted_terms = [
            {
                "statId": "explicit.stat_3981240776",
                "line": "+50 to Spirit",
                "weight": 1.0,
            },
            {
                "statId": "explicit.stat_2422708892",
                "line": "+4 to Level of all Projectile Skills",
                "weight": 12.5,
            },
        ]
        listing = {
            "item": {
                "explicitMods": [
                    {
                        "description": "+43 to [Spirit|Spirit]",
                        "domain": "desecrated",
                        "hash": "stat.desecrated.stat_3981240776",
                    },
                    {
                        "description": "+3 to Level of all Projectile Skills",
                        "domain": "explicit",
                        "hash": "stat.explicit.stat_2422708892",
                    },
                ]
            }
        }
        score = market_pricer._score_listing_weighted(
            listing,
            weighted_terms,
            slot="Amulet",
            base_type="Absent Amulet",
        )
        # 43 * 1.0 + 3 * 12.5 = 80.5
        self.assertAlmostEqual(score, 80.5, places=1)


if __name__ == "__main__":
    unittest.main()
