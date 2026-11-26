"""圣物/附魔前缀修复：仅对旧版无 `Rune:` 声明的装备剥掉 `{enchant}{rune}` 前缀。

PoB2 0.4/0.5+ 导出 XML 中，装备文本已包含 `Rune: <符文名>` 元数据行，
PoB2 引擎会自动根据 `Rune:` 生成 `runeModLines` 并参与计算；
若再剥掉同装备里的 `{enchant}{rune}...` 前缀，会导致 PoB2 把每颗符文重复算两遍
（例如法杖 +3 法术技能被算成 +6，DPS 虚高 60%+）。
同时 `{enchant}Allocates ...` 涂油词条 PoB2 原生可解析，也不应剥离。
`Bonded:` 条件词条默认保留原样，避免虚高 DPS。
"""
from __future__ import annotations

import re

_PREFIX = re.compile(r"^(?:\{[A-Za-z_]+\})+")
_ITEM_BLOCK_RE = re.compile(r"(<Item\b[^>]*>)(.*?)(</Item>)", re.S)

_KNOWN_DEAD = (
    "chance for Spell Skills to fire",
)


def _normalize_item_body(body: str, include_bonded: bool = False) -> tuple[str, int]:
    """处理单个装备文本块；仅对无原生 `Rune:` 声明的旧格式 `{rune}` 行兜底剥离，永不篡改纯 `{enchant}`。"""
    lines = body.split("\n")
    has_native_runes = any(line.strip().startswith("Rune:") for line in lines)

    out: list[str] = []
    fixed = 0
    for line in lines:
        s = line.strip()
        if s.startswith("{") and "{rune}" in s:
            # 装备已有原生 Rune: 行时，PoB2 0.4/0.5 会自动按符文库生成 runeModLines，切勿重复剥离
            if has_native_runes:
                out.append(line)
                continue
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


def normalize_runes(xml: str, include_bonded: bool = False) -> tuple[str, int]:
    """返回 (修好的 XML, 剥掉前缀的行数)。"""
    if "<Item" not in xml:
        return _normalize_item_body(xml, include_bonded=include_bonded)

    total_fixed = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal total_fixed
        head, body, tail = match.group(1), match.group(2), match.group(3)
        new_body, count = _normalize_item_body(body, include_bonded=include_bonded)
        total_fixed += count
        return f"{head}{new_body}{tail}"

    return _ITEM_BLOCK_RE.sub(repl, xml), total_fixed


def rune_stats(xml: str) -> dict[str, int]:
    rune_lines = sum(
        1 for line in xml.split("\n")
        if line.strip().startswith("{") and ("{rune}" in line or "{enchant}" in line)
    )
    bonded = sum(1 for line in xml.split("\n") if "Bonded:" in line)
    return {"runeLines": rune_lines, "bonded": bonded}
