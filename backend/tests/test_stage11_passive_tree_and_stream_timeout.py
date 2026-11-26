"""Stage 11 测试：抄作业交互式天赋树（几何布局 / 拓扑等级裁剪 / 双码差异 / PoB2 导出）与抄作业 AI 流式防超时闭环。"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

from backend.domain import llm_advisor
from backend.engine import pob_parser, tree_index

FIXTURE_POB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "sample.pob")


def _sample_code() -> str:
    with open(FIXTURE_POB, encoding="utf-8") as f:
        return f.read().strip()


class TestPassiveTreePlanAndStreamTimeout(unittest.TestCase):
    def test_tree_layout_and_level_pruning_connectivity(self) -> None:
        code = _sample_code()
        layout = tree_index.load_tree_layout()
        self.assertGreater(len(layout["nodes"]), 4000)
        self.assertGreater(len(layout["edges"]), 4500)
        self.assertIn("Mercenary", layout["classStarts"])

        plan = tree_index.build_passive_tree_plan(code, character_level=85)
        self.assertEqual(plan["targetLevel"], 100)
        self.assertEqual(plan["characterLevel"], 85)
        self.assertEqual(plan["missingPoints"], 15)
        self.assertEqual(plan["points"]["prunedMain"], 15)
        self.assertEqual(plan["points"]["keptMain"], plan["points"]["fullMain"] - 15)
        self.assertGreaterEqual(len(plan["anointedNodes"]), 1)
        self.assertGreaterEqual(len(plan["jewelsByNode"]), 1)

        # 验证裁剪后的主树节点与职业起点 100% 保持连通
        adj: dict[str, set[str]] = {}
        for a, b, _orb in layout["edges"]:
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)
        start_id = plan["classStartId"]
        self.assertIsNotNone(start_id)
        kept_graph = set(plan["keptMainIds"]) | {start_id}
        vis = {start_id}
        q = [start_id]
        while q:
            cur = q.pop(0)
            for nb in adj.get(cur, set()):
                if nb in kept_graph and nb not in vis:
                    vis.add(nb)
                    q.append(nb)
        self.assertEqual(vis, kept_graph, "裁剪后的主树节点必须全部与职业起点连通")

        # 验证裁剪后的 PoB2 码可完整解码，且作为 my_pob_code 对比时产生准确差异
        pruned_code = plan["prunedPobCode"]
        parsed_pruned = pob_parser.parse_pob_code(pruned_code)
        self.assertEqual(parsed_pruned["character"]["level"], 85)

        diff_plan = tree_index.build_passive_tree_plan(code, my_pob_code=pruned_code, character_level=100)
        self.assertIsNotNone(diff_plan["diff"])
        self.assertEqual(len(diff_plan["diff"]["toAddIds"]), len(plan["prunedIds"]))
        self.assertEqual(len(diff_plan["diff"]["toRefundIds"]), 0)

    def test_copy_stream_tool_loop_streams_directly_without_redundant_call(self) -> None:
        """验证 copy 模式流式工具环：模型第 1 轮直接流式输出答案时，不发第二次请求且实时产出 copyPlan 与 Markdown。"""
        parsed = pob_parser.parse_pob_code(_sample_code())
        profile = {
            "build": parsed,
            "positioning": {"archetype": "Test Build"},
            "simulation": {"dps": 100000, "ehp": 25000},
        }
        cfg = {
            "enabled": True,
            "provider": "openai",
            "baseUrl": "https://api.example.com/v1",
            "apiKey": "sk-test",
            "model": "mock-stream-model",
            "temperature": 0.2,
        }
        final_payload = (
            "```json\n"
            + json.dumps(
                {
                    "budgetSummary": "15D 流式直出方案",
                    "shoppingList": [
                        {
                            "slot": "Belt",
                            "action": "buy_budget",
                            "priority": 1,
                            "itemName": "Budget Belt",
                            "baseType": "Plate Belt",
                            "rarity": "RARE",
                            "budgetDivine": 2.0,
                            "reason": "补生命与抗性",
                            "selectedAffixes": [{"text": "+90 to maximum Life", "zh": "最大生命", "min": 80, "required": True}],
                        }
                    ],
                },
                ensure_ascii=False,
            )
            + "\n```\n---\n### 1. 💰 当前预算逐笔分配\n测试正文流式输出。"
        )

        def fake_stream_step(_cfg, _msgs, **_kwargs):
            yield {"type": "reasoning", "delta": "先看预算...", "model": "mock-stream-model"}
            half = len(final_payload) // 2
            yield {"type": "delta", "delta": final_payload[:half], "model": "mock-stream-model"}
            yield {"type": "delta", "delta": final_payload[half:], "model": "mock-stream-model"}
            yield {
                "type": "step_done",
                "model": "mock-stream-model",
                "text": final_payload,
                "reasoning": "先看预算...",
                "tool_calls": [],
            }

        dummy_sandbox = {"norm_xml": "<PathOfBuilding/>", "base_sim": {}, "pool": object()}
        with tempfile.TemporaryDirectory() as td:
            with (
                mock.patch.object(llm_advisor, "_stream_chat_step", side_effect=fake_stream_step) as m_step,
                mock.patch.object(llm_advisor, "stream_chat_completion", side_effect=AssertionError("不应触发二次请求")),
            ):
                events = list(
                    llm_advisor.stream_analyze_with_llm(
                        "copy",
                        profile=profile,
                        copy_context={"characterLevel": 90, "budgetDivine": 15},
                        league="Standard",
                        cfg=cfg,
                        state_dir=td,
                        use_cache=False,
                        pob_sandbox=dummy_sandbox,
                    )
                )
        self.assertEqual(m_step.call_count, 1)
        done_ev = next((e for e in events if e.get("type") == "done"), None)
        self.assertIsNotNone(done_ev)
        self.assertIn("测试正文流式输出", done_ev["text"])
        self.assertIsNotNone(done_ev.get("copyPlan"))
        self.assertEqual(len(done_ev["copyPlan"]["items"]), 1)

    def test_copy_stream_tool_loop_falls_back_cleanly_on_error(self) -> None:
        """验证工具调用报错时平滑回退到无工具流式直出，不卡死不丢输出。"""
        parsed = pob_parser.parse_pob_code(_sample_code())
        profile = {
            "build": parsed,
            "positioning": {"archetype": "Test Build"},
            "simulation": {"dps": 100000, "ehp": 25000},
        }
        cfg = {
            "enabled": True,
            "provider": "openai",
            "baseUrl": "https://api.example.com/v1",
            "apiKey": "sk-test",
            "model": "mock-fallback-model",
            "temperature": 0.2,
        }
        fallback_md = "### 1. 💰 当前预算分配\n回退流式成功。"

        def fake_fallback_stream(_cfg, _sys, _usr, **_kwargs):
            yield {"type": "delta", "delta": fallback_md, "model": "mock-fallback-model"}

        dummy_sandbox = {"norm_xml": "<PathOfBuilding/>", "base_sim": {}, "pool": object()}
        with tempfile.TemporaryDirectory() as td:
            with (
                mock.patch.object(llm_advisor, "_stream_chat_step", side_effect=RuntimeError("HTTP 400: tools not supported")),
                mock.patch.object(llm_advisor, "stream_chat_completion", side_effect=fake_fallback_stream) as m_fb,
            ):
                events = list(
                    llm_advisor.stream_analyze_with_llm(
                        "copy",
                        profile=profile,
                        copy_context={"characterLevel": 90, "budgetDivine": 15},
                        league="Standard",
                        cfg=cfg,
                        state_dir=td,
                        use_cache=False,
                        pob_sandbox=dummy_sandbox,
                    )
                )
        self.assertEqual(m_fb.call_count, 1)
        done_ev = next((e for e in events if e.get("type") == "done"), None)
        self.assertIsNotNone(done_ev)
        self.assertIn("回退流式成功", done_ev["text"])


if __name__ == "__main__":
    unittest.main()
