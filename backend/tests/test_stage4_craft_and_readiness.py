"""Stage 4 后端测试：BD 可打内容分级评估 + RePoE 全槽位词缀池 + PoB2 精华表 + 买成品 vs 半成品加工决策。"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from backend.api.server import WorkbenchRuntime, make_handler
from backend.data import affix_updater
from backend.domain import build_profile, engine_facts, llm_advisor
from backend.domain.crafting import affix_pool, compare as craft_compare
from backend.market import ninja_rates

FIXTURE_POB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "sample.pob")


def _sample_code() -> str:
    with open(FIXTURE_POB, encoding="utf-8") as f:
        return f.read().strip()


class TestContentReadinessGrading(unittest.TestCase):
    def test_no_regex_rating_without_ai(self) -> None:
        prof = build_profile.extract_profile(_sample_code(), run_simulation=False)
        self.assertIsNone(prof["positioning"].get("contentReadiness"))

    def test_engine_mobility_metrics_and_ai_rating(self) -> None:
        raw_sim = {
            "metricsVersion": 2,
            "dps": 109_290_000.0,
            "dpsSource": "CombinedDPS",
            "mainSkillName": "Twister",
            "mainSkillLevel": 28,
            "activeTotemLimit": 0,
            "skillPart": 2,
            "skillPartName": "Empowered Twister",
            "mainSkillParts": ["Base Twister", "Empowered Twister"],
            "skillStageCount": 3,
            "skillStageMax": 3,
            "gemCount": 1,
            "duration": 5.4,
            "durationMod": 1.8,
            "projSpeedMod": 2.35,
            "projectileCount": 6,
            "aoeRadius": 3.2,
            "aoeMod": 1.45,
            "sealMax": 3,
            "sealCooldown": 0.45,
            "timeMaxSeals": 1.35,
            "ehp": 47_900.0,
            "life": 1948.0,
            "lifeUnreserved": 1948.0,
            "lifeUnreservedPct": 100.0,
            "es": 3690.0,
            "mana": 900.0,
            "fireRes": 75.0,
            "coldRes": 75.0,
            "lightningRes": 75.0,
            "chaosRes": 75.0,
            "critChance": 75.0,
            "critMult": 411.1,
            "speed": 4.2,
            "moveSpeed": 18.0,
            "skillMoveSpeed": 18.0,
            "actionSpeed": 12.0,
            "physMaxHit": 14500.0,
            "fireMaxHit": 38000.0,
            "coldMaxHit": 38000.0,
            "lightningMaxHit": 38000.0,
            "eleMaxHit": 38000.0,
            "chaosMaxHit": 22000.0,
            "physDR": 22.0,
            "lifeRegen": 180.0,
            "esRecharge": 1100.0,
            "enemyIsBoss": "Pinnacle",
            "enemyLevel": 82,
            "spirit": 240.0,
            "spiritUnreserved": 20.0,
            "weaponSetStats": {
                "ws1": {"dps": 250000.0, "dpsSource": "CombinedDPS", "ehp": 42000.0, "speed": 9.42, "moveSpeed": 18.0, "skillMoveSpeed": 18.0, "actionSpeed": 12.0},
                "ws2": {"dps": 109_290_000.0, "dpsSource": "CombinedDPS", "ehp": 47900.0, "speed": 4.2, "moveSpeed": 18.0, "skillMoveSpeed": 18.0, "actionSpeed": 12.0},
            },
            "allSkillsDps": [
                {"index": 1, "isMain": True, "isTravel": False, "weaponSet": 2, "name": "Twister", "level": 28, "dps": 109_290_000.0, "speed": 4.2, "duration": 5.4, "durationMod": 1.8, "projSpeedMod": 2.35, "projCount": 6, "skillPartName": "Empowered Twister", "skillStageCount": 3, "skillStageMax": 3, "sealMax": 3},
                {"index": 2, "isMain": False, "isTravel": True, "weaponSet": 1, "name": "Whirling Slash", "level": 5, "dps": 12000.0, "speed": 9.42, "speedWs1": 9.42, "speedWs2": 3.1, "cooldown": 0.0, "castTime": 0.106, "moveSpeed": 18.0, "skillMoveSpeed": 18.0, "aoeRadius": 4.1, "skillStageCount": 3, "skillStageMax": 3},
            ],
        }
        metrics = engine_facts.extract_combat_metrics(raw_sim)
        self.assertEqual(metrics["metricsVersion"], 2)
        self.assertEqual(metrics["skillMechanics"]["mainSkill"]["durationSec"], 5.4)
        self.assertEqual(metrics["skillMechanics"]["mainSkill"]["projSpeedMod"], 2.35)
        self.assertEqual(metrics["skillMechanics"]["mainSkill"]["skillStageCount"], 3)
        self.assertEqual(metrics["skillMechanics"]["mainSkill"]["skillPartName"], "Empowered Twister")
        self.assertEqual(metrics["skillMechanics"]["mainSkill"]["sealMax"], 3)
        self.assertEqual(metrics["pobAssumptions"]["enemyIsBoss"], "Pinnacle")
        self.assertEqual(metrics["mobility"]["walkMoveSpeedPct"], 18.0)
        self.assertEqual(metrics["mobility"]["actionSpeedPct"], 12.0)
        self.assertEqual(len(metrics["mobility"]["travelSkills"]), 1)
        self.assertEqual(metrics["mobility"]["travelSkills"][0]["name"], "Whirling Slash")
        self.assertAlmostEqual(metrics["mobility"]["travelSkills"][0]["usesPerSec"], 9.42, places=2)
        self.assertIn("Whirling Slash 9.42/s", metrics["mobility"]["primaryTravelSummary"])

        prof = build_profile.extract_profile(
            _sample_code(),
            run_simulation=True,
            run_ablation=False,
            cached_base_sim=raw_sim,
            use_ai=False,
        )
        self.assertIsNone(prof["positioning"]["contentReadiness"])
        self.assertEqual(prof["simulation"]["travelSkills"][0]["name"], "Whirling Slash")
        self.assertEqual(prof["simulation"]["duration"], 5.4)
        self.assertEqual(prof["simulation"]["projSpeedMod"], 2.35)

        captured: dict[str, object] = {}

        def mock_post(url: str, headers: dict[str, str], payload: dict[str, object], timeout: float) -> dict[str, object]:
            captured["user"] = payload["messages"][1]["content"]  # type: ignore[index]
            ai_json = {
                "archetype": "古灵使徒 / 双武器组旋风斩+旋龙",
                "oneLineIdea": "WS1 9.42/s 旋风斩极速位移 + WS2 1.09 亿 DPS 旋龙清场。",
                "defenseSystem": "血盾混合 5638 池 + 全 75% 四抗",
                "scenarioTags": ["古灵使徒", "9.42/s 旋风斩位移", "1.09亿 DPS"],
                "budgetTier": "mirror",
                "contentReadiness": {
                    "overallTier": "终局 T4 全通极速秒杀档",
                    "overallGrade": "S",
                    "summary": "PoB2 实算 109.29M DPS + Whirling Slash 9.42/s 位移 + 5.4s 持续/2.35x 投速，全终局碾压。",
                    "radar": [
                        {"id": "clearSpeed", "label": "清图效率", "score": 98, "metric": "投速×2.35 · 持续5.4s", "reason": "旋龙全屏覆盖"},
                        {"id": "bossing", "label": "攻坚爆发", "score": 99, "metric": "109.3M (Pinnacle)", "reason": "数秒秒杀 T4"},
                        {"id": "mobility", "label": "机动位移", "score": 96, "metric": "旋风斩 9.42/s", "reason": "WS1 极速位移"},
                        {"id": "survivability", "label": "生存硬度", "score": 78, "metric": "EHP 48k / 物14.5k", "reason": "全满抗中等一击线"},
                        {"id": "sustain", "label": "续航手感", "score": 84, "metric": "回盾1100/s", "reason": "循环流畅"},
                        {"id": "costEfficiency", "label": "造价性价比", "score": 28, "metric": "镜级毕业造价", "reason": "造价门槛极高"},
                    ],
                    "items": [
                        {"id": "t15_mapping", "grade": "S", "verdict": "碾压速通", "reason": "Whirling Slash 9.42/s 极速位移 + 2.35x 投速全屏覆盖"},
                        {"id": "juiced_t15_delirium", "grade": "S", "verdict": "碾压速通", "reason": "109.29M DPS + 5.4s 持续 + 75% 混抗"},
                        {"id": "pinnacle_t0_t2", "grade": "S", "verdict": "碾压速通", "reason": "109.29M 单体秒杀"},
                        {"id": "pinnacle_t4_uber", "grade": "S", "verdict": "碾压速通", "reason": "Pinnacle 预设 109.29M 爆发碾压 T4"},
                        {"id": "trials_sekhema_chaos", "grade": "A", "verdict": "稳健推荐", "reason": "高机动+75%混抗"},
                    ],
                },
            }
            return {
                "model": "test-model",
                "choices": [{"message": {"content": "```json\n" + json.dumps(ai_json, ensure_ascii=False) + "\n```"}}],
            }

        with tempfile.TemporaryDirectory() as tmp:
            cfg = llm_advisor.save_llm_config(
                {"enabled": True, "provider": "deepseek", "baseUrl": "https://api.deepseek.com/v1", "apiKey": "sk-test", "model": "deepseek-chat"},
                state_dir=tmp,
            )
            rated = llm_advisor.rate_target_with_ai(prof, cfg=cfg, state_dir=tmp, http_post=mock_post, use_cache=False)
            prompt_txt = str(captured["user"])
            self.assertIn("Whirling Slash", prompt_txt)
            self.assertIn("109290000", prompt_txt)
            self.assertIn("projSpeedMod", prompt_txt)
            self.assertIn("durationSec", prompt_txt)
            self.assertIn("Empowered Twister", prompt_txt)
            self.assertIn("Pinnacle", prompt_txt)
            cr = rated["positioning"]["contentReadiness"]
            self.assertEqual(cr["confidence"], "ai")
            self.assertEqual(cr["overallGrade"], "S")
            self.assertEqual(len(cr["items"]), 5)
            self.assertEqual(len(cr["radar"]), 6)
            self.assertEqual(cr["radar"][0]["id"], "clearSpeed")
            self.assertEqual(cr["radar"][0]["score"], 98)
            self.assertEqual(cr["radar"][5]["id"], "costEfficiency")
            self.assertEqual(cr["radar"][5]["score"], 28)
            self.assertIn("DPS 109.29M", cr["engineMetricsSummary"])
            self.assertIn("持续 5.40s", cr["engineMetricsSummary"])
            self.assertIn("投速 ×2.35", cr["engineMetricsSummary"])


class TestStage4CraftingEngine(unittest.TestCase):
    def test_multi_slot_affix_pool_and_pob2_essences(self) -> None:
        self.assertEqual(affix_pool.resolve_item_class("Weapon 1", "Winged Spear"), "Spears")
        self.assertEqual(affix_pool.resolve_item_class("Body Armour", "Slipstrike Vest"), "Body Armours")
        self.assertEqual(affix_pool.resolve_item_class("Ring 1"), "Rings")
        self.assertEqual(affix_pool.resolve_item_class("Amulet"), "Amulets")
        self.assertEqual(affix_pool.resolve_item_class("Boots"), "Boots")

        essences_ring = affix_pool.list_essences_for_slot("Rings")
        self.assertGreater(len(essences_ring), 5)

        pool = affix_pool.build_slot_pool("Body Armours", ilvl=82)
        life = affix_pool.match_affix_to_pool("+110 to maximum Life", pool)
        fire = affix_pool.match_affix_to_pool("+35% to Fire Resistance", pool)
        self.assertTrue(life["matched"])
        self.assertEqual(life["side"], "prefix")
        self.assertTrue(fire["matched"])
        self.assertEqual(fire["side"], "suffix")

    def test_semi_exalt_locks_suffix_cap_and_essence_deterministic(self) -> None:
        fake_rates = ninja_rates.fetch_ninja_rates("Standard", allow_network=False)

        # 场景 1：已有 3 条后缀，仍想补第 4 条后缀 -> 补崇高应判定不可行（概率 0）
        full_suffix = craft_compare.compare_craft_vs_buy(
            {
                "slot": "Rings",
                "ilvl": 82,
                "wantAffixes": [
                    "+35% to Fire Resistance",
                    "+35% to Cold Resistance",
                    "+35% to Lightning Resistance",
                    "+20% to Chaos Resistance",
                ],
                "existingAffixes": [
                    "+35% to Fire Resistance",
                    "+35% to Cold Resistance",
                    "+35% to Lightning Resistance",
                ],
                "semiBasePrice": 1.0,
                "finishedBuyPrice": 8.0,
            },
            allow_network_rates=False,
            rates_override=fake_rates,
        )
        self.assertEqual(full_suffix["openSuffixes"], 0)
        exalt_est = next(e for e in full_suffix["estimates"] if e["strategy"] == "semi_exalt")
        self.assertEqual(exalt_est["hitProbability"], 0.0)
        self.assertEqual(full_suffix["recommended"], "buy_finished")

        # 场景 2：半成品差 1 条生命前缀，正好有 Life 精华一发锁定 -> 命中率 100%，便宜过 15D 成品 -> 推荐 craft_semi
        ess_win = craft_compare.compare_craft_vs_buy(
            {
                "slot": "Body Armours",
                "ilvl": 82,
                "wantAffixes": [
                    "+120 to maximum Life",
                    "+35% to Fire Resistance",
                    "+35% to Cold Resistance",
                ],
                "existingAffixes": [
                    "+35% to Fire Resistance",
                    "+35% to Cold Resistance",
                ],
                "semiBasePrice": 1.5,
                "finishedBuyPrice": 15.0,
            },
            allow_network_rates=False,
            rates_override=fake_rates,
        )
        ess_est = next(e for e in ess_win["estimates"] if e["strategy"] == "essence_lock")
        self.assertAlmostEqual(ess_est["hitProbability"], 1.0, places=4)
        self.assertEqual(ess_win["recommended"], "craft_semi")
        self.assertIn("pathofexile.com/trade2/search/poe2", ess_win["finishedTradeUrl"])
        self.assertIn("corrupted", urllib.parse.unquote(ess_win["semiTradeUrl"]))
        self.assertIn("explicit.stat_", urllib.parse.unquote(ess_win["finishedTradeUrl"]))

    def test_chinese_affix_matching_and_updater_builders(self) -> None:
        pool = affix_pool.build_slot_pool("Rings", ilvl=82)
        zh_life = affix_pool.match_affix_to_pool("+120 生命上限", pool)
        zh_fire = affix_pool.match_affix_to_pool("火焰抗性 +35%", pool)
        self.assertTrue(zh_life["matched"])
        self.assertEqual(zh_life["group"], "IncreasedLife")
        self.assertEqual(zh_life["side"], "prefix")
        self.assertTrue(zh_life["tradeStatId"].startswith("explicit.stat_"))
        self.assertTrue(zh_fire["matched"])
        self.assertEqual(zh_fire["group"], "FireResistance")
        self.assertEqual(zh_fire["side"], "suffix")

        # 离线构建器单测（不依赖网络）
        lib = affix_updater.build_affix_library_from_repoe(
            {
                "Life1": {
                    "domain": "item",
                    "generation_type": "prefix",
                    "groups": ["IncreasedLife"],
                    "text": "+(10-19) to [Life|maximum Life]",
                    "spawn_weights": [{"tag": "ring", "weight": 1000}],
                }
            },
            {
                "Metadata/Items/Rings/Ring1": {
                    "name": "Iron Ring",
                    "item_class": "Ring",
                    "drop_level": 1,
                    "release_state": "released",
                }
            },
            {
                "Rings": {
                    "ring,default": {
                        "bases": ["Metadata/Items/Rings/Ring1"],
                        "mods": {"prefix": {"IncreasedLife": {"Life1": 1}}, "suffix": {}},
                    }
                }
            },
            generated_at="2026-11-24 14:00:00",
        )
        self.assertEqual(lib["mods"]["Life1"][0], "+(10-19) to maximum Life")
        self.assertEqual(len(lib["classes"]["Rings"]["prefixes"]), 1)

        tc = affix_updater.build_trade_stats_cache(
            {"result": [{"id": "explicit", "entries": [{"id": "explicit.stat_1", "text": "# to maximum Life", "type": "explicit"}]}]},
            {"result": [{"id": "explicit", "entries": [{"id": "explicit.stat_1", "text": "# 生命上限", "type": "explicit"}]}]},
            generated_at="2026-11-24 14:00:00",
        )
        self.assertEqual(tc["count"], 1)
        self.assertEqual(tc["zhCount"], 1)
        self.assertEqual(tc["entries"][0]["zh"], "# 生命上限")


class TestStage4HttpApi(unittest.TestCase):
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

    def test_craft_meta_and_compare_endpoints(self) -> None:
        with urllib.request.urlopen(f"{self.base}/craft/meta?slot=Spears&ilvl=82", timeout=10) as r:
            meta = json.loads(r.read().decode("utf-8"))
        self.assertTrue(meta["ok"])
        self.assertEqual(meta["itemClass"], "Spears")
        self.assertGreater(len(meta["affixOptions"]), 15)
        self.assertGreater(len(meta["essences"]), 3)
        self.assertIn("libraryStatus", meta)
        self.assertGreater(meta["libraryStatus"]["tradeStats"]["zhCount"], 1000)
        self.assertTrue(any(o.get("zhText") for o in meta["affixOptions"]))

        with urllib.request.urlopen(f"{self.base}/craft/library-status", timeout=10) as r:
            lib_st = json.loads(r.read().decode("utf-8"))
        self.assertTrue(lib_st["ok"])
        self.assertGreater(lib_st["status"]["affixLibrary"]["modsCount"], 500)

        req = urllib.request.Request(
            f"{self.base}/craft/compare",
            data=json.dumps({
                "slot": "Boots",
                "ilvl": 82,
                "wantAffixes": ["30% increased Movement Speed", "+100 to maximum Life", "+35% to Fire Resistance"],
                "existingAffixes": ["30% increased Movement Speed", "+35% to Fire Resistance"],
                "semiBasePrice": 1.2,
                "finishedBuyPrice": 10.0,
            }).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Auth-Token": self.rt.token},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read().decode("utf-8"))
        self.assertTrue(resp["ok"])
        dec = resp["decision"]
        self.assertEqual(dec["itemClass"], "Boots")
        self.assertGreaterEqual(len(dec["estimates"]), 5)
        self.assertIn("bestAction", dec)
        self.assertIn("paretoPlans", dec)
        self.assertIn("whiteBaseTradeUrl", dec)
        self.assertEqual(dec["htcExternalUrl"], "https://www.poe2htc.com/")

    def test_htc_mdp_solver_tier_floors_recovery_and_three_way_verdict(self) -> None:
        fake_rates = ninja_rates.fetch_ninja_rates("Standard", allow_network=False)

        # 1. 高价半成品底子（6D）+ 追高 T 级 -> 应激活预兆剥离/削减救底子，且白底自做更划算时输出 craft_raw 与步骤配方
        res_high = craft_compare.compare_craft_vs_buy(
            {
                "slot": "Body Armours",
                "baseType": "Slipstrike Vest",
                "ilvl": 82,
                "wantAffixes": ["+120 to maximum Life", "+35% to Fire Resistance", "+35% to Cold Resistance"],
                "existingAffixes": ["+120 to maximum Life", "+35% to Fire Resistance"],
                "semiBasePrice": 6.0,
                "whiteBasePrice": 0.1,
                "finishedBuyPrice": 20.0,
                "minTierMode": "high",
            },
            allow_network_rates=False,
            rates_override=fake_rates,
        )
        self.assertTrue(res_high["policySummary"]["recoveryUsed"])
        self.assertGreater(len(res_high["paretoPlans"]), 0)
        self.assertGreater(len(res_high["bestAction"]["steps"]), 0)
        self.assertIn("rarity", urllib.parse.unquote(res_high["whiteBaseTradeUrl"]))

        # 2. 对比 any vs high T 级：high 模式的单轮命中率应严格低于 any 模式
        res_any = craft_compare.compare_craft_vs_buy(
            {
                "slot": "Rings",
                "ilvl": 82,
                "wantAffixes": ["+80 to maximum Life", "+35% to Fire Resistance", "+35% to Cold Resistance"],
                "existingAffixes": ["+80 to maximum Life"],
                "semiBasePrice": 2.0,
                "whiteBasePrice": 0.05,
                "finishedBuyPrice": 12.0,
                "minTierMode": "any",
            },
            allow_network_rates=False,
            rates_override=fake_rates,
        )
        res_strict = craft_compare.compare_craft_vs_buy(
            {
                "slot": "Rings",
                "ilvl": 82,
                "wantAffixes": ["+80 to maximum Life", "+35% to Fire Resistance", "+35% to Cold Resistance"],
                "existingAffixes": ["+80 to maximum Life"],
                "semiBasePrice": 2.0,
                "whiteBasePrice": 0.05,
                "finishedBuyPrice": 12.0,
                "minTierMode": "high",
            },
            allow_network_rates=False,
            rates_override=fake_rates,
        )
        p_any = next(e["expectedCostDivine"] for e in res_any["estimates"] if e["strategy"] == "htc_policy")
        p_strict = next(e["expectedCostDivine"] for e in res_strict["estimates"] if e["strategy"] == "htc_policy")
        self.assertLess(p_any, p_strict)


import urllib.parse  # noqa: E402

if __name__ == "__main__":
    unittest.main()
