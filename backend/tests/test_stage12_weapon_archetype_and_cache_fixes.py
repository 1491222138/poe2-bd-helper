"""Stage 12 测试：武器双层建模（5 维探针分型 / 不吃攻速单发大伤 / 挂件武器 / 本地底材推荐 / Trade2 damage+reload_time+edps_max）
与 P0 缺陷修复（active_pool 传参、ensure_reverse_spec 假缓存自愈、分片缓存免重写、Single-Flight 并发去重）。
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
import urllib.parse

from backend.api.server import WorkbenchRuntime
from backend.domain import ablation_dossier, budget_planner, stat_sensitivity, trade_url, weapon_bases
from backend.engine import pob_parser, runefix

FIXTURE_POB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "sample.pob")


def _sample_code() -> str:
    with open(FIXTURE_POB, encoding="utf-8") as f:
        return f.read().strip()


def _make_fake_parsed_with_weapon(
    *,
    base_type: str,
    props: dict[str, str],
    mods: list[str] | None = None,
    implicits: list[str] | None = None,
    main_skill: str = "Hammer of the Gods",
) -> dict:
    return {
        "weaponSets": {"activeWeaponSet": 1},
        "skillGroups": [{"isMain": True, "gems": [{"name": main_skill, "enabled": True}]}],
        "passiveNodes": [],
        "equippedItems": [
            {
                "itemId": "1",
                "slot": "Weapon 1",
                "rarity": "RARE",
                "name": f"Test {base_type}",
                "baseType": base_type,
                "properties": props,
                "mods": mods or ["+4 to Level of all Melee Skills", "+30% to Critical Damage Bonus"],
                "implicits": implicits or [],
            }
        ],
    }


class TestWeaponBasesAndArchetypes(unittest.TestCase):
    def test_weapon_bases_loader_and_archetype_ranking(self) -> None:
        idx = weapon_bases.load_weapon_bases()
        if not idx["byName"]:
            self.skipTest("PoB2 Data/Bases not present in this environment")

        # 确认加载了多类武器底材
        self.assertIn("two hand mace", idx["byClass"])
        self.assertIn("bow", idx["byClass"])
        self.assertIn("crossbow", idx["byClass"])

        # 取一把双手锤底材，验证 attack_hit_no_aps 按单发物理均伤降序排序
        maces = [b for b in idx["byClass"]["two hand mace"] if b["reqLevel"] >= 50]
        self.assertTrue(maces)
        recs_hit = weapon_bases.recommend_weapon_bases(
            maces[0]["baseType"],
            "attack_hit_no_aps",
            damage_preference="physical",
            crit_important=False,
            top_k=4,
        )
        self.assertGreaterEqual(len(recs_hit), 2)
        self.assertGreaterEqual(recs_hit[0]["physAvg"], recs_hit[-1]["physAvg"])

        # 取一把弓，验证 speed_tool 按基础攻速降序排序
        bows = [b for b in idx["byClass"]["bow"] if b["reqLevel"] >= 50]
        self.assertTrue(bows)
        recs_spd = weapon_bases.recommend_weapon_bases(bows[0]["baseType"], "speed_tool", top_k=4)
        self.assertGreaterEqual(recs_spd[0]["apsBase"], recs_spd[-1]["apsBase"])

    def test_weapon_bases_does_not_cache_empty_result(self) -> None:
        """POB2_ROOT 尚未就绪时返回空索引，但绝不写入永久缓存。"""
        orig_pob2_src = weapon_bases.pob2_src
        try:
            weapon_bases.clear_weapon_bases_cache()
            weapon_bases.pob2_src = lambda: "/nonexistent/pob2/src"
            empty_idx = weapon_bases.load_weapon_bases()
            self.assertEqual(empty_idx["byName"], {})
            self.assertIsNone(weapon_bases._WEAPON_BASES_CACHE)
        finally:
            weapon_bases.pob2_src = orig_pob2_src
            weapon_bases.clear_weapon_bases_cache()

    def test_non_aps_physical_weapon_uses_damage_and_edps_max(self) -> None:
        """不吃攻速的纯物理大伤武器：严禁设 dps/pdps/aps，改用 Trade2 原生 damage.min + edps.max。"""
        parsed = _make_fake_parsed_with_weapon(
            base_type="Cultist Greathammer",
            props={
                "Physical Damage": "320-520",
                "Elemental Damage": "0-0",
                "Attacks per Second": "1.05",
                "Critical Hit Chance": "8.5%",
            },
            mods=[
                "+5 to Level of all Melee Skills",
                "+35% to Critical Damage Bonus",
                "160% increased Physical Damage",
            ],
        )
        base_sim = {"dps": 100000.0, "ehp": 50000.0, "mana": 600.0, "critChance": 65.0, "fireRes": 75, "coldRes": 75, "lightningRes": 75, "chaosRes": 50}
        sensitivities = {
            "lvl_melee": {
                "id": "lvl_melee", "bucket": "offense", "zh": "近战技能等级",
                "tradeEn": "+# to Level of all Melee Skills", "dpsPctPerUnit": 11.0,
                "ehpPctPerUnit": 0.0, "manaPctPerUnit": 0.0, "isSkillLevel": True,
                "tested": True, "_patterns": (r"level of all melee skills",),
            },
            "crit_bonus": {
                "id": "crit_bonus", "bucket": "offense", "zh": "暴击伤害加成%",
                "tradeEn": "+#% to Critical Damage Bonus", "dpsPctPerUnit": 0.45,
                "ehpPctPerUnit": 0.0, "manaPctPerUnit": 0.0, "isSkillLevel": False,
                "tested": True, "_patterns": (r"critical damage bonus",),
            },
            "attack_speed": {
                "id": "attack_speed", "bucket": "offense", "zh": "攻击速度%",
                "tradeEn": "#% increased Attack Speed", "dpsPctPerUnit": 0.01,
                "ehpPctPerUnit": 0.0, "manaPctPerUnit": 0.0, "isSkillLevel": False,
                "tested": True, "_patterns": (r"increased attack speed",),
            },
        }
        weapon_audit = {
            "weapon 1": {
                "weaponArchetype": "attack_hit_no_aps",
                "physProbeDpsPct": 16.0,
                "eleProbeDpsPct": 2.0,
                "apsProbeDpsPct": 0.4,
                "critProbeDpsPct": 7.0,
                "reloadProbeDpsPct": 0.0,
                "implicitLossPct": 0.0,
                "implicitCore": False,
                "damagePreference": "physical",
                "apsImportant": False,
                "critImportant": True,
                "reloadImportant": False,
                "panelEquivalence": {"dpsPctPer100AvgHit": 40.0, "dpsPctPer100WeaponDps": 38.1, "dpsPctPer1BaseCrit": 3.5},
            }
        }
        specs = stat_sensitivity._build_slot_functional_specs(parsed, base_sim, sensitivities, weapon_audit)
        wspec = specs["Weapon 1"]
        self.assertEqual(wspec["weaponArchetype"], "attack_hit_no_aps")
        self.assertEqual(wspec["panelRole"], "attack_weapon_hit_phys")
        self.assertIn("damage", wspec["targetPanels"])
        self.assertIn("edps_max", wspec["targetPanels"])
        self.assertIn("crit", wspec["targetPanels"])
        self.assertNotIn("dps", wspec["targetPanels"])
        self.assertNotIn("pdps", wspec["targetPanels"])
        self.assertNotIn("aps", wspec["targetPanels"])
        # offensePool 中不应把攻速或本地点伤重复算入权重
        off_ids = {t["probeId"] for t in wspec["offensePool"]}
        self.assertNotIn("attack_speed", off_ids)

        # 生成官方 Trade2 查询并检查 JSON 结构
        w_search = trade_url.build_weighted_substitute_search(
            parsed["equippedItems"][0],
            league="Standard",
            kind="budget",
            parsed_build=parsed,
            slot_spec=wspec,
        )
        eq_f = w_search["tradeQuery"]["query"]["filters"]["equipment_filters"]["filters"]
        self.assertIn("damage", eq_f)
        self.assertGreater(eq_f["damage"]["min"], 250)
        self.assertIn("edps", eq_f)
        self.assertIn("max", eq_f["edps"])
        self.assertNotIn("dps", eq_f)
        self.assertNotIn("pdps", eq_f)
        self.assertNotIn("aps", eq_f)

    def test_crossbow_reload_time_max_and_stat_stick_no_dps_lock(self) -> None:
        """弩类装填敏感用 reload_time.max；挂件型攻击武器绝不锁 dps/damage/aps。"""
        # 1) 弩蓝图 reload_time
        enriched = trade_url.enrich_copy_plan_items(
            [
                {
                    "slot": "Weapon 1",
                    "action": "buy_budget",
                    "priority": 1,
                    "itemName": "One-Shot Crossbow",
                    "baseType": "Bombard Crossbow",
                    "rarity": "RARE",
                    "lockBase": False,
                    "equipmentFloors": {"damage": 310, "edps_max": 45, "reload_time": 0.8},
                    "offensePool": [{"text": "+4 to Level of all Projectile Skills", "zh": "投射物等级", "weight": 10.0}],
                    "minOffenseScore": 30.0,
                }
            ],
            league="Standard",
        )
        q = json.loads(urllib.parse.unquote(enriched[0]["tradeUrl"].split("?q=", 1)[1]))["query"]
        eq_f = q["filters"]["equipment_filters"]["filters"]
        self.assertEqual(eq_f["damage"]["min"], 310)
        self.assertEqual(eq_f["edps"]["max"], 45)
        self.assertEqual(eq_f["reload_time"]["max"], 0.8)

        # 2) 挂件型攻击武器（例如拿双手锤只吃 +7 近战等级）
        parsed = _make_fake_parsed_with_weapon(
            base_type="Massive Greathammer",
            props={"Physical Damage": "200-350", "Attacks per Second": "1.1", "Critical Hit Chance": "5%"},
            mods=["+7 to Level of all Melee Skills"],
        )
        base_sim = {"dps": 80000.0, "ehp": 40000.0, "mana": 500.0, "critChance": 20.0}
        weapon_audit = {
            "weapon 1": {
                "weaponArchetype": "stat_stick_weapon",
                "physProbeDpsPct": 0.0,
                "eleProbeDpsPct": 0.0,
                "apsProbeDpsPct": 0.0,
                "critProbeDpsPct": 0.0,
                "implicitLossPct": 6.5,
                "implicitCore": True,
                "damagePreference": "any_dps",
                "apsImportant": False,
                "critImportant": False,
                "reloadImportant": False,
            }
        }
        specs = stat_sensitivity._build_slot_functional_specs(parsed, base_sim, {}, weapon_audit)
        wspec = specs["Weapon 1"]
        self.assertEqual(wspec["weaponArchetype"], "stat_stick_weapon")
        self.assertEqual(wspec["targetPanels"], {})
        self.assertTrue(wspec["mustLockBase"])


class _FakePool:
    """轻量假 WorkerPool，验证 active_pool 传递、假缓存自愈与分片缓存。"""

    def __init__(self) -> None:
        self.sim_calls = 0
        self.batch_calls = 0

    def simulate(self, xml: str, lock_dps_key=None, include_audit=False) -> dict:
        self.sim_calls += 1
        return {
            "dps": 120000.0,
            "ehp": 45000.0,
            "mana": 800.0,
            "fireRes": 75.0,
            "coldRes": 75.0,
            "lightningRes": 75.0,
            "chaosRes": 40.0,
            "spirit": 100.0,
            "spiritUnreserved": 20.0,
            "str": 120.0,
            "reqStr": 100.0,
            "dex": 120.0,
            "reqDex": 100.0,
            "int": 120.0,
            "reqInt": 100.0,
            "critChance": 50.0,
            "dpsSource": "CombinedDPS",
            "activeWeaponSet": 1,
            "metricsVersion": 5,
        }

    def simulate_batch(self, xml_list: list[str], lock_dps_key=None, include_audit=False) -> list[dict]:
        self.batch_calls += 1
        out = []
        for x in xml_list:
            # 只要有 Probe_ 注入就给出真实增幅，证明走了实测分支而非离线硬编码降级
            bump = 1.08 if "Probe_" in x else 0.95
            out.append({
                "dps": 120000.0 * bump,
                "ehp": 45000.0 * bump,
                "mana": 800.0 * bump,
                "fireRes": 75.0,
                "coldRes": 75.0,
                "lightningRes": 75.0,
                "chaosRes": 40.0,
                "spirit": 100.0,
                "spiritUnreserved": 20.0,
                "str": 120.0,
                "reqStr": 100.0,
                "dex": 120.0,
                "reqDex": 100.0,
                "int": 120.0,
                "reqInt": 100.0,
                "critChance": 50.0,
                "dpsSource": "CombinedDPS",
                "activeWeaponSet": 1,
            })
        return out


class TestAblationPoolBugShardedCacheAndSingleFlight(unittest.TestCase):
    def test_active_pool_passed_and_sharded_cache_avoids_redundant_write(self) -> None:
        code = _sample_code()
        fake_pool = _FakePool()
        with tempfile.TemporaryDirectory() as td:
            # 1) 传 pool=None + pool_factory，验证不再退化为 tested=False 离线分支，
            #    且消融探针与 reverseSpec 灵敏度探针已合并在同 1 次 simulate_batch 批次内完成
            d1 = ablation_dossier.build_ablation_dossier(
                code,
                pool=None,
                pool_factory=lambda: fake_pool,
                league="Standard",
                allow_ninja_network=False,
                state_dir=td,
            )
            self.assertEqual(fake_pool.batch_calls, 1)
            rev = d1.get("reverseSpec") or {}
            self.assertEqual(rev.get("version"), stat_sensitivity.REVERSE_SPEC_VERSION)
            self.assertGreater(rev.get("probeRuns", 0), 0)
            self.assertTrue(any(s.get("tested") is True for s in (rev.get("statSensitivity") or [])))

            # 2) 验证已按 buildHash 分片写入 dossier_cache/dossier_<hash>.json
            shard_path = ablation_dossier._shard_path(d1["buildHash"], td)
            self.assertTrue(os.path.isfile(shard_path))
            mtime_before = os.path.getmtime(shard_path)

            # 3) 相同定价签名再次命中缓存：不重跑引擎、不重写磁盘
            time.sleep(0.02)
            batch_before = fake_pool.batch_calls
            d2 = ablation_dossier.build_ablation_dossier(
                code,
                pool=None,
                pool_factory=lambda: fake_pool,
                league="Standard",
                allow_ninja_network=False,
                state_dir=td,
            )
            self.assertTrue(d2.get("cached"))
            self.assertEqual(fake_pool.batch_calls, batch_before)
            self.assertEqual(os.path.getmtime(shard_path), mtime_before)

            # 4) 验证 ensure_reverse_spec 对历史假缓存（tested=False）自动刷新
            with open(shard_path, encoding="utf-8") as f:
                corrupted = json.load(f)
            corrupted["reverseSpec"] = {
                "version": "v1-pob2-sensitivity",
                "probeRuns": 0,
                "statSensitivity": [{"id": "lvl_all_skills", "tested": False, "dpsPctPerUnit": 8.0}],
                "slotSpecs": {"Ring 1": {"slot": "Ring 1"}},
            }
            with open(shard_path, "w", encoding="utf-8") as f:
                json.dump(corrupted, f)

            refreshed = ablation_dossier.ensure_reverse_spec(code, pool=fake_pool, state_dir=td)
            self.assertIsNotNone(refreshed)
            self.assertEqual(refreshed["version"], stat_sensitivity.REVERSE_SPEC_VERSION)
            self.assertTrue(any(s.get("tested") is True for s in refreshed["statSensitivity"]))

            # 5) 验证 update_cached_base_sim 在旧单文件迁移后也能正确回写（不再误用 bool 当 key）
            xml = pob_parser.decode_pob(code)
            norm_xml, _ = runefix.normalize_runes(xml)
            ok_upd = ablation_dossier.update_cached_base_sim(code, {"dps": 150000.0, "metricsVersion": 5}, state_dir=td)
            self.assertTrue(ok_upd)
            sim_after = ablation_dossier.get_cached_base_sim(code, state_dir=td)
            self.assertEqual(sim_after["dps"], 150000.0)

    def test_singleflight_deduplicates_concurrent_requests(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            rt = WorkbenchRuntime(state_dir=td)
            calls = {"n": 0}
            lock = threading.Lock()
            results: list[dict] = []

            def slow_work() -> dict:
                with lock:
                    calls["n"] += 1
                time.sleep(0.08)
                return {"ok": True, "val": 42}

            def worker() -> None:
                results.append(rt.run_singleflight("same-build", slow_work))

            threads = [threading.Thread(target=worker) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(calls["n"], 1)
            self.assertEqual(len(results), 5)
            self.assertTrue(all(r == {"ok": True, "val": 42} for r in results))

    def test_budget_planner_phase_combo_verification(self) -> None:
        """验证 0-1 背包产出的 Phase 1 & Phase 2 能自动补跑合穿实算并暴露红线与线性差值。"""
        code = _sample_code()
        xml = pob_parser.decode_pob(code)
        norm_xml, _ = runefix.normalize_runes(xml)
        parsed = pob_parser.parse_pob_xml(norm_xml)
        slots = [it["slot"] for it in (parsed.get("equippedItems") or []) if it.get("rawText")][:4]
        self.assertGreaterEqual(len(slots), 3)

        slot_steps = [
            {"slot": slots[0], "deltaDpsPct": 15.0, "deltaEhpPct": 5.0, "priceDivine": 2.0, "redlines": []},
            {"slot": slots[1], "deltaDpsPct": 12.0, "deltaEhpPct": 4.0, "priceDivine": 3.0, "redlines": []},
            {"slot": slots[2], "deltaDpsPct": 10.0, "deltaEhpPct": 6.0, "priceDivine": 4.0, "redlines": []},
        ]

        class _ComboFakePool:
            def __init__(self) -> None:
                self.batch_lens: list[int] = []

            def simulate_batch(self, xml_list: list[str], lock_dps_key=None, include_audit=False) -> list[dict]:
                self.batch_lens.append(len(xml_list))
                # 第 0 个为 base，第 1 个为 Phase 1 合穿，第 2 个为 Phase 1+2 累计合穿（模拟火抗跌破 75%）
                out = []
                for idx, _ in enumerate(xml_list):
                    if idx == 0:
                        out.append({"dps": 100000.0, "ehp": 40000.0, "fireRes": 75.0, "coldRes": 75.0, "lightningRes": 75.0, "chaosRes": 40.0, "str": 100, "reqStr": 90, "dex": 100, "reqDex": 90, "int": 100, "reqInt": 90, "spirit": 100, "spiritUnreserved": 10})
                    elif idx == 1:
                        out.append({"dps": 130000.0, "ehp": 44000.0, "fireRes": 75.0, "coldRes": 75.0, "lightningRes": 75.0, "chaosRes": 40.0, "str": 100, "reqStr": 90, "dex": 100, "reqDex": 90, "int": 100, "reqInt": 90, "spirit": 100, "spiritUnreserved": 10})
                    else:
                        out.append({"dps": 142000.0, "ehp": 46000.0, "fireRes": 62.0, "coldRes": 75.0, "lightningRes": 75.0, "chaosRes": 40.0, "str": 100, "reqStr": 90, "dex": 100, "reqDex": 90, "int": 100, "reqInt": 90, "spirit": 100, "spiritUnreserved": 10})
                return out

        cpool = _ComboFakePool()
        rm = budget_planner.plan_roadmap(
            [],
            slot_steps,
            budget_divine=20.0,
            my_code=code,
            target_code=code,
            pool=cpool,
        )
        self.assertEqual(cpool.batch_lens, [3])
        self.assertIn("verifiedCombo", rm)
        self.assertEqual(rm["verifiedCombo"]["deltaDpsPct"], 42.0)
        self.assertEqual(rm["verifiedCombo"]["linearDeltaDpsPct"], 37.0)
        self.assertEqual(rm["verifiedCombo"]["dpsSynergyDiffPct"], 5.0)
        self.assertFalse(rm["verifiedCombo"]["allGreen"])
        self.assertTrue(any("火" in r.get("message", "") or "Fire" in r.get("message", "") for r in rm["verifiedCombo"]["redlines"]))
        p1 = next(p for p in rm["phases"] if p["phase"] == 1)
        self.assertTrue(p1["verifiedCombo"]["allGreen"])
        self.assertEqual(p1["verifiedCombo"]["deltaDpsPct"], 30.0)


if __name__ == "__main__":
    unittest.main()
