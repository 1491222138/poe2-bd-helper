"""Stage 2 后端测试：目标 BD 定位提取 + /builds/profile + 双向通道（SSE / open-trade）。"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from backend.api.server import WorkbenchRuntime, make_handler
from backend.domain import build_profile

FIXTURE_POB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "sample.pob")


def _sample_code() -> str:
    with open(FIXTURE_POB, encoding="utf-8") as f:
        return f.read().strip()


class TestBuildProfileExtraction(unittest.TestCase):
    def test_extract_profile_static_sample(self) -> None:
        prof = build_profile.extract_profile(_sample_code(), run_simulation=False)
        self.assertEqual(prof["source"], "backend")

        pos = prof["positioning"]
        self.assertTrue(pos["archetype"])
        self.assertTrue(pos["oneLineIdea"])
        self.assertIn(pos["budgetTier"], ("league-start", "mid", "high", "mirror"))
        self.assertGreater(len(pos["scenarioTags"]), 0)
        self.assertGreater(len(pos["coreAffixes"]), 0)
        self.assertGreater(len(pos["pitfalls"]), 0)
        self.assertIsInstance(pos["coreKeystones"], list)
        self.assertIsInstance(pos["coreAscendancy"], list)

        # 确认核心装备高亮和意图摘要完整
        self.assertGreater(len(prof["coreGearHighlights"]), 3)
        self.assertTrue(prof["intent"]["mainSkill"])
        self.assertIsNone(prof["simulation"])

    def test_weapon_set_classification_and_intent_suppression(self) -> None:
        fake_build = {
            "character": {
                "className": "Mercenary",
                "ascendancy": "Gemling Legionnaire",
                "level": 95,
                "mainSocketGroup": 1,
                "stats": {
                    "FireResist": 75,
                    "ColdResist": 75,
                    "LightningResist": 75,
                    "ChaosResist": 40,
                    "Spirit": 120,
                    "SpiritUnreserved": 10,
                    "Life": 2500,
                    "EnergyShield": 800,
                },
            },
            "passiveTree": {
                "treeVersion": "0_5",
                "totalAllocated": 100,
                "keystones": [{"id": "1", "name": "Dance with Death", "kind": "keystone", "stats": []}],
                "ascendancyNodes": [{"id": "2", "name": "Enhanced Effectiveness", "kind": "ascendancy", "stats": []}],
                "notables": [],
                "weaponSet1Notables": [],
                "weaponSet2Notables": [],
            },
            "weaponSets": {
                "hasDualWeaponSet": True,
                "set1": [{"slot": "Weapon 1", "rarity": "RARE", "name": "Fast Spear", "baseType": "Winged Spear", "mods": ["28% increased Attack Speed", "+12 to Dexterity"]}],
                "set2": [{"slot": "Weapon 1 Swap", "rarity": "RARE", "name": "Big Spear", "baseType": "Winged Spear", "mods": ["160% increased Physical Damage", "Adds 80 to 160 Lightning Damage", "+4.5% to Critical Hit Chance"]}],
            },
            "equippedItems": [],
            "jewels": [],
            "flasksAndCharms": [],
            "skillGroups": [
                {
                    "index": 1,
                    "isMain": True,
                    "slot": "Weapon 1 Swap",
                    "label": "",
                    "weaponSet": "2",
                    "gems": [{"name": "Twister", "skillId": "Twister", "level": 20, "quality": 20, "weaponSet": "2"}],
                },
                {
                    "index": 2,
                    "isMain": False,
                    "slot": "Weapon 1",
                    "label": "",
                    "weaponSet": "1",
                    "gems": [{"name": "Whirling Slash", "skillId": "WhirlingSlash", "level": 3, "quality": 20, "weaponSet": "1"}],
                },
            ],
            "notes": "",
        }
        intent = build_profile.extract_intent(fake_build)
        self.assertEqual(intent["weaponSetRoles"]["set1"]["role"], "speed")
        self.assertEqual(intent["weaponSetRoles"]["set2"]["role"], "damage")
        self.assertTrue(intent["hasTravelSkill"])

        checks = build_profile.run_health_checks(fake_build, intent)
        low_lvl = next(c for c in checks if c["ruleId"] == "skill.lowLevelHighQuality")
        self.assertIn("suppressedByIntent", low_lvl)
        speed_ws = next(c for c in checks if c["ruleId"] == "weapon.lowDamage")
        self.assertIn("suppressedByIntent", speed_ws)

        pos = build_profile.build_positioning(fake_build, intent, checks, [])
        self.assertIn("Dance with Death", pos["coreKeystones"])
        self.assertIn("双武器组分工", pos["scenarioTags"])
        self.assertTrue(any("与死共舞" in p for p in pos["pitfalls"]))

    def test_ci_eb_mom_oracle_anchor_keystones_and_mana_weights(self) -> None:
        oracle_build = {
            "character": {
                "className": "Druid",
                "ascendancy": "Oracle",
                "level": 100,
                "mainSocketGroup": 1,
                "stats": {
                    "CombinedDPS": 1_941_432,
                    "TotalEHP": 29_081,
                    "Life": 1,
                    "EnergyShield": 0,
                    "Mana": 10_115,
                    "ManaCost": 1133,
                    "ManaPerSecondCost": 5977,
                    "ManaRegenRecovery": 1519,
                    "FireResist": 75,
                    "ColdResist": 75,
                    "LightningResist": 75,
                    "ChaosResist": -7,
                    "PreEffectiveCritChance": 47.88,
                    "CritChance": 100,
                    "CritMultiplier": 4.02,
                    "Speed": 5.28,
                    "Spirit": 383,
                    "SpiritUnreserved": -89,
                    "EffectiveMovementSpeedMod": 2.0,
                },
            },
            "passiveTree": {
                "treeVersion": "0_5",
                "totalAllocated": 160,
                "keystones": [
                    {"id": "1", "name": "Resonance", "kind": "keystone", "stats": []},
                    {"id": "2", "name": "Conduit", "kind": "keystone", "stats": []},
                    {"id": "3", "name": "Mind Over Matter", "kind": "keystone", "stats": []},
                    {"id": "4", "name": "Chaos Inoculation", "kind": "keystone", "stats": []},
                    {"id": "5", "name": "Eldritch Battery", "kind": "keystone", "stats": []},
                    {"id": "6", "name": "Hollow Palm Technique", "kind": "keystone", "stats": []},
                ],
                "ascendancyNodes": [
                    {"id": "10", "name": "Entwined Realities", "kind": "ascendancy", "stats": []},
                    {"id": "11", "name": "Forced Outcome", "kind": "ascendancy", "stats": []},
                ],
                "notables": [],
                "weaponSet1Notables": [{"id": "20", "name": "Preservation", "kind": "notable", "stats": []}],
                "weaponSet2Notables": [{"id": "21", "name": "Near at Hand", "kind": "notable", "stats": []}],
            },
            "weaponSets": {
                "hasDualWeaponSet": True,
                "hasDualWeaponGear": False,
                "hasDualWeaponTree": True,
                "set1": [],
                "set2": [
                    {
                        "slot": "Weapon 2 Swap",
                        "rarity": "RARE",
                        "name": "Apocalypse Ram",
                        "baseType": "Stoic Sceptre",
                        "mods": ["58% increased Spirit", "+162 to maximum Mana", "64% increased Mana Regeneration Rate"],
                    }
                ],
            },
            "equippedItems": [
                {
                    "slot": "Weapon 2 Swap",
                    "rarity": "RARE",
                    "name": "Apocalypse Ram",
                    "baseType": "Stoic Sceptre",
                    "mods": ["58% increased Spirit", "+162 to maximum Mana", "64% increased Mana Regeneration Rate"],
                },
                {
                    "slot": "Ring 1",
                    "rarity": "RARE",
                    "name": "Fate Grip",
                    "baseType": "Mnemonic Ring",
                    "mods": ["+247 to maximum Mana", "8% increased maximum Mana", "36% increased Mana Cost Efficiency of Spells", "23% increased Cast Speed", "77% increased Mana Regeneration Rate"],
                },
            ],
            "jewels": [],
            "flasksAndCharms": [
                {"slot": "Charm 3", "rarity": "UNIQUE", "name": "Rite of Passage", "baseType": "Golden Charm", "mods": []},
                {"slot": "Flask 2", "rarity": "UNIQUE", "name": "Melting Maelstrom", "baseType": "Ultimate Mana Flask", "mods": []},
            ],
            "skillGroups": [
                {"index": 1, "isMain": True, "slot": "", "label": "", "weaponSet": "", "gems": [{"name": "Ember Fusillade", "level": 20, "quality": 20}]},
                {"index": 2, "isMain": False, "slot": "", "label": "", "weaponSet": "", "gems": [{"name": "Archmage", "level": 20, "quality": 20}]},
                {"index": 3, "isMain": False, "slot": "", "label": "", "weaponSet": "", "gems": [{"name": "Cast on Critical", "level": 20, "quality": 20}]},
                {"index": 4, "isMain": False, "slot": "", "label": "", "weaponSet": "", "gems": [{"name": "Blink", "level": 20, "quality": 20}]},
                {"index": 5, "isMain": False, "slot": "", "label": "", "weaponSet": "", "gems": [{"name": "Trinity", "level": 20, "quality": 20}]},
                {"index": 6, "isMain": False, "slot": "", "label": "", "weaponSet": "", "gems": [{"name": "Elemental Conflux", "level": 20, "quality": 20}]},
                {"index": 7, "isMain": False, "slot": "", "label": "", "weaponSet": "", "gems": [{"name": "Mana Remnants", "level": 20, "quality": 20}]},
            ],
            "notes": "",
        }
        intent = build_profile.extract_intent(oracle_build)
        checks = build_profile.run_health_checks(oracle_build, intent)
        gear = build_profile.extract_core_gear(oracle_build)
        pos = build_profile.build_positioning(oracle_build, intent, checks, gear)

        # 1. CI + EB + MoM 必须识别为纯蓝池，不能误标为「CI 纯护盾」
        self.assertIn("CI + EB + MoM 纯蓝池", pos["oneLineIdea"])
        self.assertNotIn("CI 纯护盾", pos["oneLineIdea"])
        self.assertIn("Chaos Inoculation", pos["oneLineIdea"])
        self.assertNotIn("Resonance", pos["oneLineIdea"])
        self.assertIn("单武器锁双组天赋", pos["scenarioTags"])

        # 2. 魔力与精魂词缀必须上榜，权杖与魔力戒权重正常
        self.assertIn("最大魔力 (Mana)", pos["coreAffixes"])
        sceptre = next(g for g in gear if g["name"] == "Apocalypse Ram")
        self.assertGreaterEqual(sceptre["importance"], 100)
        ring = next(g for g in gear if g["name"] == "Fate Grip")
        self.assertIn("+247 to maximum Mana", ring["keyMods"])

        # 3. 药剂/护符暗金纳入 coreUniques，且蓝耗与多开精魂均有准确预警
        self.assertIn("Rite of Passage", pos["coreUniques"])
        self.assertIn("Melting Maelstrom", pos["coreUniques"])
        self.assertTrue(any(c["ruleId"] == "mana.sustain" for c in checks))
        sp_check = next(c for c in checks if c["ruleId"] == "spirit.overflow")
        self.assertEqual(sp_check["severity"], "warn")


class TestStage2HttpBridge(unittest.TestCase):
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
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))

    def _get(self, path: str) -> dict:
        with urllib.request.urlopen(f"{self.base}{path}", timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))

    def test_profile_endpoint_and_auto_positioning_on_target(self) -> None:
        code = _sample_code()
        res = self._post("/builds/profile", {"pobCode": code, "runSimulation": True})
        self.assertTrue(res["ok"])
        prof = res["profile"]
        self.assertIsNotNone(prof["simulation"])
        self.assertGreater(prof["simulation"]["dps"], 1000)
        self.assertTrue(prof["positioning"]["oneLineIdea"])

        # 推送目标 BD 不传 positioning 时，后端自动补全 positioning
        pushed = self._post("/builds/target", {"pobCode": code, "league": "Dawn of the Hunt"})
        self.assertTrue(pushed["ok"])
        self.assertIsNotNone(pushed["target"]["positioning"])
        self.assertTrue(pushed["target"]["title"])

        # /health 应体现最新的目标 BD
        health = self._get("/health")
        self.assertEqual(health["targetBuildTitle"], pushed["target"]["title"])

    def test_bidirectional_trade_open_and_sse(self) -> None:
        received: list[str] = []
        ready = threading.Event()

        def listen_sse() -> None:
            with urllib.request.urlopen(f"{self.base}/events", timeout=5) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8").strip()
                    if line == "event: connected":
                        ready.set()
                    if line.startswith("event: trade_open_requested") or line.startswith("data:"):
                        received.append(line)
                    if any("tr_test_1" in x for x in received):
                        break

        t = threading.Thread(target=listen_sse, daemon=True)
        t.start()
        self.assertTrue(ready.wait(timeout=3.0), "SSE connected event timeout")

        open_res = self._post("/extension/open-trade", {
            "id": "tr_test_1",
            "league": "Dawn of the Hunt",
            "slot": "Amulet",
            "kind": "semi",
            "itemName": "Solar Amulet",
            "label": "搜项链半成品底子",
            "tradeUrl": "https://www.pathofexile.com/trade2/search/poe2/Dawn%20of%20the%20Hunt",
        })
        self.assertTrue(open_res["ok"])
        t.join(timeout=3.0)
        self.assertTrue(any("event: trade_open_requested" in x for x in received))
        self.assertTrue(any("tr_test_1" in x for x in received))

        pending = self._get("/extension/trade-requests")
        self.assertEqual(len(pending["requests"]), 1)
        self.assertEqual(pending["requests"][0]["id"], "tr_test_1")

        ack = self._post("/extension/trade-requests/ack", {"id": "tr_test_1"})
        self.assertTrue(ack["ok"])
        pending_after = self._get("/extension/trade-requests")
        self.assertEqual(len(pending_after["requests"]), 0)

    def test_chrome_extension_cors_and_token_flow(self) -> None:
        ext_origin = "chrome-extension://abcdefghijklmnop"
        # 1. 预检 OPTIONS
        opt_req = urllib.request.Request(f"{self.base}/builds/profile", headers={"Origin": ext_origin}, method="OPTIONS")
        with urllib.request.urlopen(opt_req, timeout=5) as r:
            self.assertEqual(r.status, 204)
            self.assertEqual(r.headers.get("Access-Control-Allow-Origin"), ext_origin)

        # 2. 白名单扩展拉取 /token
        tok_req = urllib.request.Request(f"{self.base}/token", headers={"Origin": ext_origin}, method="GET")
        with urllib.request.urlopen(tok_req, timeout=5) as r:
            tok_data = json.loads(r.read().decode("utf-8"))
            self.assertEqual(r.headers.get("Access-Control-Allow-Origin"), ext_origin)
            self.assertTrue(tok_data["ok"])
            tok = tok_data["token"]

        # 3. 携带令牌推送多档价格快照 (exact / budget / semi)
        price_req = urllib.request.Request(
            f"{self.base}/prices",
            data=json.dumps({
                "league": "Dawn of the Hunt",
                "source": "extension",
                "prices": {"Weapon 1": 12.0},
                "entries": [
                    {"slot": "Weapon 1", "kind": "exact", "divineValue": 12.0, "updatedAt": 1000},
                    {"slot": "Weapon 1", "kind": "budget", "divineValue": 4.5, "updatedAt": 1000},
                    {"slot": "Weapon 1", "kind": "semi", "divineValue": 1.8, "updatedAt": 1000},
                ],
            }).encode("utf-8"),
            headers={"Content-Type": "application/json", "Origin": ext_origin, "X-Auth-Token": tok},
            method="POST",
        )
        with urllib.request.urlopen(price_req, timeout=5) as r:
            resp = json.loads(r.read().decode("utf-8"))
            self.assertTrue(resp["ok"])
            self.assertEqual(len(resp["prices"]["entries"]), 3)


if __name__ == "__main__":
    unittest.main()
