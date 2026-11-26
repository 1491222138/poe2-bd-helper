"""Stage 13 回归测试：Worker 非阻塞超时与临时目录清理、HTTP 服务配置、市集全局限流与 429 冷却锁。"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import unittest
from unittest import mock

from backend.api import server
from backend.engine.worker_pool import WorkerError, WorkerManager
from backend.market import market_pricer


class TestWorkerPoolHardening(unittest.TestCase):
    def test_nonblocking_timeout_and_tmpdir_cleanup(self) -> None:
        """验证子进程卡死无输出时能按 timeout 真正超时终止，且 close() 自动清理临时目录。"""
        wm = WorkerManager(timeout=0.4, startup_timeout=2.0)
        tmpdir = wm._ensure_tmpdir()
        self.assertTrue(os.path.isdir(tmpdir))

        # 拉起一个静默睡眠的子进程模拟 LuaJIT 卡死
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        wm._proc = proc
        wm._start_stdout_reader(proc)

        t0 = time.monotonic()
        with self.assertRaises(WorkerError) as ctx:
            wm.simulate("<PathOfBuilding2/>")
        elapsed = time.monotonic() - t0

        self.assertIn("模拟超时", str(ctx.exception))
        self.assertLess(elapsed, 3.0, "阻塞 readline 未按 timeout 及时返回")
        # 超时后应已自动关闭子进程并删除临时目录
        self.assertIsNotNone(proc.poll())
        self.assertFalse(os.path.exists(tmpdir), "close() 未清理 Worker 临时目录")

    def test_http_server_flags(self) -> None:
        self.assertTrue(server._WorkbenchHTTPServer.allow_reuse_address)
        self.assertTrue(server._WorkbenchHTTPServer.daemon_threads)


class TestTradeRateLimiterAndGuardrails(unittest.TestCase):
    def tearDown(self) -> None:
        market_pricer.TRADE_RATE_LIMITER.reset()

    def test_rate_limiter_spacing_and_429_cooldown(self) -> None:
        rl = market_pricer.TradeRateLimiter(min_interval=0.15, cooldown_sec=2.0)
        self.assertEqual(rl.wait_turn(), 0.0)
        t0 = time.monotonic()
        self.assertEqual(rl.wait_turn(), 0.0)
        self.assertGreaterEqual(time.monotonic() - t0, 0.12)

        locked = rl.note_rate_limited(retry_after=3.5)
        self.assertGreaterEqual(locked, 3.0)
        self.assertGreater(rl.remaining_cooldown(), 2.5)
        self.assertGreater(rl.wait_turn(), 2.5)

    def test_price_single_item_short_circuits_during_cooldown(self) -> None:
        market_pricer.TRADE_RATE_LIMITER.reset()
        market_pricer.TRADE_RATE_LIMITER.note_rate_limited(retry_after=30.0)
        item = {
            "slot": "Amulet",
            "name": "Test Amulet",
            "baseType": "Stellar Amulet",
            "rarity": "RARE",
            "mods": ["+50 to Spirit"],
        }
        with mock.patch.object(market_pricer, "_http_json_ex") as m_http:
            res = market_pricer.price_single_item_trade2(item, league="Standard", kind="budget")
        self.assertFalse(res["ok"])
        self.assertTrue(res["rateLimited"])
        m_http.assert_not_called()


if __name__ == "__main__":
    unittest.main()
