#!/usr/bin/env python3
"""校验外部 PoB2 引擎资产（src/**/*.lua）是否与 pob2-engine.lock 一致。

用法：
    python3 backend/engine/check_engine.py
    python3 backend/engine/check_engine.py --update-lock
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from backend.config import REPO_ROOT, pob2_root  # noqa: E402

LOCK_FILE = os.path.join(REPO_ROOT, "pob2-engine.lock")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def scan(engine_root: str) -> dict:
    src = os.path.join(engine_root, "src")
    if not os.path.isdir(src):
        raise FileNotFoundError(f"找不到 PoB2 源码目录：{src}（请检查 .env.local 中的 POB2_ROOT）")
    core: dict[str, str] = {}
    data_hash = hashlib.sha256()
    data_count = 0
    data_bytes = 0
    for root, dirs, files in os.walk(src):
        dirs.sort()
        for name in sorted(files):
            if not name.endswith(".lua"):
                continue
            full = os.path.join(root, name)
            rel = os.path.relpath(full, engine_root).replace("\\", "/")
            if rel.startswith("src/Data/"):
                data_count += 1
                data_bytes += os.path.getsize(full)
                data_hash.update(rel.encode("utf-8"))
                data_hash.update(sha256_file(full).encode("utf-8"))
            else:
                core[rel] = sha256_file(full)
    core_hash = hashlib.sha256()
    for rel in sorted(core):
        core_hash.update(rel.encode("utf-8"))
        core_hash.update(core[rel].encode("utf-8"))
    return {
        "core": {"files": core, "hash": core_hash.hexdigest()},
        "data": {"count": data_count, "bytes": data_bytes, "hash": data_hash.hexdigest()},
    }


def verify(engine_root: str | None = None, lock_path: str = LOCK_FILE) -> tuple[bool, dict]:
    root = engine_root or pob2_root()
    if not os.path.isfile(lock_path):
        return False, {"error": f"缺少锁文件：{lock_path}"}
    with open(lock_path, encoding="utf-8") as f:
        lock = json.load(f)
    cur = scan(root)
    lock_files = (lock.get("core") or {}).get("files") or {}
    cur_files = cur["core"]["files"]
    changed = [k for k, v in cur_files.items() if lock_files.get(k) != v]
    removed = [k for k in lock_files if k not in cur_files]
    data_changed = cur["data"]["hash"] != (lock.get("data") or {}).get("hash")
    ok = (not changed) and (not removed) and (not data_changed)
    return ok, {
        "engineRoot": root,
        "coreCount": len(cur_files),
        "dataCount": cur["data"]["count"],
        "changed": changed,
        "removed": removed,
        "dataChanged": data_changed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="PoB2 引擎资产校验")
    parser.add_argument("--update-lock", action="store_true", help="重建 pob2-engine.lock")
    args = parser.parse_args()

    root = pob2_root()
    if args.update_lock:
        cur = scan(root)
        payload = {
            "engine": "PathOfBuilding-PoE2",
            "upstream": "https://github.com/PathOfBuildingCommunity/PathOfBuilding-PoE2",
            "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
            "scope": "src/**/*.lua（Data 只做聚合，其余逐文件）",
            "core": cur["core"],
            "data": cur["data"],
        }
        with open(LOCK_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        print(f"已更新锁文件：core={len(cur['core']['files'])} data={cur['data']['count']}")
        return 0

    ok, info = verify(root)
    if ok:
        print(f"引擎干净：core {info['coreCount']} 个 lua，data {info['dataCount']} 个，与 lock 一致")
        return 0
    print("引擎校验未通过：", json.dumps(info, ensure_ascii=False, indent=2))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
