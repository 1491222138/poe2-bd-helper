"""测试 Trade2 词缀解析、一键直达市集链接构造与 AI 造价抄作业结构化选词清单。"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

from backend.api import server
from backend.domain import llm_advisor, trade_url


class TestTradeUrlAndCopyPlan(unittest.TestCase):
    def test_resolve_affix_local_vs_global_and_zh(self) -> None:
        w_as = trade_url.resolve_affix_to_trade_stat("20% increased Attack Speed", slot="Weapon 1")
        g_as = trade_url.resolve_affix_to_trade_stat("12% increased Attack Speed", slot="Gloves")
        self.assertTrue(w_as["matched"])
        self.assertIn("(Local)", w_as["statText"] or "")
        self.assertTrue(g_as["matched"])
        self.assertNotIn("(Local)", g_as["statText"] or "")
        self.assertNotEqual(w_as["statId"], g_as["statId"])

        res_tot = trade_url.resolve_affix_to_trade_stat("", zh="总元素抗性", slot="Ring 1")
        self.assertTrue(res_tot["matched"])
        self.assertEqual(res_tot["statId"], "pseudo.pseudo_total_elemental_resistance")

        ms = trade_url.resolve_affix_to_trade_stat("", zh="移动速度", slot="Boots")
        self.assertTrue(ms["matched"])
        self.assertEqual(ms["statId"], "explicit.stat_2250533757")

    def test_build_trade_search_with_affixes_and_filters(self) -> None:
        built = trade_url.build_trade_search(
            league="Dawn of the Hunt",
            slot="Boots",
            item_name="Rare Boots",
            base_type="Secutor Greaves",
            rarity="RARE",
            action="buy_budget",
            lock_base=False,
            affixes=[
                {"text": "30% increased Movement Speed", "zh": "移动速度", "min": 25, "required": True},
                {"text": "+90 to maximum Life", "zh": "最大生命", "min": 75, "required": True},
                {"text": "+35% to Fire Resistance", "zh": "火焰抗性", "min": 30, "required": False},
            ],
            min_sockets=2,
            corrupted=False,
        )
        self.assertEqual(built["matchedStatCount"], 2)
        self.assertTrue(built["tradeUrl"].startswith("https://www.pathofexile.com/trade2/search/poe2/Dawn%20of%20the%20Hunt?q="))
        q_raw = urllib.parse.unquote(built["tradeUrl"].split("?q=", 1)[1])
        payload = json.loads(q_raw)
        q = payload["query"]
        self.assertEqual(q["filters"]["type_filters"]["filters"]["category"]["option"], "armour.boots")
        self.assertEqual(q["filters"]["equipment_filters"]["filters"]["total_augment_sockets"]["min"], 2)
        self.assertEqual(q["filters"]["misc_filters"]["filters"]["corrupted"]["option"], "false")
        stat_filters = q["stats"][0]["filters"]
        self.assertEqual(len(stat_filters), 2)
        self.assertEqual(stat_filters[0]["value"]["min"], 25.0)

    def test_copy_mode_extracts_structured_shopping_list_and_trade_urls(self) -> None:
        mock_content = (
            "```json\n"
            + json.dumps(
                {
                    "budgetSummary": "15D 起步：先买核心暗金，黄装鞋子和戒指保关键 2 词条压价。",
                    "shoppingList": [
                        {
                            "slot": "Boots",
                            "action": "buy_budget",
                            "priority": 1,
                            "itemName": "Rare Boots",
                            "baseType": "Wrapped Sandals",
                            "rarity": "RARE",
                            "budgetDivine": 2.0,
                            "lockBase": False,
                            "reason": "优先保 25%+ 移速与高血量，抗性随缘补齐",
                            "selectedAffixes": [
                                {"text": "25% increased Movement Speed", "zh": "移动速度", "min": 25, "required": True},
                                {"text": "+95 to maximum Life", "zh": "最大生命", "min": 80, "required": True},
                            ],
                            "cutAffixes": ["Rarity of Items found"],
                        }
                    ],
                },
                ensure_ascii=False,
            )
            + "\n```\n---\n### 1. 💰 当前预算逐笔分配清单\n优先把 2D 分配给移速血量鞋。"
        )

        def fake_post(_url, _headers, _body, _timeout):
            return {
                "model": "mock-model",
                "choices": [{"message": {"content": mock_content}}],
                "usage": {},
            }

        cfg = {
            "enabled": True,
            "provider": "openai",
            "baseUrl": "https://api.example.com/v1",
            "apiKey": "sk-test",
            "model": "mock-model",
            "temperature": 0.2,
        }
        profile = {
            "build": {
                "character": {"className": "Ranger", "ascendancy": "Deadeye", "level": 95, "stats": {}},
                "passiveTree": {
                    "treeVersion": "0_2",
                    "totalAllocated": 90,
                    "keystones": [],
                    "ascendancyNodes": [],
                    "notables": [],
                    "weaponSet1Notables": [],
                    "weaponSet2Notables": [],
                },
                "equippedItems": [],
                "jewels": [],
                "flasksAndCharms": [],
                "skillGroups": [],
                "config": {},
            },
            "positioning": {"archetype": "Deadeye Bow"},
        }
        with tempfile.TemporaryDirectory() as td:
            res = llm_advisor.analyze_with_llm(
                "copy",
                profile=profile,
                copy_context={"characterLevel": 88, "budgetDivine": 15},
                league="Standard",
                cfg=cfg,
                state_dir=td,
                http_post=fake_post,
            )
            self.assertIn("### 1.", res["text"])
            self.assertNotIn("```json", res["text"])
            cp = res.get("copyPlan")
            self.assertIsNotNone(cp)
            self.assertEqual(len(cp["items"]), 1)
            boots = cp["items"][0]
            self.assertEqual(boots["slot"], "Boots")
            self.assertEqual(boots["matchedStatCount"], 2)
            self.assertIn("trade2/search/poe2/Standard?q=", boots["tradeUrl"])
            self.assertTrue(all(a["matched"] and a["statId"] for a in boots["selectedAffixes"]))

    def test_http_trade_build_url_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            rt = server.WorkbenchRuntime(state_dir=td)
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(rt))
            port = httpd.server_address[1]
            th = threading.Thread(target=httpd.serve_forever, daemon=True)
            th.start()
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/trade/build-url",
                    data=json.dumps(
                        {
                            "league": "Standard",
                            "slot": "Amulet",
                            "itemName": "Rare Amulet",
                            "rarity": "RARE",
                            "action": "buy_budget",
                            "affixes": [
                                {"text": "+2 to Level of all Projectile Skills", "min": 2, "required": True},
                                {"text": "+40 to Spirit", "min": 35, "required": True},
                            ],
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json", "X-Auth-Token": rt.token},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data["ok"])
                self.assertEqual(data["matchedStatCount"], 2)
                self.assertIn("trade2/search/poe2/Standard?q=", data["tradeUrl"])
            finally:
                httpd.shutdown()
                httpd.server_close()
                rt.shutdown()

    def test_copy_plan_local_disk_store_and_http_endpoints(self) -> None:
        from backend.market.price_store import StateStore, pob_fingerprint

        self.assertEqual(pob_fingerprint("abc"), "ba7816bf8f01cfea")
        with tempfile.TemporaryDirectory() as td:
            rt = server.WorkbenchRuntime(state_dir=td)
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(rt))
            port = httpd.server_address[1]
            th = threading.Thread(target=httpd.serve_forever, daemon=True)
            th.start()
            try:
                def post_json(path: str, payload: dict) -> dict:
                    req = urllib.request.Request(
                        f"http://127.0.0.1:{port}{path}",
                        data=json.dumps(payload).encode("utf-8"),
                        headers={"Content-Type": "application/json", "X-Auth-Token": rt.token},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        return json.loads(resp.read().decode("utf-8"))

                saved_res = post_json(
                    "/copy-plans",
                    {
                        "mode": "copy",
                        "pobCode": "eNrtDemoBuildCode",
                        "buildTitle": "Deadeye Bow",
                        "league": "Standard",
                        "model": "mock-model",
                        "copyContext": {"characterLevel": 90, "budgetDivine": 15, "goalNote": "刷 T15"},
                        "text": "### 1. 预算分配",
                        "copyPlan": {
                            "budgetDivine": 15,
                            "characterLevel": 90,
                            "league": "Standard",
                            "items": [
                                {
                                    "slot": "Boots",
                                    "action": "buy_budget",
                                    "priority": 1,
                                    "itemName": "Rare Boots",
                                    "rarity": "RARE",
                                    "reason": "移速血量",
                                    "selectedAffixes": [],
                                }
                            ],
                        },
                    },
                )
                self.assertTrue(saved_res["ok"])
                plan = saved_res["plan"]
                plan_id = plan["id"]
                self.assertEqual(plan["pobHash"], pob_fingerprint("eNrtDemoBuildCode"))

                # 标记某件装备为已入手并修改搜索权重倍率
                patched_res = post_json(
                    "/copy-plans/item-state",
                    {
                        "planId": plan_id,
                        "itemKey": "Boots::Rare Boots",
                        "patch": {"purchased": True, "scoreScale": 0.8, "lockBase": True},
                    },
                )
                self.assertTrue(patched_res["ok"])
                self.assertTrue(patched_res["plan"]["itemStates"]["Boots::Rare Boots"]["purchased"])
                self.assertEqual(patched_res["plan"]["itemStates"]["Boots::Rare Boots"]["scoreScale"], 0.8)

                # GET /copy-plans 按 pobHash 查询
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/copy-plans?pobHash={plan['pobHash']}", timeout=5
                ) as resp:
                    listed = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(listed["ok"])
                self.assertEqual(len(listed["plans"]), 1)
                self.assertTrue(listed["plans"][0]["itemStates"]["Boots::Rare Boots"]["purchased"])

                # 模拟重启：全新 StateStore 从磁盘自动恢复
                reloaded = StateStore(state_dir=td)
                disk_plans = reloaded.list_copy_plans(pob_hash=plan["pobHash"])
                self.assertEqual(len(disk_plans), 1)
                self.assertTrue(disk_plans[0]["itemStates"]["Boots::Rare Boots"]["purchased"])

                # 删除记录
                del_res = post_json("/copy-plans/delete", {"id": plan_id})
                self.assertTrue(del_res["ok"])
                self.assertTrue(del_res["deleted"])
                self.assertEqual(len(StateStore(state_dir=td).list_copy_plans()), 0)
            finally:
                httpd.shutdown()
                httpd.server_close()
                rt.shutdown()


if __name__ == "__main__":
    unittest.main()
