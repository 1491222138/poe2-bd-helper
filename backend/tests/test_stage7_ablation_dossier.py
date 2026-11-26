"""Stage 7 测试：单目标 BD 全量引擎消融剖析账本（装备 + 珠宝 + 技能/辅助 + 全量词缀目的 + 造价 + 缓存 + LLM 注入）。"""
from __future__ import annotations

import os
import tempfile
import unittest

from backend import config
from backend.domain import ablation_dossier, build_profile, llm_advisor
from backend.engine import pob_parser, rune_index, tree_index, xml_mutator
from backend.engine.worker_pool import WorkerPool

FIXTURE_POB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "sample.pob")


def _sample_code() -> str:
    with open(FIXTURE_POB, encoding="utf-8") as f:
        return f.read().strip()


class TestHardwareAdaptiveConfig(unittest.TestCase):
    def test_recommend_worker_config_across_machines(self) -> None:
        # 8GB 4核轻薄本：严格限 2 个突发 Worker + 30s 快速回收
        low = config.recommend_worker_config(4, 8.0)
        self.assertEqual(low["recommendedWorkers"], 2)
        self.assertEqual(low["recommendedBurstIdleSec"], 30.0)
        self.assertEqual(low["tier"], "low-ram")

        # 16GB 8核主流机：4 个突发 Worker + 60s 回收
        mid = config.recommend_worker_config(8, 16.0)
        self.assertEqual(mid["recommendedWorkers"], 4)
        self.assertEqual(mid["recommendedBurstIdleSec"], 60.0)

        # 24GB 10核机：5 个突发 Worker
        perf = config.recommend_worker_config(10, 24.0)
        self.assertEqual(perf["recommendedWorkers"], 5)

        # 64GB 16核工作站：上限 6 个突发 Worker，避免内存膨胀与边际收益递减
        high = config.recommend_worker_config(16, 64.0)
        self.assertEqual(high["recommendedWorkers"], 6)


class TestSkillXmlMutators(unittest.TestCase):
    def test_ablate_skill_group_and_gem_and_downgrade(self) -> None:
        xml = pob_parser.decode_pob(_sample_code())
        parsed = pob_parser.parse_pob_xml(xml)
        groups = parsed["skillGroups"]
        self.assertGreater(len(groups), 1)

        # 禁用一个非主技能组
        non_main = next(g for g in groups if not g["isMain"])
        mut_group = xml_mutator.ablate_skill_group(xml, non_main["index"])
        parsed_after = pob_parser.parse_pob_xml(mut_group)
        self.assertEqual(len(parsed_after["skillGroups"]), len(groups) - 1)

        # 禁用主技能组中的一颗辅助宝石
        main_g = next(g for g in groups if g["isMain"])
        self.assertGreater(len(main_g["gems"]), 1)
        mut_gem = xml_mutator.ablate_skill_gem(xml, main_g["index"], 1)
        parsed_gem = pob_parser.parse_pob_xml(mut_gem)
        main_after = next(g for g in parsed_gem["skillGroups"] if g["index"] == main_g["index"])
        self.assertEqual(len(main_after["gems"]), len(main_g["gems"]) - 1)

        # 找一颗 >20 级或 >20 品质宝石降配到 20/20（若样本无则造一颗验证）
        high_pair = None
        for sg in groups:
            for gi, gem in enumerate(sg["gems"]):
                if gem["level"] > 20 or gem["quality"] > 20:
                    high_pair = (sg["index"], gi)
                    break
            if high_pair:
                break
        if high_pair is not None:
            mut_dg = xml_mutator.downgrade_skill_gem(xml, high_pair[0], high_pair[1], max_level=20, max_quality=20)
            p_dg = pob_parser.parse_pob_xml(mut_dg)
            g_dg = next(g for g in p_dg["skillGroups"] if g["index"] == high_pair[0])["gems"][high_pair[1]]
            self.assertLessEqual(g_dg["level"], 20)
            self.assertLessEqual(g_dg["quality"], 20)


class TestFullAblationDossierEngine(unittest.TestCase):
    def test_full_dossier_items_jewels_skills_affixes_cache_and_llm_digest(self) -> None:
        code = _sample_code()
        fake_prices = {
            "league": "Standard",
            "updatedAt": 1700000000000,
            "prices": {"Weapon 1": 12.0, "Body Armour": 8.0},
            "entries": [
                {"slot": "Weapon 1", "kind": "exact", "divineValue": 12.0, "source": "trade"},
                {"slot": "Weapon 1", "kind": "budget", "divineValue": 4.5, "source": "trade"},
                {"slot": "Body Armour", "kind": "exact", "divineValue": 8.0, "source": "trade"},
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            pool = WorkerPool(size=4, min_size=2)
            try:
                dossier = ablation_dossier.build_ablation_dossier(
                    code,
                    pool,
                    price_snapshot=fake_prices,
                    state_dir=tmpdir,
                )
            finally:
                pool.close()

            self.assertFalse(dossier["cached"])
            self.assertGreater(len(dossier["items"]), 0)
            self.assertGreater(len(dossier["skills"]), 0)
            self.assertGreater(dossier["summary"]["totalAffixesTested"], 10)
            self.assertGreater(dossier["totalEstimatedDivine"], 0)

            # 验证每件装备都包含角色定位、造价、抄作业建议与逐条词缀目的
            for item in dossier["items"]:
                self.assertTrue(item["roleLabel"])
                self.assertTrue(item["copyTierLabel"])
                self.assertTrue(item["copyAdvice"])
                for aff in item["affixes"]:
                    self.assertTrue(aff["purposeLabel"])
                    self.assertTrue(aff["purposeDetail"])
                    self.assertIn(aff["copyAction"], ("lock", "downgrade", "craft_later", "cut", "skip"))

            # 验证主技能组拆解了辅助宝石
            main_sk = next((s for s in dossier["skills"] if s["isMain"]), None)
            self.assertIsNotNone(main_sk)
            assert main_sk is not None
            self.assertTrue(any(g["isSupport"] and g["tested"] for g in main_sk["gems"]))

            # 验证价格快照已挂载并计算出占比/性价比
            w1 = next((x for x in dossier["items"] if x["slot"] == "Weapon 1"), None)
            self.assertIsNotNone(w1)
            assert w1 is not None
            self.assertEqual(w1["cost"]["exactDivine"], 12.0)
            self.assertEqual(w1["cost"]["budgetDivine"], 4.5)
            self.assertIsNotNone(w1["cost"]["costSharePct"])

            # 验证缓存秒命中（不再需要真实 worker）
            cached = ablation_dossier.build_ablation_dossier(
                code,
                pool=None,
                price_snapshot=fake_prices,
                allow_ninja_network=False,
                state_dir=tmpdir,
            )
            self.assertTrue(cached["cached"])
            self.assertEqual(cached["buildHash"], dossier["buildHash"])

            # 验证后续价格变动 / 切赛季仍命中本地消融底座（不调 worker），并在底座上瞬时更正造价
            updated_prices = {
                "league": "Forbidden Rites",
                "updatedAt": 1799999999000,
                "prices": {"Weapon 1": 35.0, "Body Armour": 10.0},
                "entries": [
                    {"slot": "Weapon 1", "kind": "exact", "divineValue": 35.0, "source": "manual"},
                    {"slot": "Body Armour", "kind": "exact", "divineValue": 10.0, "source": "manual"},
                ],
            }
            overlayed = ablation_dossier.build_ablation_dossier(
                code,
                pool=None,
                price_snapshot=updated_prices,
                league="Forbidden Rites",
                allow_ninja_network=False,
                state_dir=tmpdir,
            )
            self.assertTrue(overlayed["cached"])
            self.assertEqual(overlayed["league"], "Forbidden Rites")
            w1_after = next(x for x in overlayed["items"] if x["slot"] == "Weapon 1")
            self.assertEqual(w1_after["cost"]["exactDivine"], 35.0)
            self.assertEqual(w1_after["cost"]["priceSource"], "manual")
            self.assertEqual(overlayed["totalEstimatedDivine"], 45.0)

            # 验证更正后的造价已直接持久化到本地，后续读取即得最新造价且缓存文件不产生重复副本
            reloaded = ablation_dossier.get_cached_dossier_by_build(code, state_dir=tmpdir)
            self.assertIsNotNone(reloaded)
            assert reloaded is not None
            w1_reloaded = next(x for x in reloaded["items"] if x["slot"] == "Weapon 1")
            self.assertEqual(w1_reloaded["cost"]["exactDivine"], 35.0)

            # 验证基准模拟结果已随本地底座持久化，供首页秒开免冷启动
            cached_sim = ablation_dossier.get_cached_base_sim(code, state_dir=tmpdir)
            self.assertIsNotNone(cached_sim)
            assert cached_sim is not None
            self.assertGreater(float(cached_sim.get("dps") or 0), 0)

            # 验证装备插槽物品（符文 / 灵核 / 神像 / 遗物）逐颗消融与元数据
            items_with_socketed = [
                it for it in dossier["items"]
                if isinstance(it.get("runeImpact"), dict) and len(it["runeImpact"].get("socketedItems") or []) > 0
            ]
            self.assertGreater(len(items_with_socketed), 0)
            self.assertGreater(dossier["summary"]["totalSocketedItemsTested"], 0)
            for it in items_with_socketed:
                for si in it["runeImpact"]["socketedItems"]:
                    self.assertTrue(si["name"])
                    self.assertTrue(si["runeTypeLabel"])
                    self.assertTrue(si["purposeLabel"])
                    self.assertTrue(si["purposeDetail"])

            # 验证升华天赋整套与逐点孤立/分支消融
            asc = dossier.get("ascendancy")
            self.assertIsNotNone(asc)
            assert asc is not None
            self.assertTrue(asc["ascendancy"])
            self.assertGreater(len(asc["nodes"]), 0)
            self.assertTrue(asc["whyChosenSummary"])
            # Implanted Gems (MultipleChoice) + Motoric Implants (Option) 应已合并而非重复出现
            node_names = [n["name"] for n in asc["nodes"]]
            self.assertTrue(any("Implanted Gems" in nm and "Motoric Implants" in nm for nm in node_names))
            # Essence of Virtue 为前置节点，其孤立 DPS 损失不应错误重复计入下游子节点
            eov = next((n for n in asc["nodes"] if "Essence of Virtue" in n["name"]), None)
            self.assertIsNotNone(eov)
            assert eov is not None
            self.assertTrue(eov["hasBranchDescendants"])
            self.assertGreater(eov["branch"]["dpsLossPct"], eov["isolated"]["dpsLossPct"])

            # 验证技能端防御分析（防御角色归类 + 辅助回血/闪避/偏转实算）
            self.assertGreater(len(dossier.get("skillDefenseSummary") or []), 0)
            wind_dancer = next((s for s in dossier["skills"] if "wind dancer" in s["mainGemName"].lower()), None)
            self.assertIsNotNone(wind_dancer)
            assert wind_dancer is not None
            self.assertEqual(wind_dancer["roleKind"], "defense")
            self.assertTrue(wind_dancer["defenseAnalysis"]["isDefensive"])
            self.assertGreater(wind_dancer["ehpLossPct"], 5.0)
            purity = next((s for s in dossier["skills"] if "purity of ice" in s["mainGemName"].lower()), None)
            self.assertIsNotNone(purity)
            assert purity is not None
            self.assertEqual(purity["roleKind"], "defense")
            self.assertGreater(purity["lifeRecoveryDrop"], 10.0)
            vitality_gem = next((g for g in purity["gems"] if "vitality" in g["name"].lower()), None)
            self.assertIsNotNone(vitality_gem)
            assert vitality_gem is not None
            self.assertTrue(vitality_gem["tested"])
            self.assertGreater(vitality_gem["lifeRecoveryDrop"], 10.0)

            # 验证 LLM digest 与 rating digest 自动吸收 ascendancyAblation / coreSocketedItems / skillDefenseBreakdown
            prof = build_profile.extract_profile(code, run_simulation=False)
            prof["ablationDossier"] = ablation_dossier.get_cached_dossier_by_build(code, state_dir=tmpdir)
            digest = llm_advisor.build_target_digest(prof)
            self.assertIn("ablationLedger", digest)
            self.assertIn("ascendancyAblation", digest)
            self.assertGreater(len(digest["ascendancyAblation"]["nodes"]), 0)
            self.assertGreater(len(digest["ablationLedger"]["itemAffixPurposes"]), 0)
            self.assertGreater(len(digest["ablationLedger"]["skillImpacts"]), 0)
            self.assertGreater(len(digest["ablationLedger"]["skillDefenseSummary"]), 0)

            r_digest = llm_advisor.build_rating_digest(prof)
            self.assertIn("ascendancyAblation", r_digest)
            self.assertGreater(len(r_digest["ascendancyAblation"]["nodes"]), 0)
            self.assertGreater(len(r_digest["defense"]["skillDefenseBreakdown"]), 0)
            self.assertGreater(len(r_digest["buildSkeletonAndCost"]["coreSocketedItems"]), 0)

            # 验证 AI 评级结构化清洗完整保留 ascendancyVerdict
            fake_ai = {
                "archetype": "古灵使徒 / 旋龙",
                "contentReadiness": {
                    "overallGrade": "A",
                    "overallTier": "终局稳通",
                    "summary": "测试总结",
                    "ascendancyVerdict": {
                        "ascendancy": "Gemling Legionnaire",
                        "verdict": "选择古灵主要为了品质额外效果与 +2 敏捷技能等级",
                        "keyReasons": ["Advanced Thaumaturgy 带来核心攻防增益", "Essence of Virtue 提供防御屏障"],
                        "tradeoff": "前期 2 点过路收益依赖后置解锁",
                    },
                },
            }
            sanitized = llm_advisor._sanitize_structured_target(fake_ai)
            self.assertEqual(sanitized["contentReadiness"]["ascendancyVerdict"]["ascendancy"], "Gemling Legionnaire")
            merged_pos = llm_advisor.merge_ai_positioning(prof["positioning"], sanitized)
            self.assertEqual(merged_pos["contentReadiness"]["ascendancyVerdict"]["verdict"], fake_ai["contentReadiness"]["ascendancyVerdict"]["verdict"])


class TestSocketedItemAndAscendancyMutators(unittest.TestCase):
    def test_ablate_single_rune_and_passive_nodes(self) -> None:
        xml = pob_parser.decode_pob(_sample_code())
        parsed = pob_parser.parse_pob_xml(xml)
        w1 = next(it for it in parsed["equippedItems"] if it["slot"] == "Weapon 1")
        self.assertGreaterEqual(len(w1.get("runeEntries") or []), 2)

        mut_r = xml_mutator.ablate_item_rune_at(xml, w1["itemId"], 0)
        p_after = pob_parser.parse_pob_xml(mut_r)
        w1_after = next(it for it in p_after["equippedItems"] if it["slot"] == "Weapon 1")
        self.assertEqual(len(w1_after.get("runeEntries") or []), len(w1["runeEntries"]) - 1)

        # 验证 ModRunes 索引解析
        info = rune_index.lookup_socketed_item("Perfect Iron Rune", slot="Body Armour")
        self.assertIsNotNone(info)
        assert info is not None
        self.assertEqual(info["runeTypeLabel"], "符文")
        self.assertGreater(len(info["mods"]), 0)

        # 验证升华消融图计划
        alloc_ids = pob_parser.extract_allocated_node_ids(xml)
        plan = tree_index.plan_ascendancy_ablation(
            alloc_ids,
            parsed["character"]["ascendancy"],
            tree_version=parsed["passiveTree"].get("treeVersion") or None,
        )
        self.assertGreater(len(plan["allAscNodeIds"]), 0)
        self.assertGreater(len(plan["notables"]), 0)

        mut_asc = xml_mutator.ablate_passive_nodes(xml, plan["allAscNodeIds"], granted_skill_names=["Virtuous Barrier"])
        alloc_after = set(pob_parser.extract_allocated_node_ids(mut_asc))
        for nid in plan["allAscNodeIds"]:
            self.assertNotIn(nid, alloc_after)


if __name__ == "__main__":
    unittest.main()
