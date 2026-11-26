#!/usr/bin/env python3
"""从本机 POB2_ROOT 一键导出紧凑离线版本词典（天赋节点 + 主动/辅助宝石原文与核心数值）。

用法：
    python3 scripts/export_patch_dict.py
    python3 scripts/export_patch_dict.py --tree-version 0_5
"""
from __future__ import annotations

import argparse
import gzip
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from backend.data.patch_dict_updater import (  # noqa: E402
    BACKEND_DICT_PATH,
    EXTENSION_DICT_PATH,
    write_patch_dict_files,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="导出 PoE2 紧凑离线版本词典（供扩展与后端共享）")
    parser.add_argument("--tree-version", default=None, help="指定 TreeData 版本（默认最新）")
    parser.add_argument("--backend-only", action="store_true", help="仅写入 backend/data/patch_dict.json")
    parser.add_argument("--extension-only", action="store_true", help="仅写入 extension/public/data/patch_dict.json")
    args = parser.parse_args()

    write_backend = not args.extension_only
    write_extension = not args.backend_only
    data = write_patch_dict_files(
        tree_version=args.tree_version,
        write_backend=write_backend,
        write_extension=write_extension,
    )

    sample_path = BACKEND_DICT_PATH if write_backend else EXTENSION_DICT_PATH
    raw_bytes = open(sample_path, "rb").read()
    gz_bytes = gzip.compress(raw_bytes)
    print(
        f"已导出 PoE2 词典 v{data['treeVersion']}："
        f"天赋节点 {data['nodeCount']} 个，技能/辅助宝石 {data['gemCount']} 个\n"
        f"体积：{len(raw_bytes)/1024:.1f} KB raw / {len(gz_bytes)/1024:.1f} KB gzip"
    )
    if write_backend:
        print(f"  -> {BACKEND_DICT_PATH}")
    if write_extension:
        print(f"  -> {EXTENSION_DICT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
