"""跨平台常驻 LuaJIT Worker 池（macOS / Windows / Linux）。

特性：
  1. 自动探测 LuaJIT（环境变量 / PATH / Homebrew / /usr/local）
  2. 跨平台内存检测（Linux 读 /proc，macOS 走 ps -o rss=），防止 PoB2 长驻缓存 OOM
  3. 弹性进程池：常驻 min_size 个，批量大时按需扩容到 size 个，空闲自动回收
  4. 返回完整的 45+ 项面板 JSON（含双武器精魂审计、四抗溢出、CI、三属性红线）
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Optional

from backend.config import get_env, pob2_root, pob2_src

WORKER_LUA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "persistent_worker.lua")


class WorkerError(RuntimeError):
    pass


def find_luajit() -> Optional[str]:
    candidates = [
        get_env("POB2_LUAJIT"),
        shutil.which("luajit"),
        "/opt/homebrew/bin/luajit",
        "/usr/local/bin/luajit",
        os.path.join(pob2_root(), "luajit.exe"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def _process_rss_mb(pid: int) -> float:
    """跨平台读取子进程 RSS（MB）。读取失败时返回 0.0。"""
    if sys.platform.startswith("linux"):
        try:
            with open(f"/proc/{pid}/status", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1]) / 1024.0
        except Exception:
            return 0.0
    elif sys.platform == "darwin":
        try:
            out = subprocess.check_output(
                ["ps", "-o", "rss=", "-p", str(pid)],
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=2,
            ).strip()
            if out:
                return int(out) / 1024.0
        except Exception:
            return 0.0
    return 0.0


class WorkerManager:
    """单个常驻 LuaJIT 进程 + stdin/stdout 管道（单进程内串行执行）。"""

    def __init__(
        self,
        timeout: float = 120.0,
        startup_timeout: float = 180.0,
        recycle_after: int = 30,
        max_rss_mb: float = 600.0,
    ):
        self.timeout = timeout
        self.startup_timeout = startup_timeout
        self.recycle_after = recycle_after
        self.max_rss_mb = max_rss_mb
        self.jobs_done = 0
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._seq = 0
        self._tmpdir = tempfile.mkdtemp(prefix="poe2_wb_worker_")
        self._stderr_path = os.path.join(self._tmpdir, "worker_stderr.log")
        self._stderr = None
        self._boot_ms: Optional[float] = None

    @property
    def boot_ms(self) -> Optional[float]:
        return self._boot_ms

    def rss_mb(self) -> float:
        if not self._proc or self._proc.poll() is not None:
            return 0.0
        return _process_rss_mb(self._proc.pid)

    def _needs_recycle(self) -> bool:
        if self.jobs_done >= self.recycle_after:
            return True
        return self.rss_mb() > self.max_rss_mb

    def start(self) -> None:
        if self._proc and self._proc.poll() is None:
            return
        luajit_bin = find_luajit()
        if not luajit_bin:
            raise WorkerError("未找到 luajit 可执行文件（Mac 请运行 brew install luajit，或设置 POB2_LUAJIT）")
        src_dir = pob2_src()
        if not os.path.isfile(os.path.join(src_dir, "HeadlessWrapper.lua")):
            raise WorkerError(f"找不到 HeadlessWrapper.lua：{src_dir}（请检查 .env.local 的 POB2_ROOT）")

        env = os.environ.copy()
        runtime_lua = os.path.join(pob2_root(), "runtime", "lua", "?.lua")
        runtime_init = os.path.join(pob2_root(), "runtime", "lua", "?", "init.lua")
        env["LUA_PATH"] = f"./?.lua;{runtime_lua};{runtime_init};;"

        t0 = time.time()
        self._stderr = open(self._stderr_path, "w", encoding="utf-8")
        self._proc = subprocess.Popen(
            [luajit_bin, WORKER_LUA],
            cwd=src_dir,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr,
            text=True,
            bufsize=1,
        )

        deadline = time.time() + self.startup_timeout
        boot_output: list[str] = []
        while time.time() < deadline:
            assert self._proc.stdout is not None
            line = self._proc.stdout.readline()
            if not line:
                err = self._tail_stderr()
                detail = " | ".join(boot_output[-5:])
                raise WorkerError(f"worker 启动失败：{detail or err}")
            stripped = line.strip()
            if stripped == "READY":
                self._boot_ms = (time.time() - t0) * 1000.0
                return
            if stripped.startswith("BOOT_ERR\t"):
                err_msg = stripped.split("\t", 1)[1]
                raise WorkerError(f"PoB2 初始化失败：{err_msg}")
            if stripped:
                boot_output.append(stripped)
        self.close()
        raise WorkerError(f"worker 启动超时（>{self.startup_timeout}s）")

    def _tail_stderr(self) -> str:
        try:
            if self._stderr:
                self._stderr.flush()
            with open(self._stderr_path, encoding="utf-8", errors="replace") as f:
                return f.read()[-800:].strip()
        except Exception:
            return ""

    def close(self) -> None:
        proc = self._proc
        self._proc = None
        if proc:
            try:
                if proc.poll() is None and proc.stdin:
                    proc.stdin.write("QUIT\n")
                    proc.stdin.flush()
                    proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            for pipe in (proc.stdin, proc.stdout):
                if pipe:
                    try:
                        pipe.close()
                    except Exception:
                        pass
        if self._stderr:
            try:
                self._stderr.close()
            except Exception:
                pass
            self._stderr = None

    def recycle(self) -> None:
        self.close()
        self.jobs_done = 0
        self.start()

    def ping(self) -> bool:
        with self._lock:
            if not self._proc or self._proc.poll() is not None:
                return False
            assert self._proc.stdin and self._proc.stdout
            self._proc.stdin.write("PING\n")
            self._proc.stdin.flush()
            return self._proc.stdout.readline().strip() == "PONG"

    def simulate(
        self,
        xml_text: str,
        lock_dps_key: Optional[str] = None,
        include_audit: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            if self._proc and self._proc.poll() is None and self._needs_recycle():
                self.close()
                self.jobs_done = 0
            if not self._proc or self._proc.poll() is not None:
                self.start()

            self._seq += 1
            job_id = str(self._seq)
            xml_path = os.path.join(self._tmpdir, f"job_{job_id}.xml")
            with open(xml_path, "w", encoding="utf-8") as f:
                f.write(xml_text)

            lock_arg = (lock_dps_key or "-").replace("\t", "").replace("\n", "")
            audit_arg = "1" if include_audit else "0"
            assert self._proc and self._proc.stdin and self._proc.stdout
            self._proc.stdin.write(f"SIM\t{job_id}\t{xml_path}\t{lock_arg}\t{audit_arg}\n")
            self._proc.stdin.flush()

            deadline = time.time() + self.timeout
            try:
                while time.time() < deadline:
                    line = self._proc.stdout.readline()
                    if not line:
                        raise WorkerError(f"worker 意外退出：{self._tail_stderr()}")
                    if line.startswith(f"RES_JSON\t{job_id}\t"):
                        payload = line.split("\t", 2)[2].strip()
                        self.jobs_done += 1
                        return json.loads(payload)
                    if line.startswith(f"ERR\t{job_id}\t"):
                        raise WorkerError(line.split("\t", 2)[2].strip())
                raise WorkerError(f"模拟超时（>{self.timeout}s）")
            finally:
                try:
                    os.remove(xml_path)
                except OSError:
                    pass

    def __enter__(self) -> "WorkerManager":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
        shutil.rmtree(self._tmpdir, ignore_errors=True)


class WorkerPool:
    """弹性常驻进程池：平时仅保活 min_size 个，大批量时扩容到 size 个，空闲后自动缩容/休眠。"""

    def __init__(
        self,
        size: int = 3,
        min_size: int = 1,
        scale_after: int = 6,
        items_per_worker: int = 5,
        timeout: float = 120.0,
        idle_timeout: float = 1800.0,
    ):
        self.size = max(1, int(size))
        self.min_size = max(0, min(int(min_size), self.size))
        self.scale_after = max(1, int(scale_after))
        self.items_per_worker = max(1, int(items_per_worker))
        self.timeout = timeout
        self.idle_timeout = max(0.0, float(idle_timeout))
        self._workers: list[WorkerManager] = []
        self._rr = 0
        self._lock = threading.RLock()
        self._busy = 0
        self.last_used = time.time()
        self.scales = 0
        self.idle_closed = 0
        self._stop_watchdog = threading.Event()
        self._watchdog: Optional[threading.Thread] = None

    @property
    def boot_ms(self) -> list[float]:
        with self._lock:
            return [round(w.boot_ms or 0.0, 1) for w in self._workers]

    @property
    def running_count(self) -> int:
        with self._lock:
            return len(self._workers)

    def start(self) -> None:
        with self._lock:
            if not self._workers:
                self._spawn_locked(max(1, self.min_size))
            self.last_used = time.time()
            self._ensure_watchdog()

    def _spawn_locked(self, target: int) -> None:
        need = target - len(self._workers)
        if need <= 0:
            return
        new_workers = [WorkerManager(timeout=self.timeout) for _ in range(need)]
        errors: list[BaseException] = []

        def boot(w: WorkerManager) -> None:
            try:
                w.start()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=boot, args=(w,), daemon=True) for w in new_workers]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        if errors:
            for w in new_workers:
                w.close()
            raise errors[0]
        self._workers.extend(new_workers)

    def _ensure_watchdog(self) -> None:
        if self.idle_timeout <= 0 or (self._watchdog and self._watchdog.is_alive()):
            return
        self._stop_watchdog.clear()

        def watch() -> None:
            while not self._stop_watchdog.wait(30.0):
                with self._lock:
                    if self._busy > 0 or not self._workers:
                        continue
                    idle = time.time() - self.last_used
                    if idle >= self.idle_timeout:
                        while len(self._workers) > self.min_size:
                            self._workers.pop().close()
                        if self.min_size == 0 and self._workers:
                            for w in self._workers:
                                w.close()
                            self._workers.clear()
                            self.idle_closed += 1

        self._watchdog = threading.Thread(target=watch, name="poe2-worker-watchdog", daemon=True)
        self._watchdog.start()

    def _ensure_capacity_for(self, n_items: int) -> None:
        with self._lock:
            if not self._workers:
                self._spawn_locked(max(1, self.min_size))
            if n_items >= self.scale_after and len(self._workers) < self.size:
                desired = min(self.size, max(len(self._workers), (n_items + self.items_per_worker - 1) // self.items_per_worker))
                if desired > len(self._workers):
                    self._spawn_locked(desired)
                    self.scales += 1

    def simulate(
        self,
        xml_text: str,
        lock_dps_key: Optional[str] = None,
        include_audit: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            if not self._workers:
                self._spawn_locked(max(1, self.min_size))
            w = self._workers[self._rr % len(self._workers)]
            self._rr += 1
            self._busy += 1
            self.last_used = time.time()
        try:
            return w.simulate(xml_text, lock_dps_key=lock_dps_key, include_audit=include_audit)
        finally:
            with self._lock:
                self._busy = max(0, self._busy - 1)
                self.last_used = time.time()

    def simulate_batch(
        self,
        xml_list: list[str],
        lock_dps_key: Optional[str] = None,
        include_audit: bool = False,
    ) -> list[dict[str, Any]]:
        if not xml_list:
            return []
        self._ensure_capacity_for(len(xml_list))
        with self._lock:
            workers = list(self._workers)
            self._busy += 1
            self.last_used = time.time()

        results: list[Optional[dict[str, Any]]] = [None] * len(xml_list)
        try:
            if len(workers) == 1 or len(xml_list) == 1:
                w = workers[0]
                for i, xml in enumerate(xml_list):
                    try:
                        results[i] = w.simulate(xml, lock_dps_key=lock_dps_key, include_audit=include_audit)
                    except WorkerError:
                        w.recycle()
                        results[i] = w.simulate(xml, lock_dps_key=lock_dps_key, include_audit=include_audit)
            else:
                buckets: list[list[tuple[int, str]]] = [[] for _ in workers]
                for i, xml in enumerate(xml_list):
                    buckets[i % len(workers)].append((i, xml))

                def run_bucket(w: WorkerManager, items: list[tuple[int, str]]) -> None:
                    for idx, xml in items:
                        try:
                            results[idx] = w.simulate(xml, lock_dps_key=lock_dps_key, include_audit=include_audit)
                        except WorkerError:
                            w.recycle()
                            results[idx] = w.simulate(xml, lock_dps_key=lock_dps_key, include_audit=include_audit)

                with concurrent.futures.ThreadPoolExecutor(max_workers=len(workers)) as ex:
                    futs = [ex.submit(run_bucket, w, b) for w, b in zip(workers, buckets) if b]
                    for f in futs:
                        f.result()
            return [r or {} for r in results]
        finally:
            with self._lock:
                self._busy = max(0, self._busy - 1)
                self.last_used = time.time()

    def close(self) -> None:
        self._stop_watchdog.set()
        with self._lock:
            for w in self._workers:
                w.close()
            self._workers.clear()
