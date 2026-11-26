"""Stage 5 后端测试：珠宝试穿/剥离全覆盖 + 大模型 (LLM) 配置与摘要接口。"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from backend.api.server import WorkbenchRuntime, make_handler
from backend.domain import build_profile, gap_analyzer, llm_advisor
from backend.engine import pob_parser, xml_mutator

FIXTURE_POB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "sample.pob")


def _sample_code() -> str:
    with open(FIXTURE_POB, encoding="utf-8") as f:
        return f.read().strip()


class TestJewelsInGapAndMutator(unittest.TestCase):
    def test_swap_tree_jewel_and_gap_includes_jewels(self) -> None:
        xml = pob_parser.decode_pob(_sample_code())
        parsed = pob_parser.parse_pob_xml(xml)
        jewels = parsed.get("jewels") or []
        self.assertGreaterEqual(len(jewels), 3)

        first_tree_jewel = next(j for j in jewels if j.get("nodeId"))
        new_jewel_text = (
            "Rarity: RARE\nTest Jewel\nSapphire\nItem Level: 80\nImplicits: 0\n"
            "20% increased Spell Damage\n+15% to Fire Resistance\n"
        )
        swapped_xml, ok = xml_mutator.swap_tree_jewel(xml, str(first_tree_jewel["nodeId"]), new_jewel_text)
        self.assertTrue(ok)
        swapped_parsed = pob_parser.parse_pob_xml(swapped_xml)
        hit = next((j for j in swapped_parsed["jewels"] if j.get("nodeId") == first_tree_jewel["nodeId"]), None)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["name"], "Test Jewel")

    def test_live_gap_analyzer_includes_jewel_upgrades(self) -> None:
        my_xml = pob_parser.decode_pob(_sample_code())
        tgt_xml = pob_parser.decode_pob(_sample_code())
        tgt_parsed = pob_parser.parse_pob_xml(tgt_xml)
        j0 = next(j for j in tgt_parsed["jewels"] if j.get("nodeId"))

        # 把目标 BD 的第 1 颗树上珠宝换成高增益珠宝，验证 gap_analyzer 能把珠宝纳入 slotUpgrades
        better_jewel = (
            "Rarity: RARE\nMirror Jewel\nSapphire\nItem Level: 82\nImplicits: 0\n"
            "100% increased Spell Damage\n50% increased Critical Hit Chance\n"
        )
        tgt_xml_mod, ok = xml_mutator.swap_tree_jewel(tgt_xml, str(j0["nodeId"]), better_jewel)
        self.assertTrue(ok)

        rt = WorkbenchRuntime()
        try:
            report = gap_analyzer.analyze_gap(my_xml, tgt_xml_mod, rt.pool())
            jewel_steps = [u for u in report["slotUpgrades"] if "Jewel" in u["slot"]]
            self.assertGreaterEqual(len(jewel_steps), 3)
            changed = next((u for u in jewel_steps if u["slot"] == j0["slot"]), None)
            self.assertIsNotNone(changed)
            self.assertFalse(changed.get("isIdentical"))

            # 验证 analyze_slot_affixes 也支持直接剥离珠宝词缀
            aff_rep = gap_analyzer.analyze_slot_affixes(my_xml, tgt_xml_mod, j0["slot"], rt.pool())
            self.assertEqual(aff_rep["itemName"], "Mirror Jewel")
            self.assertEqual(len(aff_rep["affixes"]), 2)
        finally:
            rt.shutdown()


class TestLlmAdvisorAndEndpoints(unittest.TestCase):
    def test_llm_config_masking_and_mock_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = llm_advisor.save_llm_config(
                {
                    "enabled": True,
                    "provider": "deepseek",
                    "baseUrl": "https://api.deepseek.com/v1",
                    "apiKey": "sk-test-1234567890",
                    "model": "deepseek-chat",
                },
                state_dir=tmp,
            )
            self.assertEqual(cfg["apiKey"], "sk-test-1234567890")
            pub = llm_advisor.public_llm_config(cfg)
            self.assertTrue(pub["hasApiKey"])
            self.assertNotIn("1234567890", pub["apiKeyPreview"])

            # 留空 apiKey 更新其他字段时应保留已存 key
            cfg2 = llm_advisor.save_llm_config(
                {"enabled": True, "provider": "deepseek", "baseUrl": "https://api.deepseek.com/v1", "model": "deepseek-reasoner"},
                state_dir=tmp,
            )
            self.assertEqual(cfg2["apiKey"], "sk-test-1234567890")
            self.assertEqual(cfg2["model"], "deepseek-reasoner")

            fake_profile = {
                "build": {
                    "character": {"className": "Druid", "ascendancy": "Oracle", "level": 100},
                    "passiveTree": {"totalAllocated": 110, "keystones": [], "ascendancyNodes": [], "notables": []},
                    "equippedItems": [],
                    "jewels": [{"slot": f"Tree Jewel (Node {i})", "rarity": "RARE", "name": f"J{i}", "baseType": "Sapphire", "mods": ["+10% Crit"]} for i in range(13)],
                    "skillGroups": [],
                },
                "positioning": {"archetype": "Oracle / Spell Totem", "budgetTier": "mirror", "pitfalls": []},
                "simulation": {"dps": 2900000},
            }
            digest = llm_advisor.build_target_digest(fake_profile)
            self.assertEqual(len(digest["jewels"]), 13)

            captured: dict[str, object] = {}

            def mock_post(url: str, headers: dict[str, str], payload: dict[str, object], timeout: float) -> dict[str, object]:
                captured["url"] = url
                captured["payload"] = payload
                return {"model": "deepseek-reasoner", "choices": [{"message": {"content": "核心机制清晰：共13颗珠宝协同。"}}]}

            res = llm_advisor.analyze_with_llm("target", profile=fake_profile, state_dir=tmp, http_post=mock_post)
            self.assertEqual(captured["url"], "https://api.deepseek.com/v1/chat/completions")
            self.assertIn("13颗珠宝", res["text"])

    def test_llm_digest_preserves_main_skill_beyond_index_8_and_full_context(self) -> None:
        skill_groups = [
            {"index": i, "isMain": i == 9, "slot": "", "weaponSet": "", "gems": [{"name": f"Skill{i}", "level": 20, "quality": 20}]}
            for i in range(1, 12)
        ]
        notables = [{"name": f"Notable{i}"} for i in range(1, 55)]
        prof = {
            "build": {
                "character": {"className": "Druid", "ascendancy": "Oracle", "level": 100},
                "passiveTree": {
                    "totalAllocated": 160,
                    "keystones": [{"name": "Chaos Inoculation"}],
                    "ascendancyNodes": [{"name": "Forced Outcome"}],
                    "anointedNotables": [{"slot": "Helmet", "name": "Dominion", "stats": ["Archon Buffs have no recovery period"]}],
                    "weaponSet1Notables": [{"name": "Preservation"}],
                    "weaponSet2Notables": [{"name": "Near at Hand"}],
                    "notables": notables,
                },
                "equippedItems": [
                    {
                        "slot": "Weapon 1 Swap",
                        "rarity": "RARE",
                        "name": "Wrath Song",
                        "baseType": "Dueling Wand",
                        "socketedRunes": ["Legacy of Lifesprig"],
                        "mods": ["+4 to Level of all Spell Skills"],
                    }
                ],
                "jewels": [],
                "flasksAndCharms": [
                    {"slot": "Flask 2", "rarity": "UNIQUE", "name": "Melting Maelstrom", "baseType": "Ultimate Mana Flask", "mods": ["215% increased Duration"]}
                ],
                "skillGroups": skill_groups,
            },
            "positioning": {"archetype": "Oracle / Skill9", "budgetTier": "mirror", "pitfalls": []},
            "simulation": {"dps": 1941432.1},
        }
        digest = llm_advisor.build_target_digest(prof)
        # 主技能（原本排第 9）必须保留且排在首位
        self.assertTrue(digest["skillGroups"][0]["isMain"])
        self.assertIn("Skill9(20/20%)", digest["skillGroups"][0]["gems"])
        self.assertEqual(len(digest["skillGroups"]), 11)
        # 全部 54 个大点、涂油、符文与药剂护符不得截断丢失
        self.assertEqual(len(digest["passiveTree"]["notables"]), 54)
        self.assertEqual(digest["passiveTree"]["anointedNotables"][0]["name"], "Dominion")
        self.assertEqual(digest["equippedItems"][0]["socketedRunes"], ["Legacy of Lifesprig"])
        self.assertEqual(digest["flasksAndCharms"][0]["name"], "Melting Maelstrom")

    def test_structured_ai_takeover_merges_positioning_and_caches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = llm_advisor.save_llm_config(
                {
                    "enabled": True,
                    "provider": "deepseek",
                    "baseUrl": "https://api.deepseek.com/v1",
                    "apiKey": "sk-test-structured",
                    "model": "deepseek-chat",
                },
                state_dir=tmp,
            )
            calls = {"count": 0}
            ai_json = {
                "archetype": "Oracle / Grim Pillars 双图腾",
                "oneLineIdea": "以 6 孔符文杖 + 13 珠宝 2138% 暴伤驱动双图腾冰柱，低血 Pain Attunement 配双诅咒与副手幸运格挡。",
                "scenarioTags": ["Oracle", "低血 ES", "双图腾 454万DPS", "13珠宝 The Adorned"],
                "budgetTier": "mirror",
                "coreAffixes": ["暴击伤害/法术暴伤", "+法术技能等级", "高能量护盾 (ES)"],
                "pitfalls": ["火冰抗依赖执政官 +20% 全抗补满，PoB 未勾选时看似不满抗。"],
                "coreGearNotes": {"Weapon 1": "核心 300% 符文放大杖，脱掉 DPS 暴跌"},
                "contentReadiness": {
                    "overallTier": "终局 T4 全通镜级秒杀档",
                    "overallGrade": "S",
                    "summary": "双图腾 454 万真实输出轻松秒杀 T4。",
                    "items": [
                        {"id": "pinnacle_t4_uber", "grade": "S", "verdict": "碾压速通", "reason": "双图腾 454 万 DPS + 反转抗性"}
                    ],
                },
                "analysisMarkdown": "### 1. 核心机制\n双图腾冰柱联动。",
            }

            def mock_post(url: str, headers: dict[str, str], payload: dict[str, object], timeout: float) -> dict[str, object]:
                calls["count"] += 1
                return {
                    "model": "deepseek-chat",
                    "choices": [{"message": {"content": "```json\n" + json.dumps(ai_json, ensure_ascii=False) + "\n```"}}],
                }

            prof = build_profile.extract_profile(
                _sample_code(),
                run_simulation=False,
                use_ai=True,
                state_dir=tmp,
                llm_cfg=cfg,
                llm_http_post=mock_post,
            )
            self.assertEqual(calls["count"], 1)
            self.assertEqual(prof["positioning"]["analyzedBy"], "ai")
            self.assertEqual(prof["positioning"]["archetype"], "Oracle / Grim Pillars 双图腾")
            self.assertEqual(prof["positioning"]["contentReadiness"]["overallGrade"], "S")
            self.assertIn("双图腾冰柱联动", prof["aiAnalysis"]["text"])
            w1 = next((g for g in prof["coreGearHighlights"] if g["slot"] == "Weapon 1"), None)
            if w1:
                self.assertIn("符文放大", w1.get("aiNote", ""))

    def test_http_llm_config_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rt = WorkbenchRuntime(state_dir=tmp)
            srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(rt))
            port = srv.server_address[1]
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/llm/config", timeout=5) as r:
                    d = json.loads(r.read().decode("utf-8"))
                self.assertTrue(d["ok"])
                self.assertFalse(d["config"]["enabled"])

                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/llm/config",
                    data=json.dumps({
                        "enabled": True,
                        "provider": "openai",
                        "baseUrl": "https://api.openai.com/v1",
                        "apiKey": "sk-unit-test-9999",
                        "model": "gpt-4o-mini",
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json", "X-Auth-Token": rt.token},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as r:
                    saved = json.loads(r.read().decode("utf-8"))
                self.assertTrue(saved["ok"])
                self.assertTrue(saved["config"]["enabled"])
                self.assertTrue(saved["config"]["hasApiKey"])

                with urllib.request.urlopen(f"http://127.0.0.1:{port}/meta/patch-dict", timeout=5) as r:
                    pd = json.loads(r.read().decode("utf-8"))
                self.assertTrue(pd["ok"])
                self.assertGreater(pd["patchDict"]["nodeCount"], 500)
                self.assertGreater(pd["patchDict"]["gemCount"], 500)
            finally:
                srv.shutdown()
                srv.server_close()
                rt.shutdown()

    def test_twister_regression_blindspots_and_stream_protocols(self) -> None:
        from backend.engine import tree_index

        # 1. 升华分支子节点 Motoric Implants (63259) 不再丢失
        _, nodes = tree_index.load_tree_nodes("0_5")
        self.assertEqual(nodes["63259"]["kind"], "ascendancy")
        self.assertEqual(nodes["63259"]["name"], "Motoric Implants")

        # 2. 装备 Ward 与 LevelReq 完整保留
        glove = pob_parser.parse_item_text(
            "Rarity: RARE\nPhoenix Claw\nRuneforged Secured Wraps\nWard: 166\nLevelReq: 80\nImplicits: 0\n+2 to Level of all Projectile Skills\n"
        )
        self.assertEqual(glove.get("levelReq"), 80)
        self.assertEqual((glove.get("properties") or {}).get("Ward"), "166")

        # 3. 主手攻击矛 + 副手精魂权杖不再误判为 caster
        ws_role = build_profile.classify_weapon_set([
            {
                "name": "The Ordained",
                "baseType": "Grand Spear",
                "mods": ["169% increased Physical Damage", "Adds 1 to 250 Lightning Damage", "+7.51% to Critical Hit Chance"],
            },
            {
                "name": "Guiding Palm of the Eye",
                "baseType": "Shrine Sceptre",
                "mods": ["Gain 25% of Damage as Extra Cold Damage", "15% increased Spirit"],
            },
        ])
        self.assertEqual(ws_role["role"], "damage")
        self.assertIn("副手精魂权杖", ws_role["detail"])

        # 4. 两段式协议（轻量 JSON 头含 gearTiers + --- + Markdown 正文）与 copy/consult Prompt
        raw_reply = (
            '```json\n{"archetype":"古灵 / 旋龙","oneLineIdea":"WS1旋风斩+WS2旋龙","gearTiers":{"mandatory":["The Taming"],"amplifier":["The Ordained"],"luxury":["Headhunter"]}}\n```\n'
            "---\n### 1. ⚙️ 核心输出循环与隐藏联动门槛\n手套必须 ≥73 Ward。"
        )
        struct, md = llm_advisor.split_structured_header_and_markdown(raw_reply)
        self.assertIsNotNone(struct)
        self.assertEqual(struct["gearTiers"]["mandatory"], ["The Taming"])
        self.assertIn("手套必须 ≥73 Ward", md)

        consult_p = llm_advisor.build_consult_prompt({
            "characterLevel": 80,
            "budgetDivine": 10,
            "freeformQuery": {"ascendancy": "古灵使徒", "skillIdea": "旋龙"},
        })
        self.assertIn("Lv.80", consult_p)
        self.assertIn("10 Divine", consult_p)

    def test_v4_archetype_metrics_and_v10_endgame_cap_prompt(self):
        from backend.domain import engine_facts

        raw_sim = {
            "metricsVersion": 4,
            "dps": 18_500_000,
            "dpsSource": "CombinedDPS",
            "mainSkillName": "Explosive Shot",
            "mainSkillLevel": 26,
            "skillArchetype": "crossbow_grenade",
            "magazineSize": 6,
            "reloadTime": 0.65,
            "reloadSpeedMod": 1.45,
            "aoeRadius": 3.2,
            "speed": 4.2,
            "ehp": 62_000,
            "physMaxHit": 24_000,
            "eleMaxHit": 52_000,
            "allSkillsDps": [
                {
                    "name": "Explosive Shot",
                    "level": 26,
                    "isMain": True,
                    "archetype": "crossbow_grenade",
                    "dps": 18_500_000,
                    "magazineSize": 6,
                    "reloadTime": 0.65,
                    "reloadSpeedMod": 1.45,
                    "speed": 4.2,
                },
                {
                    "name": "Cast on Critical",
                    "level": 20,
                    "archetype": "trigger_meta",
                    "triggerRate": 4.8,
                    "metaEnergyGainMod": 1.6,
                    "maxEnergy": 100,
                    "manaPerSecCost": 180,
                },
            ],
        }
        metrics = engine_facts.extract_combat_metrics(raw_sim)
        self.assertEqual(metrics["metricsVersion"], 4)
        main_m = metrics["skillMechanics"]["mainSkill"]
        self.assertEqual(main_m["archetype"], "crossbow_grenade")
        self.assertEqual(main_m["magazineSize"], 6)
        self.assertAlmostEqual(main_m["reloadTimeSec"], 0.65)
        self.assertAlmostEqual(main_m["reloadSpeedMod"], 1.45)

        trig_row = next(s for s in metrics["skillMechanics"]["keySkills"] if s["name"] == "Cast on Critical")
        self.assertEqual(trig_row["archetype"], "trigger_meta")
        self.assertEqual(trig_row["maxEnergy"], 100)
        self.assertEqual(trig_row["manaPerSecCost"], 180.0)

        profile = {
            "build": {"character": {"className": "Mercenary", "ascendancy": "Gemling Legionnaire", "level": 95}},
            "positioning": {"archetype": "Crossbow"},
            "engineFacts": {**metrics, "effectiveDps": 18_500_000, "dpsSource": "CombinedDPS"},
            "simulation": build_profile._format_sim_summary(raw_sim),
        }
        digest = llm_advisor.build_rating_digest(profile)
        prompt = llm_advisor.build_rating_prompt(digest)
        self.assertIn("并列要求、必须同时满足而非二选一", prompt)
        self.assertIn("必须**同时满足**「8 词 T16」与「高难赛季玩法", prompt)
        self.assertIn("crossbow_grenade", prompt)
        self.assertIn("reloadTimeSec", prompt)
        summary = llm_advisor._format_engine_metrics_summary(profile)
        self.assertIn("弩箭/手雷", summary)
        self.assertIn("弹匣 6/装填 0.65s", summary)

    def test_v5_twister_multihit_ttk_mana_deflect_calibration(self):
        """验证 PoB2 单发旋龙面板(306k)能被引擎自动还原复数击中、TTK、节拍蓝耗与偏转防御。"""
        from backend.domain import engine_facts
        from backend.engine import pob_parser

        fixture = os.path.join(os.path.dirname(__file__), "fixtures", "sample.pob")
        with open(fixture, encoding="utf-8") as f:
            xml = pob_parser.decode_pob(f.read().strip())
        parsed = pob_parser.parse_pob_xml(xml)
        st = parsed["character"]["stats"]

        raw_sim = {
            "metricsVersion": 5,
            "dps": st["CombinedDPS"],
            "combinedDps": st["CombinedDPS"],
            "dpsSource": "CombinedDPS",
            "mainSkillName": "Twister",
            "mainSkillLevel": 20,
            "skillArchetype": "roaming_bouncing_proj",
            "pobSingleHitOnly": 1,
            "bouncesOffTerrain": 1,
            "pierceAll": 1,
            "hitIntervalSec": 0.66,
            "duration": 3.0,
            "projSpeedMod": 1.36,
            "projectileCount": 1,
            "sealMax": st["SealMax"],
            "avgHit": st["AverageDamage"],
            "speed": st["Speed"],
            "frenzyChargesMax": st["FrenzyChargesMax"],
            "ehp": st["TotalEHP"],
            "life": st["Life"],
            "es": st["EnergyShield"],
            "mana": st["Mana"],
            "manaUnreserved": st["ManaUnreserved"],
            "manaCost": st["ManaCost"],
            "manaPerSecCost": st["ManaPerSecondCost"],
            "manaRegen": st["ManaRegenRecovery"],
            "evadeChance": st["EvadeChance"],
            "deflectChance": st["DeflectChance"],
            "deflectionRating": st["DeflectionRating"],
            "physMaxHit": st["PhysicalMaximumHitTaken"],
            "eleMaxHit": st["FireMaximumHitTaken"],
            "chaosMaxHit": st["ChaosMaximumHitTaken"],
            "spirit": st["Spirit"],
            "spiritUnreserved": st["SpiritUnreserved"],
            "spiritUnreservedSet1": 15.0,
            "spiritUnreservedSet2": 115.0,
            "allSkillsDps": [
                {"name": "Twister", "level": 20, "isMain": True, "archetype": "roaming_bouncing_proj", "dps": st["CombinedDPS"], "manaCost": 56.0, "speed": st["Speed"]},
                {"name": "Whirling Slash", "level": 1, "isTravel": True, "manaCost": 8.0, "speedWs1": 6.5, "speed": 6.5},
                {"name": "Barrage", "level": 19, "manaCost": 24.0, "cooldown": 4.0},
            ],
        }
        metrics = engine_facts.extract_combat_metrics(raw_sim, parsed)
        mh = metrics["skillMechanics"]["multiHitAudit"]
        self.assertEqual(mh["cadenceArchetype"], "CADENCE_BURST")
        self.assertTrue(mh["pobDisplaysSingleHitOnly"])
        self.assertGreater(mh["multiHitFactorFull"], 20.0)
        self.assertGreater(mh["effectiveDpsRepresentative"], 10_000_000)

        ttk = metrics["offense"]["ttkAnchors"]
        self.assertTrue(ttk["clearsSoloEndgameCap"])

        ms = metrics["sustain"]["manaSustainAudit"]
        self.assertLess(ms["actualManaCostPerSec"], ms["pobRawManaPerSecCost"])
        self.assertEqual(ms["bossManaVerdict"], "PASS")

        da = metrics["defense"]["defenseAudit"]
        self.assertEqual(da["paradigm"], "evasion_deflect_recovery")
        self.assertGreater(da["deflectedPhysMaxHit"], da["rawPhysMaxHit"])
        self.assertTrue(any("Ghost Dance" in x for x in da["skillAndGearDefenseLayers"]))
        self.assertTrue(any("Headhunter" in x for x in da["skillAndGearDefenseLayers"]))

        hints = metrics["pobAssumptions"].get("brokenSynergyHints") or []
        self.assertTrue(any("双武器套切换假红线" in h for h in hints))

        # 同时验证 109M 面板旋风直接命中 6人满队 Uber 秒杀锚点
        raw_109m = {**raw_sim, "dps": 109_290_000, "combinedDps": 109_290_000, "pobSingleHitOnly": 0, "gemCount": 6}
        m_109 = engine_facts.extract_combat_metrics(raw_109m, parsed)
        self.assertTrue(m_109["offense"]["ttkAnchors"]["clearsParty6UberEasily"])
        self.assertTrue(m_109["offense"]["ttkAnchors"]["clearsParty6Delirium200Easily"])
        self.assertLessEqual(m_109["offense"]["ttkAnchors"]["targets"]["party6_uber_boss"]["effectiveTtkSec"], 1.5)


if __name__ == "__main__":
    unittest.main()
