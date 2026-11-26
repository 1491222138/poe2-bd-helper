"""Stage 10 测试：BD 倒推灵敏度引擎、四段式攻防分离加权 Trade2 查询与 AI 调用 PoB2 沙盒闭环验收。"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
import urllib.parse

from backend.domain import llm_advisor, stat_sensitivity, trade_url
from backend.engine import pob_parser, runefix, xml_mutator
from backend.engine.worker_pool import WorkerPool

FIXTURE_POB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "sample.pob")


def _sample_code() -> str:
    with open(FIXTURE_POB, encoding="utf-8") as f:
        return f.read().strip()


class TestReverseSpecAndFourTierTrade(unittest.TestCase):
    def test_four_tier_trade_search_and_blueprint_enrichment(self) -> None:
        """验证四段式 Trade2 查询：面板底线 + AND硬绑定 + OR核心门槛 + 攻防分离双加权池。"""
        raw_items = [
            {
                "slot": "Weapon 1",
                "action": "buy_budget",
                "priority": 1,
                "itemName": "Spell Wand",
                "baseType": "Siphoning Wand",
                "rarity": "RARE",
                "budgetDivine": 4.0,
                "lockBase": False,
                "reason": "法系魔杖不死抄原件词缀：满足 +4 等级 OR 且额外伤/法伤/施速加权达标即可",
                "mandatoryAnyOf": [
                    {
                        "label": "+4 全法术或闪电法术等级",
                        "minCount": 1,
                        "affixes": [
                            {"text": "+4 to Level of all Spell Skills", "zh": "全法术等级", "min": 4},
                            {"text": "+4 to Level of all Lightning Spell Skills", "zh": "闪电法术等级", "min": 4},
                        ],
                    }
                ],
                "offensePool": [
                    {"text": "Gain 20% of Damage as Extra Lightning Damage", "zh": "额外电伤", "weight": 0.45},
                    {"text": "Gain 20% of Damage as Extra Cold Damage", "zh": "额外冰伤", "weight": 0.42},
                    {"text": "60% increased Spell Damage", "zh": "法术伤害%", "weight": 0.18},
                    {"text": "20% increased Cast Speed", "zh": "施法速度%", "weight": 0.40},
                ],
                "minOffenseScore": 28.0,
            },
            {
                "slot": "Weapon 1 Swap",
                "action": "buy_budget",
                "priority": 2,
                "itemName": "High DPS Bow",
                "baseType": "Gemini Bow",
                "rarity": "RARE",
                "lockBase": False,
                "reason": "弓只要大类相同且总 DPS ≥ 480、攻速 ≥ 1.55 即可",
                "equipmentFloors": {"dps": 480, "aps": 1.55},
                "offensePool": [
                    {"text": "+30% to Critical Damage Bonus", "zh": "暴伤", "weight": 0.35},
                    {"text": "+2 to Level of all Projectile Skills", "zh": "投射物等级", "weight": 9.5},
                ],
                "minOffenseScore": 12.0,
            },
        ]

        enriched = trade_url.enrich_copy_plan_items(raw_items, league="Standard")
        self.assertEqual(len(enriched), 2)

        wand = enriched[0]
        self.assertEqual(wand["searchMode"], "weighted_blueprint")
        self.assertIsNotNone(wand["searchBlueprint"])
        q_wand = json.loads(urllib.parse.unquote(wand["tradeUrl"].split("?q=", 1)[1]))["query"]
        # 不锁普通基底，按魔杖大类搜，确保技能不失效
        self.assertEqual(q_wand["filters"]["type_filters"]["filters"]["category"]["option"], "weapon.wand")
        self.assertNotIn("type", q_wand)
        stat_types = [g["type"] for g in q_wand["stats"]]
        self.assertIn("count", stat_types)
        self.assertIn("weight", stat_types)
        count_grp = next(g for g in q_wand["stats"] if g["type"] == "count")
        self.assertEqual(count_grp["value"]["min"], 1)
        self.assertEqual(len(count_grp["filters"]), 2)
        weight_grp = next(g for g in q_wand["stats"] if g["type"] == "weight")
        self.assertEqual(weight_grp["value"]["min"], 28.0)
        self.assertGreaterEqual(len(weight_grp["filters"]), 3)

        bow = enriched[1]
        q_bow = json.loads(urllib.parse.unquote(bow["tradeUrl"].split("?q=", 1)[1]))["query"]
        self.assertEqual(q_bow["filters"]["type_filters"]["filters"]["category"]["option"], "weapon.bow")
        self.assertEqual(q_bow["filters"]["equipment_filters"]["filters"]["dps"]["min"], 480)
        self.assertEqual(q_bow["filters"]["equipment_filters"]["filters"]["aps"]["min"], 1.55)

    def test_live_pob2_reverse_spec_and_ai_sandbox_tool_loop(self) -> None:
        """真实常驻 LuaJIT PoB2 引擎实测：灵敏度倒推 + 全槽位等价词池 + AI 多轮沙盒验收闭环。"""
        code = _sample_code()
        xml = pob_parser.decode_pob(code)
        norm_xml, _ = runefix.normalize_runes(xml)
        parsed = pob_parser.parse_pob_xml(norm_xml)

        pool = WorkerPool(size=4, min_size=2)
        try:
            base_sim = pool.simulate(norm_xml, include_audit=False)
            rev = stat_sensitivity.compute_reverse_build_spec(
                norm_xml,
                pool,
                base_sim=base_sim,
                parsed=parsed,
            )

            self.assertGreater(rev["probeRuns"], 10)
            self.assertGreater(len(rev["statSensitivity"]), 5)
            self.assertTrue(any(s["dpsPctPerUnit"] > 0 for s in rev["statSensitivity"]))
            self.assertGreater(len(rev["slotSpecs"]), 3)

            # 找一个稀有非暗金槽位，验证倒推出来的池子包含【原件没有但有效的等价词缀】并能生成加权 URL
            rare_item = next(
                (it for it in parsed["equippedItems"] if str(it.get("rarity") or "").upper() != "UNIQUE" and it.get("slot") in rev["slotSpecs"]),
                None,
            )
            self.assertIsNotNone(rare_item)
            slot_spec = rev["slotSpecs"][rare_item["slot"]]
            w_search = trade_url.build_weighted_substitute_search(
                rare_item,
                league="Standard",
                kind="budget",
                parsed_build=parsed,
                slot_spec=slot_spec,
            )
            self.assertTrue(w_search.get("reverseDriven"))
            self.assertIn("trade2/search/poe2/Standard?q=", w_search["tradeUrl"])

            # 验证 AI 调用 PoB2 沙盒工具（2 轮工具调用 + 终稿 JSON & Markdown）
            call_count = {"n": 0}
            target_slot = rare_item["slot"]
            target_base = rare_item.get("baseType") or "Ring"

            def fake_tool_post(_url, _headers, body, _timeout):
                call_count["n"] += 1
                if call_count["n"] == 1:
                    self.assertIn("tools", body)
                    return {
                        "model": "mock-tool-model",
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": "",
                                    "tool_calls": [
                                        {
                                            "id": "call_verify_1",
                                            "type": "function",
                                            "function": {
                                                "name": "pob_verify_copy_plan",
                                                "arguments": json.dumps(
                                                    {
                                                        "replacements": [
                                                            {
                                                                "slot": target_slot,
                                                                "baseType": target_base,
                                                                "rarity": "RARE",
                                                                "mods": [
                                                                    "+85 to maximum Life",
                                                                    "+35% to Fire Resistance",
                                                                    "+35% to Cold Resistance",
                                                                ],
                                                            }
                                                        ]
                                                    }
                                                ),
                                            },
                                        }
                                    ],
                                }
                            }
                        ],
                    }
                # 第 2 轮：AI 看到 PoB2 实测结果后输出最终四段式蓝图 JSON + Markdown
                self.assertTrue(any(m.get("role") == "tool" for m in body["messages"]))
                final_txt = (
                    "```json\n"
                    + json.dumps(
                        {
                            "budgetSummary": "PoB2 沙盒验收通过：按等价加权池搜平替即可达标。",
                            "shoppingList": [
                                {
                                    "slot": target_slot,
                                    "action": "buy_budget",
                                    "priority": 1,
                                    "itemName": f"平替 {target_base}",
                                    "baseType": target_base,
                                    "rarity": "RARE",
                                    "budgetDivine": 2.0,
                                    "lockBase": False,
                                    "reason": "采用倒推等价加权池，任意有效输出+抗性组合达标即可",
                                    "offenseTargetRatio": 0.7,
                                    "defenseTargetRatio": 0.6,
                                }
                            ],
                        },
                        ensure_ascii=False,
                    )
                    + "\n```\n---\n### 1. 💰 当前预算逐笔分配与跨部位资源统筹\n沙盒实测已通过。"
                )
                return {
                    "model": "mock-tool-model",
                    "choices": [{"message": {"role": "assistant", "content": final_txt}}],
                }

            cfg = {
                "enabled": True,
                "provider": "openai",
                "baseUrl": "https://api.example.com/v1",
                "apiKey": "sk-test",
                "model": "mock-tool-model",
                "temperature": 0.2,
            }
            profile = {
                "build": parsed,
                "positioning": {"archetype": "Test Build"},
                "simulation": base_sim,
                "reverseSpec": rev,
            }
            with tempfile.TemporaryDirectory() as td:
                res = llm_advisor.analyze_with_llm(
                    "copy",
                    profile=profile,
                    copy_context={"characterLevel": 90, "budgetDivine": 15},
                    league="Standard",
                    cfg=cfg,
                    state_dir=td,
                    pob_sandbox={"norm_xml": norm_xml, "base_sim": base_sim, "pool": pool},
                    http_post=fake_tool_post,
                )
            self.assertEqual(call_count["n"], 2)
            cp = res.get("copyPlan")
            self.assertIsNotNone(cp)
            self.assertIsInstance(cp.get("pobVerification"), dict)
            self.assertGreater(cp["pobVerification"]["dps"], 0)
            self.assertEqual(len(cp.get("toolTrace") or []), 1)
            self.assertEqual(cp["items"][0]["slot"], target_slot)
        finally:
            pool.close()


if __name__ == "__main__":
    unittest.main()
