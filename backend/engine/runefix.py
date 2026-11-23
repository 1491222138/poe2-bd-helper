"""圣物/附魔前缀修复：把 `{enchant}{rune}...` 前缀剥掉，避免 XML 导入时词条被 PoB2 丢弃。

`Bonded:` 条件词条默认保留原样，避免虚高 DPS。
"""
from __future__ import annotations

import re

_PREFIX = re.compile(r"^(?:\{[A-Za-z_]+\})+")

_KNOWN_DEAD = (
    "chance for Spell Skills to fire",
)


def normalize_runes(xml: str, include_bonded: bool = False) -> tuple[str, int]:
    """返回 (修好的 XML, 剥掉前缀的行数)。"""
    out: list[str] = []
    fixed = 0
    for line in xml.split("\n"):
        s = line.strip()
        if s.startswith("{") and ("{rune}" in s or "{enchant}" in s):
            if ("Bonded:" in s or "Bonded " in s) and not include_bonded:
                out.append(line)
                continue
            stripped = _PREFIX.sub("", s)
            if stripped and not any(k in stripped for k in _KNOWN_DEAD):
                out.append(stripped)
                fixed += 1
                continue
        out.append(line)
    return "\n".join(out), fixed


def rune_stats(xml: str) -> dict[str, int]:
    rune_lines = sum(
        1 for line in xml.split("\n")
        if line.strip().startswith("{") and ("{rune}" in line or "{enchant}" in line)
    )
    bonded = sum(1 for line in xml.split("\n") if "Bonded:" in line)
    return {"runeLines": rune_lines, "bonded": bonded}
