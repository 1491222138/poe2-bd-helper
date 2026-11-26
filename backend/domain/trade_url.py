"""PoE2 官方市集（Trade2）词条解析与一键直达链接构造器。

支持：
  1. 将英文 PoB2 词条、英文模板或中文词缀描述精准映射到官方 Trade2 stat ID（基于 trade_stats_cache.json）；
  2. 按槽位智能区分 (local) 与全局词条（武器/护甲优先 local，饰品/珠宝优先全局），自动排除 rune/augment；
  3. 根据物品槽位、暗金/黄装、是否锁基底、孔位、腐化状态、物等与词条下限构造完整 Trade2 搜索 URL；
  4. 为 AI 造价抄作业输出的 shoppingList 批量补全每个词条的 statId 与一键直达市场 URL。
"""
from __future__ import annotations

import functools
import json
import math
import os
import re
import time
import urllib.parse
import urllib.request
from typing import Any

from backend.data.affix_updater import normalize_stat_template
from backend.domain import affix_analyzer
from backend.domain.crafting import affix_pool
from backend.market.price_store import STATE_DIR

_NUM_RE = re.compile(r"[+-]?\d+(?:\.\d+)?")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_TAG_SUFFIX_RE = re.compile(r"\s*\((implicit|crafted|fractured|rune|desecrated|mutated)\)\s*$", re.I)
_BRACE_RE = re.compile(r"\{[^}]*\}")
_RICH_PIPE_RE = re.compile(r"\[([^|\]]+)\|([^\]]+)\]")
_RICH_SINGLE_RE = re.compile(r"\[([^\]]+)\]")

_STAT_TYPE_PRIO: dict[str, int] = {
    "skill": 1,
    "explicit": 1,
    "desecrated": 2,
    "implicit": 3,
    "fractured": 4,
    "enchant": 5,
    "pseudo": 6,
}

# 整数型词缀：构造市集下限时必须向下取整为整数（避免诸如 +1.5 投射物等级 造成筛空）
_INT_STAT_RE = re.compile(
    r"to level of|to spirit\b|to maximum life|to maximum mana|to maximum energy shield|"
    r"to armour|to evasion|to accuracy|to strength|to dexterity|to intelligence|to all attributes|"
    r"resistance|additional (arrow|projectile)|charm slot|socket",
    re.I,
)

# 可互换的三元素单抗 & 全元素抗标准 statId（官方 Trade2 explicit 组，用于加权平替抗性互换池）
_ELE_RES_STAT_IDS: dict[str, tuple[str, str, float]] = {
    "fire": ("explicit.stat_3372524247", "火焰抗性", 1.0),
    "cold": ("explicit.stat_4220027924", "冰霜抗性", 1.0),
    "lightning": ("explicit.stat_1671376347", "闪电抗性", 1.0),
    "all_ele": ("explicit.stat_2901986750", "全元素抗性", 3.0),
}

_SKILL_STAT_CACHE_FILE = "trade_skill_stats.json"


@functools.lru_cache(maxsize=1)
def _load_trade_skill_stats() -> dict[str, dict[str, str]]:
    """加载官方 Trade2 `skill` 词条组映射（norm_skill_name -> {id, text}），带本地缓存与 slug 兜底。"""
    cpath = os.path.join(STATE_DIR, _SKILL_STAT_CACHE_FILE)
    if os.path.isfile(cpath):
        try:
            with open(cpath, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("byName"):
                return data["byName"]
        except Exception:
            pass

    by_name: dict[str, dict[str, str]] = {}
    try:
        req = urllib.request.Request(
            "https://www.pathofexile.com/api/trade2/data/stats",
            headers={"Accept": "application/json", "User-Agent": "PoE2Workbench/1.0"},
        )
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        for grp in raw.get("result") or []:
            if str(grp.get("id") or "").lower() != "skill":
                continue
            for e in grp.get("entries") or []:
                sid = str(e.get("id") or "")
                txt = str(e.get("text") or "")
                if not sid or not txt:
                    continue
                m = re.match(r"^Grants\s+Skill:\s*(?:Level\s+#\s+)?(.+)$", txt, re.I)
                sname = (m.group(1) if m else txt).strip().lower()
                sname = re.sub(r"\s+", " ", sname)
                by_name[sname] = {"id": sid, "text": txt}
        if by_name:
            os.makedirs(STATE_DIR, exist_ok=True)
            tmp = cpath + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"updatedAt": int(time.time() * 1000), "byName": by_name}, f, ensure_ascii=False)
            os.replace(tmp, cpath)
    except Exception:
        pass
    return by_name


def resolve_granted_skill_to_trade_stat(mod_text: str) -> dict[str, Any]:
    """将 'Grants Skill: Level 18 Eternal Rage' 解析为官方 Trade2 `skill.*` statId（如 skill.eternal_rage）。"""
    parsed = affix_analyzer.extract_granted_skill(mod_text)
    if not parsed:
        return {"matched": False, "statId": None, "statText": None, "statType": None, "zh": ""}
    skill_name, _ = parsed
    nk = re.sub(r"\s+", " ", skill_name.strip().lower())
    nk_no_minion = re.sub(r"\s+minion$", "", nk)
    table = _load_trade_skill_stats()
    hit = table.get(nk) or table.get(nk_no_minion)
    if hit:
        return {
            "matched": True,
            "statId": hit["id"],
            "statText": hit["text"],
            "statType": "skill",
            "zh": f"基底赋予技能：{skill_name}",
        }
    # 离线兜底：官方 Trade2 skill statId 规则为 skill.<snake_case>
    slug = re.sub(r"[^a-z0-9]+", "_", nk_no_minion).strip("_")
    if slug:
        return {
            "matched": True,
            "statId": f"skill.{slug}",
            "statText": f"Grants Skill: Level # {skill_name}",
            "statType": "skill",
            "zh": f"基底赋予技能：{skill_name}",
        }
    return {"matched": False, "statId": None, "statText": None, "statType": None, "zh": ""}

_EXCLUDED_STAT_TYPES = {"rune", "augment", "sanctum"}

_CLASS_TO_TRADE_CAT: dict[str, str] = {
    "Amulets": "accessory.amulet",
    "Rings": "accessory.ring",
    "Belts": "accessory.belt",
    "Body Armours": "armour.chest",
    "Helmets": "armour.helmet",
    "Gloves": "armour.gloves",
    "Boots": "armour.boots",
    "Quivers": "armour.quiver",
    "Shields": "armour.shield",
    "Bucklers": "armour.buckler",
    "Foci": "armour.focus",
    "Spears": "weapon.spear",
    "Bows": "weapon.bow",
    "Crossbows": "weapon.crossbow",
    "Wands": "weapon.wand",
    "Sceptres": "weapon.sceptre",
    "Staves": "weapon.staff",
    "Quarterstaves": "weapon.warstaff",
    "One Hand Swords": "weapon.onesword",
    "Two Hand Swords": "weapon.twosword",
    "One Hand Axes": "weapon.oneaxe",
    "Two Hand Axes": "weapon.twoaxe",
    "One Hand Maces": "weapon.onemace",
    "Two Hand Maces": "weapon.twomace",
    "Daggers": "weapon.dagger",
    "Claws": "weapon.claw",
    "Flails": "weapon.flail",
    "Talismans": "weapon.talisman",
    "Jewels": "jewel",
}

# 特殊机制基底：平替时也通常需要锁基底（含结界底子、潜能戒、自带精魂/光环/技能的项链、权杖、法杖、魔杖等）
_SPECIAL_BASE_RE = re.compile(
    r"unset ring|solar amulet|breach ring|stellar amulet|heavy belt|utility belt|ward|runeforged|"
    r"absent amulet|lament amulet|portent amulet|"
    r"shrine sceptre|stoic sceptre|omen sceptre|rattling sceptre|lupine sceptre|ochre sceptre|"
    r"chiming staff|dueling wand|siphoning wand|attuned wand|"
    r"keth|slipstrike|secutor|viper|gemini bow|warmonger",
    re.I,
)

# 常用中文口语简称 -> 标准 Trade2 英文规范化模板（兜底 AI 输出简短中文词条）
_ZH_SHORTCUT_TO_EN: tuple[tuple[tuple[str, ...], str], ...] = (
    (("总元素抗性", "元素总抗", "总抗"), "#% total elemental resistance"),
    (("总火焰抗性", "总火抗"), "#% total to fire resistance"),
    (("总冰霜抗性", "总冰抗"), "#% total to cold resistance"),
    (("总闪电抗性", "总电抗"), "#% total to lightning resistance"),
    (("总混沌抗性", "总混抗"), "#% total to chaos resistance"),
    (("火焰抗性", "火抗"), "#% to fire resistance"),
    (("冰霜抗性", "冰抗"), "#% to cold resistance"),
    (("闪电抗性", "电抗", "雷抗"), "#% to lightning resistance"),
    (("混沌抗性", "混抗"), "#% to chaos resistance"),
    (("所有元素抗性", "全元素抗性", "全抗"), "#% to all elemental resistances"),
    (("移动速度", "移速"), "#% increased movement speed"),
    (("最大生命", "生命上限", "血量", "生命"), "# to maximum life"),
    (("最大魔力", "魔力上限", "蓝量", "魔力"), "# to maximum mana"),
    (("最大能量护盾", "能量护盾上限", "能量护盾", "护盾", "es"), "# to maximum energy shield"),
    (("百分比能量护盾", "护盾%"), "#% increased energy shield"),
    (("闪避值", "闪避"), "# to evasion rating"),
    (("百分比闪避", "闪避%"), "#% increased evasion rating"),
    (("护甲值", "护甲"), "# to armour"),
    (("百分比护甲", "护甲%"), "#% increased armour"),
    (("攻击速度", "攻速"), "#% increased attack speed"),
    (("施法速度", "施速"), "#% increased cast speed"),
    (("暴击伤害", "暴伤"), "#% to critical damage bonus"),
    (("法术暴击率", "法术暴击"), "#% increased critical hit chance for spells"),
    (("暴击率", "暴击几率"), "#% increased critical hit chance"),
    (("精魂", "最大精魂"), "# to spirit"),
    (("物品稀有度", "稀有度", "打宝"), "#% increased rarity of items found"),
    (("全属性", "所有属性"), "# to all attributes"),
    (("力量",), "# to strength"),
    (("敏捷",), "# to dexterity"),
    (("智慧", "智力"), "# to intelligence"),
    (("魔力回复", "回蓝"), "#% increased mana regeneration rate"),
    (("物理伤害%", "百分比物伤", "提高物理伤害"), "#% increased physical damage"),
    (("附加物理伤害", "物理点伤"), "adds # to # physical damage"),
    (("附加闪电伤害", "闪电点伤"), "adds # to # lightning damage"),
    (("附加冰霜伤害", "冰霜点伤"), "adds # to # cold damage"),
    (("附加火焰伤害", "火焰点伤"), "adds # to # fire damage"),
    (("攻击元素伤害", "武器元素伤害"), "#% increased elemental damage with attacks"),
    (("法术伤害",), "#% increased spell damage"),
    (("所有投射物技能等级", "投射物技能等级", "投射物等级"), "# to level of all projectile skills"),
    (("所有近战技能等级", "近战技能等级", "近战等级"), "# to level of all melee skills"),
    (("所有法术技能等级", "法术技能等级", "法术等级"), "# to level of all spell skills"),
    (("所有闪电法术技能等级", "闪电技能等级"), "# to level of all lightning spell skills"),
    (("所有火焰法术技能等级", "火焰技能等级"), "# to level of all fire spell skills"),
    (("所有冰霜法术技能等级", "冰霜技能等级"), "# to level of all cold spell skills"),
    (("所有召唤物技能等级", "召唤技能等级"), "# to level of all minion skills"),
    (("所有技能等级", "全技能等级"), "# to level of all skills"),
    (("投射物速度", "投速"), "#% increased projectile speed"),
    (("命中值", "命中"), "# to accuracy rating"),
)


def _clean_mod_text(text: str) -> str:
    s = str(text or "")
    s = _RICH_PIPE_RE.sub(r"\2", s)
    s = _RICH_SINGLE_RE.sub(r"\1", s)
    s = _BRACE_RE.sub("", s)
    s = _TAG_SUFFIX_RE.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


def _is_weapon_slot(slot: str) -> bool:
    s = (slot or "").lower()
    return bool(re.search(r"weapon|spear|bow|crossbow|wand|staff|sceptre|mace|axe|sword|dagger|claw|flail|talisman", s))


def _is_armour_slot(slot: str) -> bool:
    s = (slot or "").lower()
    return bool(re.search(r"helmet|body|armour|chest|gloves|boots|shield|buckler|focus", s))


def _prefer_local_for_mod(slot: str, mod_text: str) -> bool:
    """按槽位与词条性质精准判断 Trade2 是否优先匹配 (Local) 词条。"""
    t = (mod_text or "").lower()
    if _is_weapon_slot(slot):
        return bool(
            re.search(
                r"attack speed|physical damage|fire damage|cold damage|lightning damage|chaos damage|"
                r"critical (?:hit|strike) chance|accuracy|攻速|攻击速度|物理伤害|点伤|暴击率|暴击几率|命中",
                t,
            )
        )
    if _is_armour_slot(slot):
        return bool(
            re.search(
                r"armour|evasion|energy shield|block chance|护甲|闪避|能量护盾|护盾|格挡",
                t,
            )
        )
    return False


def _zh_compact(text: str) -> str:
    s = normalize_stat_template(text)
    return re.sub(r"[\s+\-#%：:,，。()（）]+", "", s)


def _extract_numeric_value(text: str) -> float | None:
    cleaned = _clean_mod_text(text)
    nums = [float(x) for x in _NUM_RE.findall(cleaned)]
    if not nums:
        return None
    if len(nums) == 1:
        val = nums[0]
        return round(val, 1) if val != int(val) else int(val)
    avg = (nums[0] + nums[1]) / 2.0
    return round(avg, 1)


@functools.lru_cache(maxsize=1)
def _build_resolver_index() -> dict[str, Any]:
    raw = affix_pool.load_trade_stats()
    entries = raw.get("entries") or []

    by_id: dict[str, dict[str, Any]] = {}
    by_en_norm: dict[str, list[dict[str, Any]]] = {}
    by_en_no_local: dict[str, list[dict[str, Any]]] = {}
    by_zh_norm: dict[str, list[dict[str, Any]]] = {}
    by_zh_compact: dict[str, list[dict[str, Any]]] = {}
    valid_entries: list[dict[str, Any]] = []

    for e in entries:
        if not isinstance(e, dict) or not e.get("id"):
            continue
        etype = str(e.get("type") or "").lower()
        if etype in _EXCLUDED_STAT_TYPES:
            continue
        en = str(e.get("en") or "")
        zh = str(e.get("zh") or "")
        is_local = "(local)" in en.lower()
        en_no_local = re.sub(r"\s*\(local\)\s*", " ", en, flags=re.I).strip()
        en_norm = normalize_stat_template(en)
        en_nl_norm = normalize_stat_template(en_no_local)
        zh_norm = normalize_stat_template(zh) if zh else ""
        zh_comp = _zh_compact(zh) if zh else ""

        rec = {
            "id": str(e["id"]),
            "type": etype,
            "en": en,
            "zh": zh,
            "isLocal": is_local,
            "prio": _STAT_TYPE_PRIO.get(etype, 8),
            "enNorm": en_norm,
            "enNoLocalNorm": en_nl_norm,
            "zhNorm": zh_norm,
            "zhCompact": zh_comp,
        }
        by_id[rec["id"]] = rec
        valid_entries.append(rec)
        if en_norm:
            by_en_norm.setdefault(en_norm, []).append(rec)
        if en_nl_norm:
            by_en_no_local.setdefault(en_nl_norm, []).append(rec)
        if zh_norm:
            by_zh_norm.setdefault(zh_norm, []).append(rec)
        if zh_comp:
            by_zh_compact.setdefault(zh_comp, []).append(rec)

    return {
        "by_id": by_id,
        "by_en_norm": by_en_norm,
        "by_en_no_local": by_en_no_local,
        "by_zh_norm": by_zh_norm,
        "by_zh_compact": by_zh_compact,
        "entries": valid_entries,
    }


def _pick_best(cands: list[dict[str, Any]], *, prefer_local: bool, prefer_pseudo: bool = False) -> dict[str, Any] | None:
    if not cands:
        return None

    def key(c: dict[str, Any]) -> tuple[int, int, int, int]:
        if prefer_pseudo and c["type"] == "pseudo":
            type_rank = 0
        else:
            type_rank = c["prio"]
        local_rank = (0 if c["isLocal"] else 1) if prefer_local else (1 if c["isLocal"] else 0)
        zh_rank = 0 if c["zh"] else 1
        return (local_rank, type_rank, zh_rank, len(c["en"]))

    return sorted(cands, key=key)[0]


def resolve_affix_to_trade_stat(
    text: str,
    *,
    zh: str = "",
    slot: str = "",
    base_type: str = "",
) -> dict[str, Any]:
    """将单条英文或中文词缀文本解析为 PoE2 官方 Trade2 stat 信息（含基底自带技能 skill.*）。"""
    if text and re.match(r"^\s*grants\s+skill:", _clean_mod_text(text), re.I):
        sk_res = resolve_granted_skill_to_trade_stat(text)
        if sk_res.get("matched"):
            return sk_res
    idx = _build_resolver_index()
    raw_text = _clean_mod_text(text)
    raw_zh = _clean_mod_text(zh)
    if not raw_text and not raw_zh:
        return {"matched": False, "statId": None, "statText": None, "statType": None, "zh": ""}
    prefer_local = _prefer_local_for_mod(slot or base_type, f"{raw_text} {raw_zh}")

    # 1. 若给了英文文本，优先用官方英文规范化 + 变体查表
    if raw_text and not _CJK_RE.search(raw_text):
        prefer_pseudo = "total" in raw_text.lower()
        variants = affix_pool._en_text_variants(raw_text)
        # 额外处理常见 PoB 写法差异：Adds X to Y ... Damage to Attacks -> Adds # to # ... Damage to Attacks
        for v in list(variants):
            no_percent = v.replace("%", "")
            if no_percent != v and no_percent not in variants:
                variants.append(no_percent)
        for v in variants:
            hits = idx["by_en_no_local"].get(v) or idx["by_en_norm"].get(v)
            if hits:
                best = _pick_best(hits, prefer_local=prefer_local, prefer_pseudo=prefer_pseudo)
                if best:
                    return {
                        "matched": True,
                        "statId": best["id"],
                        "statText": best["en"],
                        "statType": best["type"],
                        "zh": raw_zh or best["zh"],
                    }

        # 回退：部分 AI 会写略简英文如 "+30% Fire Resistance" 或 "Maximum Life"
        simple = normalize_stat_template(raw_text)
        tokens = [tok for tok in re.split(r"[^a-z0-9]+", simple) if len(tok) >= 2 and tok not in ("to", "of", "the", "and", "with", "for")]
        if tokens:
            best_fuzzy: tuple[float, dict[str, Any]] | None = None
            tset = set(tokens)
            for rec in idx["entries"]:
                r_tokens = [tok for tok in re.split(r"[^a-z0-9]+", rec["enNoLocalNorm"]) if len(tok) >= 2 and tok not in ("to", "of", "the", "and", "with", "for")]
                if not r_tokens:
                    continue
                rset = set(r_tokens)
                inter = len(tset & rset)
                if inter == 0:
                    continue
                score = inter / len(tset | rset)
                if tset.issubset(rset):
                    score += 0.15 * (len(tset) / len(rset))
                if score >= 0.72:
                    if best_fuzzy is None or score > best_fuzzy[0] or (
                        abs(score - best_fuzzy[0]) < 1e-6 and rec["prio"] < best_fuzzy[1]["prio"]
                    ):
                        best_fuzzy = (score, rec)
            if best_fuzzy is not None:
                best = best_fuzzy[1]
                return {
                    "matched": True,
                    "statId": best["id"],
                    "statText": best["en"],
                    "statType": best["type"],
                    "zh": raw_zh or best["zh"],
                }

    # 2. 中文文本匹配（来自 zh 字段或 text 本身含中文）
    zh_query = raw_zh or (raw_text if _CJK_RE.search(raw_text) else "")
    if zh_query:
        zc = _zh_compact(zh_query)
        zn = normalize_stat_template(zh_query)

        # 2a. 中文常用简称优先命中标准英文模板（避开官方汉化表中重复/歧义条目如 pseudo_count_elemental_resistances）
        for aliases, en_target in _ZH_SHORTCUT_TO_EN:
            if any(al == zc or (len(al) >= 3 and al in zc) for al in aliases):
                hits = idx["by_en_no_local"].get(en_target) or idx["by_en_norm"].get(en_target)
                if hits:
                    best = _pick_best(hits, prefer_local=prefer_local, prefer_pseudo=("total" in en_target))
                    if best:
                        return {
                            "matched": True,
                            "statId": best["id"],
                            "statText": best["en"],
                            "statType": best["type"],
                            "zh": raw_zh or best["zh"],
                        }

        # 2b. 精确中文模板命中
        hits = idx["by_zh_norm"].get(zn) or idx["by_zh_compact"].get(zc)
        if hits:
            best = _pick_best(hits, prefer_local=prefer_local, prefer_pseudo=("总" in zh_query))
            if best:
                return {
                    "matched": True,
                    "statId": best["id"],
                    "statText": best["en"],
                    "statType": best["type"],
                    "zh": raw_zh or best["zh"],
                }

        # 2c. 兜底短词简称（2字简称如 生命/魔力/力量/敏捷）
        for aliases, en_target in _ZH_SHORTCUT_TO_EN:
            if any(al in zc for al in aliases):
                hits = idx["by_en_no_local"].get(en_target) or idx["by_en_norm"].get(en_target)
                if hits:
                    best = _pick_best(hits, prefer_local=prefer_local, prefer_pseudo=("total" in en_target))
                    if best:
                        return {
                            "matched": True,
                            "statId": best["id"],
                            "statText": best["en"],
                            "statType": best["type"],
                            "zh": raw_zh or best["zh"],
                        }

        # 2c. 模糊中文子串匹配
        if len(zc) >= 2:
            q_chars = set(zc)
            scored: list[tuple[float, dict[str, Any]]] = []
            for rec in idx["entries"]:
                ec = rec["zhCompact"]
                if not ec:
                    continue
                if zc in ec or ec in zc:
                    ratio = min(len(zc), len(ec)) / max(len(zc), len(ec))
                    if ratio >= 0.55:
                        scored.append((0.75 + 0.2 * ratio, rec))
                        continue
                inter = len(q_chars & set(ec))
                jacc = inter / max(1, len(q_chars | set(ec)))
                if jacc >= 0.65:
                    scored.append((jacc * 0.8, rec))
            if scored:
                scored.sort(
                    key=lambda x: (
                        -x[0],
                        (0 if x[1]["isLocal"] else 1) if prefer_local else (1 if x[1]["isLocal"] else 0),
                        x[1]["prio"],
                    )
                )
                best = scored[0][1]
                return {
                    "matched": True,
                    "statId": best["id"],
                    "statText": best["en"],
                    "statType": best["type"],
                    "zh": raw_zh or best["zh"],
                }

    # 3. 兜底通过槽位词缀池 match_affix_to_pool
    if slot:
        try:
            pool = affix_pool.build_slot_pool(slot, ilvl=82, base_type=base_type)
            m = affix_pool.match_affix_to_pool(raw_text or zh_query, pool)
            if m.get("matched") and m.get("tradeStatId"):
                sid = str(m["tradeStatId"])
                rec = idx["by_id"].get(sid)
                return {
                    "matched": True,
                    "statId": sid,
                    "statText": rec["en"] if rec else str(m.get("sampleText") or ""),
                    "statType": rec["type"] if rec else "explicit",
                    "zh": raw_zh or str(m.get("zhText") or ""),
                }
        except Exception:
            pass

    return {"matched": False, "statId": None, "statText": None, "statType": None, "zh": raw_zh}


def infer_trade_category(slot: str, base_type: str = "") -> str | None:
    s = (slot or "").lower()
    b = (base_type or "").lower()
    combined = f"{s} {b}"
    if "jewel" in combined or any(k in b for k in ("diamond", "emerald", "ruby", "sapphire", "time-lost")):
        return "jewel"
    if "flask" in s:
        return "flask"
    if "charm" in s:
        return "charm"
    try:
        cls = affix_pool.resolve_item_class(slot, base_type)
        if cls in _CLASS_TO_TRADE_CAT:
            return _CLASS_TO_TRADE_CAT[cls]
    except Exception:
        pass
    if "helmet" in combined:
        return "armour.helmet"
    if "body" in combined or "chest" in combined:
        return "armour.chest"
    if "glove" in combined:
        return "armour.gloves"
    if "boot" in combined:
        return "armour.boots"
    if "amulet" in combined:
        return "accessory.amulet"
    if "ring" in combined:
        return "accessory.ring"
    if "belt" in combined:
        return "accessory.belt"
    if "quiver" in combined:
        return "armour.quiver"
    if "focus" in combined:
        return "armour.focus"
    if "shield" in combined:
        return "armour.shield"
    if "spear" in combined:
        return "weapon.spear"
    if "crossbow" in combined:
        return "weapon.crossbow"
    if "bow" in combined:
        return "weapon.bow"
    if "quarterstaff" in combined or "warstaff" in combined:
        return "weapon.warstaff"
    if "staff" in combined:
        return "weapon.staff"
    if "wand" in combined:
        return "weapon.wand"
    if "sceptre" in combined:
        return "weapon.sceptre"
    return None


def build_trade_search(
    *,
    league: str = "Standard",
    slot: str = "",
    item_name: str = "",
    base_type: str = "",
    rarity: str = "RARE",
    action: str = "buy_budget",
    lock_base: bool | None = None,
    affixes: list[dict[str, Any]] | None = None,
    mandatory_stats: list[dict[str, Any]] | None = None,
    mandatory_any_of: list[dict[str, Any]] | None = None,
    weighted_stats: list[dict[str, Any]] | None = None,
    min_weight_sum: float | None = None,
    weighted_groups: list[dict[str, Any]] | None = None,
    equipment_floors: dict[str, float] | None = None,
    min_ilvl: int | None = None,
    min_sockets: int | None = None,
    corrupted: bool | None = None,
    max_divine: float | None = None,
    status_option: str = "available",
) -> dict[str, Any]:
    """根据槽位、物品信息、设备底线、硬绑定、OR 门槛组、多组加权池构造官方 PoE2 Trade2 查询 JSON 与一键直达 URL。"""
    enc_league = urllib.parse.quote(league or "Standard")
    rarity_up = str(rarity or "RARE").upper()
    is_unique = rarity_up == "UNIQUE" or action == "buy_unique"

    # 解析所有传入词条（保留未匹配的供前端展示，写入已匹配的 statId 到 query）
    resolved_affixes: list[dict[str, Any]] = []
    stat_filters: list[dict[str, Any]] = []
    seen_stat_ids: set[str] = set()

    for raw in affixes or []:
        if isinstance(raw, str):
            raw = {"text": raw, "required": True}
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or raw.get("line") or "").strip()
        zh = str(raw.get("zh") or "").strip()
        required = raw.get("required", True) is not False
        min_val = raw.get("min")
        max_val = raw.get("max")
        if min_val is None and text:
            extracted = _extract_numeric_value(text)
            if extracted is not None and extracted > 0:
                min_val = extracted
        if isinstance(min_val, (int, float)) and not isinstance(min_val, bool) and _INT_STAT_RE.search(text):
            min_val = max(1, int(math.floor(float(min_val))))

        existing_sid = str(raw.get("statId") or "").strip()
        if existing_sid:
            res = {
                "matched": True,
                "statId": existing_sid,
                "statText": str(raw.get("statText") or ""),
                "statType": str(raw.get("statType") or "explicit"),
                "zh": zh,
            }
        else:
            res = resolve_affix_to_trade_stat(text, zh=zh, slot=slot, base_type=base_type)

        entry = {
            "text": text or res.get("statText") or zh,
            "zh": res.get("zh") or zh,
            "min": min_val,
            "max": max_val,
            "required": required,
            "statId": res.get("statId"),
            "statText": res.get("statText"),
            "statType": res.get("statType"),
            "matched": bool(res.get("matched")),
        }
        resolved_affixes.append(entry)

        if action != "buy_base" and required and entry["matched"] and entry["statId"] and entry["statId"] not in seen_stat_ids:
            seen_stat_ids.add(str(entry["statId"]))
            f_obj: dict[str, Any] = {"id": entry["statId"], "disabled": False}
            val_obj: dict[str, float] = {}
            if isinstance(min_val, (int, float)) and not isinstance(min_val, bool):
                val_obj["min"] = int(min_val) if float(min_val).is_integer() else float(min_val)
            if isinstance(max_val, (int, float)) and not isinstance(max_val, bool):
                val_obj["max"] = int(max_val) if float(max_val).is_integer() else float(max_val)
            if val_obj:
                f_obj["value"] = val_obj
            stat_filters.append(f_obj)

    # PoE2 官方市集默认 status 为 "available"（同时覆盖市集直购 Instant Buyout 与在线交易）
    query: dict[str, Any] = {"status": {"option": status_option or "available"}}
    filters: dict[str, Any] = {}

    if is_unique and item_name and action != "buy_base":
        query["name"] = item_name
        if base_type and base_type.lower() != item_name.lower() and lock_base:
            query["type"] = base_type
    else:
        should_lock_base = (
            lock_base
            if lock_base is not None
            else (
                action in ("buy_base", "craft_semi")
                or bool(base_type and _SPECIAL_BASE_RE.search(base_type))
            )
        )
        if should_lock_base and base_type:
            query["type"] = base_type
        else:
            cat = infer_trade_category(slot, base_type)
            if cat:
                filters.setdefault("type_filters", {"filters": {}})["filters"]["category"] = {"option": cat}
            elif base_type:
                query["type"] = base_type

    if action == "buy_base" and not is_unique:
        tf = filters.setdefault("type_filters", {"filters": {}})["filters"]
        tf["rarity"] = {"option": "normal"}
        if min_ilvl and min_ilvl >= 1:
            tf["ilvl"] = {"min": int(min_ilvl)}
        filters.setdefault("misc_filters", {"filters": {}})["filters"]["corrupted"] = {"option": "false"}
    elif action == "craft_semi" and not is_unique:
        filters.setdefault("misc_filters", {"filters": {}})["filters"]["corrupted"] = {"option": "false"}
        if min_ilvl and min_ilvl >= 70:
            filters.setdefault("type_filters", {"filters": {}})["filters"]["ilvl"] = {"min": int(min_ilvl)}
    elif corrupted is False:
        filters.setdefault("misc_filters", {"filters": {}})["filters"]["corrupted"] = {"option": "false"}

    if isinstance(min_sockets, int) and min_sockets >= 2:
        filters.setdefault("equipment_filters", {"filters": {}})["filters"]["total_augment_sockets"] = {"min": min_sockets}

    if isinstance(equipment_floors, dict):
        valid_eq_min = {"ar", "ev", "es", "ward", "dps", "pdps", "edps", "damage", "aps", "crit", "spirit", "block"}
        eq_f = filters.setdefault("equipment_filters", {"filters": {}})["filters"]
        for ek, ev in equipment_floors.items():
            if not isinstance(ev, (int, float)) or isinstance(ev, bool) or ev <= 0:
                continue
            if ek == "reload_time":
                eq_f["reload_time"] = {"max": round(float(ev), 2)}
            elif ek == "edps_max":
                eq_f.setdefault("edps", {})["max"] = int(math.ceil(float(ev)))
            elif ek == "pdps_max":
                eq_f.setdefault("pdps", {})["max"] = int(math.ceil(float(ev)))
            elif ek in valid_eq_min:
                eq_f.setdefault(ek, {})["min"] = (
                    round(float(ev), 2) if ek in ("aps", "crit") else int(math.floor(float(ev)))
                )
        if not eq_f:
            filters.pop("equipment_filters", None)

    if isinstance(max_divine, (int, float)) and max_divine > 0:
        filters.setdefault("trade_filters", {"filters": {}})["filters"]["price"] = {
            "max": round(float(max_divine), 2),
            "option": "divine",
        }

    if filters:
        query["filters"] = filters

    stat_groups: list[dict[str, Any]] = []
    matched_cnt = 0
    if mandatory_stats and action != "buy_base":
        m_filters = [
            {"id": str(ms["id"]), "disabled": False, **({"value": ms["value"]} if ms.get("value") else {})}
            for ms in mandatory_stats
            if isinstance(ms, dict) and ms.get("id")
        ]
        if m_filters:
            stat_groups.append({"type": "and", "filters": m_filters})
            matched_cnt += len(m_filters)

    if mandatory_any_of and action != "buy_base":
        for any_grp in mandatory_any_of:
            if not isinstance(any_grp, dict):
                continue
            raw_fs = any_grp.get("filters") or []
            c_filters = [
                {"id": str(f["id"]), "disabled": False, **({"value": f["value"]} if f.get("value") else {})}
                for f in raw_fs
                if isinstance(f, dict) and f.get("id")
            ]
            if not c_filters:
                continue
            min_c = max(1, min(len(c_filters), int(any_grp.get("min") or 1)))
            stat_groups.append({"type": "count", "value": {"min": min_c}, "filters": c_filters[:10]})
            matched_cnt += len(c_filters[:10])

    has_multi_weight = False
    if weighted_groups and action != "buy_base":
        for wg in weighted_groups:
            if not isinstance(wg, dict):
                continue
            w_filters = [
                {"id": str(ws["id"]), "disabled": False, "value": {"weight": round(float(ws["weight"]), 2)}}
                for ws in (wg.get("filters") or [])
                if isinstance(ws, dict) and ws.get("id") and isinstance(ws.get("weight"), (int, float)) and ws["weight"] > 0
            ]
            if not w_filters:
                continue
            grp: dict[str, Any] = {"type": "weight", "filters": w_filters[:14]}
            min_v = wg.get("min")
            if isinstance(min_v, (int, float)) and min_v > 0:
                grp["value"] = {"min": round(float(min_v), 1)}
            stat_groups.append(grp)
            matched_cnt += len(w_filters[:14])
            has_multi_weight = True

    if not has_multi_weight and weighted_stats and action != "buy_base":
        w_filters = [
            {"id": str(ws["id"]), "disabled": False, "value": {"weight": round(float(ws["weight"]), 2)}}
            for ws in weighted_stats
            if isinstance(ws, dict) and ws.get("id") and isinstance(ws.get("weight"), (int, float)) and ws["weight"] > 0
        ]
        if w_filters:
            grp = {"type": "weight", "filters": w_filters[:14]}
            if isinstance(min_weight_sum, (int, float)) and min_weight_sum > 0:
                grp["value"] = {"min": round(float(min_weight_sum), 1)}
            stat_groups.append(grp)
            matched_cnt += len(w_filters[:14])
    elif not has_multi_weight and stat_filters and action != "buy_base":
        stat_groups.append({"type": "and", "filters": stat_filters[:6]})
        matched_cnt += len(stat_filters[:6])

    if stat_groups:
        query["stats"] = stat_groups

    payload = {"query": query, "sort": {"price": "asc"}}
    url = f"https://www.pathofexile.com/trade2/search/poe2/{enc_league}?q={urllib.parse.quote(json.dumps(payload))}"
    return {
        "tradeUrl": url,
        "tradeQuery": payload,
        "resolvedAffixes": resolved_affixes,
        "matchedStatCount": matched_cnt,
    }


def _parse_weapon_dps_from_props(props: dict[str, Any]) -> tuple[float, float]:
    """从 PoB2 装备 properties 计算粗略总 DPS 与物理 DPS（用于武器平替 equipment_filters.dps 下限）。"""
    aps_m = re.search(r"(\d+(?:\.\d+)?)", str(props.get("Attacks per Second") or ""))
    aps = float(aps_m.group(1)) if aps_m else 0.0
    if aps <= 0:
        return 0.0, 0.0
    phys_avg = 0.0
    pm = re.findall(r"(\d+)\s*-\s*(\d+)", str(props.get("Physical Damage") or ""))
    for lo, hi in pm:
        phys_avg += (float(lo) + float(hi)) / 2.0
    ele_avg = 0.0
    em = re.findall(r"(\d+)\s*-\s*(\d+)", str(props.get("Elemental Damage") or ""))
    for lo, hi in em:
        ele_avg += (float(lo) + float(hi)) / 2.0
    pdps = phys_avg * aps
    total_dps = (phys_avg + ele_avg) * aps
    return round(total_dps, 1), round(pdps, 1)


def build_weighted_substitute_search(
    item: dict[str, Any],
    *,
    league: str = "Standard",
    kind: str = "budget",
    parsed_build: dict[str, Any] | None = None,
    dossier_item: dict[str, Any] | None = None,
    slot_spec: dict[str, Any] | None = None,
    status_option: str = "available",
) -> dict[str, Any]:
    """基于 PoB2 倒推灵敏度（全槽位合法有效词缀池，不拘泥原件词缀）与四段式 Trade2 查询构造「加权平替」方案：
    - 面板底线（equipment_filters：攻击武器 dps/pdps/aps/crit，防具 es/ev/ar/ward，大类相同防技能失效）；
    - 硬绑定（AND：基底自带核心技能等）；
    - 等价核心门槛（COUNT >= 1：如 +N 全法术 OR +N 闪电法术）；
    - 攻防分离双加权池（WEIGHT offense + WEIGHT defense，防止抗性刷分挤占输出）。
    """
    slot = str(item.get("slot") or "")
    name = str(item.get("name") or item.get("baseType") or "")
    base_type = str(item.get("baseType") or "")
    rarity = str(item.get("rarity") or "RARE").upper()
    is_unique = rarity == "UNIQUE"
    kind_norm = "exact" if is_unique else (kind if kind in ("exact", "budget", "semi", "base") else "budget")

    deep = affix_analyzer.analyze_item_deeply(item, parsed_build)
    sa = deep.get("socketAnalysis") or {}
    props = item.get("properties") or {}
    ilvl_raw = item.get("itemLevel")
    ilvl = int(ilvl_raw) if str(ilvl_raw or "").isdigit() else 80

    # 1. 传奇/暗金：直买查询（仅看名 + 瓦尔超上限孔位）
    if is_unique:
        min_socks = int(sa.get("socketCount") or 0) if sa.get("exceedsCraftableSockets") else None
        built = build_trade_search(
            league=league,
            slot=slot,
            item_name=name,
            base_type=base_type,
            rarity="UNIQUE",
            action="buy_unique",
            lock_base=False,
            min_sockets=min_socks,
            status_option=status_option,
        )
        relaxed = build_trade_search(
            league=league,
            slot=slot,
            item_name=name,
            base_type=base_type,
            rarity="UNIQUE",
            action="buy_unique",
            lock_base=False,
            min_sockets=None,
            status_option=status_option,
        )
        return {
            "mode": "unique",
            "tradeUrl": built["tradeUrl"],
            "tradeQuery": built["tradeQuery"],
            "apiSearchQuery": built["tradeQuery"],
            "apiRelaxedQuery": relaxed["tradeQuery"],
            "weightedTerms": [],
            "targetScore": 0.0,
            "minScore": 0.0,
            "equipmentFloors": {},
            "mandatorySkills": [],
            "matchedStatCount": 0,
        }

    # 2. 白底 / 半成品：走简洁专用查询
    if kind_norm in ("base", "semi"):
        raw_locks = (deep.get("semiPlan") or {}).get("lockAffixes") or []
        if dossier_item and kind_norm == "semi":
            d_semi = (dossier_item.get("semiPlan") or {}).get("lockAffixes") or []
            if d_semi:
                raw_locks = d_semi
        mandatory_sk: list[dict[str, Any]] = []
        mandatory_skill_names: list[str] = []
        for gs in deep.get("importantGrantedSkills") or []:
            sk_res = resolve_granted_skill_to_trade_stat(f"Grants Skill: {gs['skillName']}")
            if sk_res.get("matched") and sk_res.get("statId"):
                mandatory_sk.append({"id": sk_res["statId"]})
                mandatory_skill_names.append(str(gs["skillName"]))
        scale = 0.82
        scaled_affs = []
        for ln in (raw_locks[:2] if kind_norm == "semi" else []):
            ev = _extract_numeric_value(ln)
            mv = None
            if ev and ev > 0:
                raw_m = ev * scale
                mv = max(1, int(math.floor(raw_m))) if _INT_STAT_RE.search(ln) else round(raw_m, 1)
            scaled_affs.append({"text": ln, "min": mv, "required": True})
        min_socks = int(sa.get("socketCount") or 0) if sa.get("requireSocketsInSemi") else None
        built = build_trade_search(
            league=league,
            slot=slot,
            item_name=name,
            base_type=base_type,
            rarity="RARE",
            action="buy_base" if kind_norm == "base" else "craft_semi",
            lock_base=True,
            affixes=scaled_affs,
            mandatory_stats=mandatory_sk if kind_norm == "semi" else None,
            min_ilvl=max(80, ilvl) if kind_norm == "base" else None,
            min_sockets=min_socks,
            corrupted=False,
            status_option=status_option,
        )
        relaxed = build_trade_search(
            league=league,
            slot=slot,
            item_name=name,
            base_type=base_type,
            rarity="RARE",
            action="buy_base" if kind_norm == "base" else "craft_semi",
            lock_base=True,
            affixes=scaled_affs[:1] if kind_norm == "semi" else [],
            mandatory_stats=mandatory_sk if kind_norm == "semi" else None,
            min_ilvl=max(78, ilvl - 2) if kind_norm == "base" else None,
            min_sockets=None,
            corrupted=False,
            status_option=status_option,
        )
        return {
            "mode": kind_norm,
            "tradeUrl": built["tradeUrl"],
            "tradeQuery": built["tradeQuery"],
            "apiSearchQuery": built["tradeQuery"],
            "apiRelaxedQuery": relaxed["tradeQuery"],
            "weightedTerms": [],
            "targetScore": 0.0,
            "minScore": 0.0,
            "equipmentFloors": {},
            "mandatorySkills": mandatory_skill_names,
            "matchedStatCount": built["matchedStatCount"],
        }

    # 3. 黄装「原件 / 平替」：若已计算 BD 倒推槽位规格（slot_spec），直接使用全槽位有效等价池 + 攻防分离双权重组！
    ratio_floor = 0.84 if kind_norm == "exact" else 0.65
    eq_ratio = 0.84 if kind_norm == "exact" else 0.72

    if isinstance(slot_spec, dict) and (
        slot_spec.get("offensePool") or slot_spec.get("defensePool") or slot_spec.get("targetPanels")
    ):
        spec_eq_floors: dict[str, float] = {}
        for ek, raw_v in (slot_spec.get("targetPanels") or {}).items():
            if not isinstance(raw_v, (int, float)) or raw_v <= 0:
                continue
            if ek == "aps":
                spec_eq_floors["aps"] = round(float(raw_v) * (0.94 if kind_norm == "exact" else 0.88), 2)
            elif ek == "crit":
                spec_eq_floors["crit"] = round(float(raw_v) * (0.92 if kind_norm == "exact" else 0.85), 2)
            elif ek == "reload_time":
                # 装填时间越小越好，平替档适当放宽上限
                spec_eq_floors["reload_time"] = round(float(raw_v) * (1.05 if kind_norm == "exact" else 1.15), 2)
            elif ek in ("edps_max", "pdps_max"):
                spec_eq_floors[ek] = int(math.ceil(float(raw_v)))
            else:
                spec_eq_floors[ek] = int(math.floor(float(raw_v) * eq_ratio))

        spec_mandatory = [
            {"id": str(m["id"])}
            for m in (slot_spec.get("mandatoryHard") or [])
            if isinstance(m, dict) and m.get("id")
        ]
        spec_mandatory_names = [
            str(m.get("zh") or m.get("text") or m.get("id"))
            for m in (slot_spec.get("mandatoryHard") or [])
            if isinstance(m, dict) and m.get("id")
        ]
        spec_any_of = list(slot_spec.get("mandatoryAnyOf") or [])

        off_pool = list(slot_spec.get("offensePool") or [])
        def_pool = list(slot_spec.get("defensePool") or [])
        orig_off = float(slot_spec.get("originalOffenseScore") or 0.0)
        orig_def = float(slot_spec.get("originalDefenseScore") or 0.0)

        min_off = round(orig_off * ratio_floor, 1) if orig_off >= 2.0 else None
        min_def = round(orig_def * (0.78 if kind_norm == "exact" else 0.55), 1) if orig_def >= 2.5 else None

        w_groups: list[dict[str, Any]] = []
        if off_pool and (min_off or not def_pool):
            w_groups.append({
                "label": "输出等价池",
                "min": min_off,
                "filters": [{"id": t["statId"], "weight": t["weight"]} for t in off_pool[:12]],
            })
        if def_pool and (min_def or not w_groups):
            w_groups.append({
                "label": "防御/抗性池",
                "min": min_def,
                "filters": [{"id": t["statId"], "weight": t["weight"]} for t in def_pool[:10]],
            })

        lock_base_spec = bool(slot_spec.get("mustLockBase"))
        min_socks_spec = (
            int(sa.get("socketCount") or 0)
            if (kind_norm == "exact" and sa.get("requireSocketsInBudget"))
            else None
        )
        weighted_built = build_trade_search(
            league=league,
            slot=slot,
            item_name=name,
            base_type=base_type,
            rarity="RARE",
            action="buy_finished" if kind_norm == "exact" else "buy_budget",
            lock_base=lock_base_spec,
            mandatory_stats=spec_mandatory or None,
            mandatory_any_of=spec_any_of or None,
            weighted_groups=w_groups or None,
            equipment_floors=spec_eq_floors or None,
            min_sockets=min_socks_spec,
            status_option=status_option,
        )
        combined_terms = [
            {
                "text": t.get("en") or "",
                "zh": t.get("zh") or "",
                "statId": t["statId"],
                "statText": t.get("en") or "",
                "targetVal": float(t.get("originalVal") or 0.0),
                "contrib": round(float(t.get("weight") or 0.0) * max(1.0, float(t.get("originalVal") or 10.0)), 2),
                "weight": t["weight"],
                "isResPool": bool(t.get("isResPool")),
                "bucket": "offense",
                "onOriginalItem": bool(t.get("onOriginalItem")),
            }
            for t in off_pool[:10]
        ] + [
            {
                "text": t.get("en") or "",
                "zh": t.get("zh") or "",
                "statId": t["statId"],
                "statText": t.get("en") or "",
                "targetVal": float(t.get("originalVal") or 0.0),
                "contrib": round(float(t.get("weight") or 0.0) * max(1.0, float(t.get("originalVal") or 10.0)), 2),
                "weight": t["weight"],
                "isResPool": bool(t.get("isResPool")),
                "bucket": "defense",
                "onOriginalItem": bool(t.get("onOriginalItem")),
            }
            for t in def_pool[:8]
        ]
        total_target = round(orig_off + orig_def, 1)
        total_min = round((min_off or 0.0) + (min_def or 0.0), 1)
        return {
            "mode": "weighted",
            "reverseDriven": True,
            "weaponArchetype": slot_spec.get("weaponArchetype"),
            "recommendedBases": slot_spec.get("recommendedBases") or [],
            "panelEquivalence": slot_spec.get("panelEquivalence"),
            "tradeUrl": weighted_built["tradeUrl"],
            "tradeQuery": weighted_built["tradeQuery"],
            "apiSearchQuery": weighted_built["tradeQuery"],
            "apiRelaxedQuery": weighted_built["tradeQuery"],
            "weightedTerms": combined_terms,
            "weightedGroups": [
                {"label": "输出等价池", "minScore": min_off, "targetScore": orig_off, "terms": off_pool[:10]},
                {"label": "防御/抗性池", "minScore": min_def, "targetScore": orig_def, "terms": def_pool[:8]},
            ],
            "mandatoryAnyOf": spec_any_of,
            "targetScore": total_target,
            "minScore": total_min,
            "equipmentFloors": spec_eq_floors,
            "mandatorySkills": spec_mandatory_names,
            "matchedStatCount": weighted_built["matchedStatCount"],
        }

    # 3a. 必保基底技能（如 Absent Amulet -> skill.eternal_rage）放进硬性 and 组
    mandatory_skills_filters: list[dict[str, Any]] = []
    mandatory_skill_names = []
    for gs in deep.get("importantGrantedSkills") or []:
        sk_res = resolve_granted_skill_to_trade_stat(f"Grants Skill: {gs['skillName']}")
        if sk_res.get("matched") and sk_res.get("statId"):
            mandatory_skills_filters.append({"id": sk_res["statId"]})
            mandatory_skill_names.append(str(gs["skillName"]))

    # 3b. 装备面板底线（防具总 ES/EV/AR/Ward，非攻速工具的攻击武器总 DPS）
    equipment_floors: dict[str, float] = {}
    df = deep.get("defenseFloor")
    if isinstance(df, dict) and df.get("stat") in ("es", "ev", "ar", "ward") and float(df.get("raw") or 0) > 0:
        equipment_floors[str(df["stat"])] = int(math.floor(float(df["raw"]) * eq_ratio))
    elif _is_armour_slot(slot):
        for pkey, ek, thresh in (("Ward", "ward", 40), ("Energy Shield", "es", 180), ("Evasion", "ev", 380), ("Armour", "ar", 380)):
            m = re.search(r"(\d+(?:\.\d+)?)", str(props.get(pkey) or ""))
            if m and float(m.group(1)) >= thresh:
                equipment_floors[ek] = int(math.floor(float(m.group(1)) * eq_ratio))
                break
    elif _is_weapon_slot(slot) and not deep.get("isSpeedWeapon"):
        tot_dps, pdps = _parse_weapon_dps_from_props(props)
        if tot_dps >= 180:
            equipment_floors["dps"] = int(math.floor(tot_dps * eq_ratio))
        elif pdps >= 150:
            equipment_floors["pdps"] = int(math.floor(pdps * eq_ratio))

    # 3c. 从消融账本或静态深度分析计算每条有用词缀的权重
    dossier_affix_by_line: dict[str, dict[str, Any]] = {}
    if isinstance(dossier_item, dict):
        for da in dossier_item.get("affixes") or []:
            ln = str(da.get("line") or "").strip().lower()
            if ln and ln not in dossier_affix_by_line:
                dossier_affix_by_line[ln] = da

    weighted_terms: list[dict[str, Any]] = []
    seen_sids: set[str] = set()
    has_ele_res_contrib = 0.0
    has_ele_res_val = 0.0

    # 若装备本身已经通过 equipment_floors 锁了本地护盾/闪避/护甲/武器点伤，避免再把本地纯数值词条重复放进权重池
    def _covered_by_eq_floor(mod_t: str) -> bool:
        ml = mod_t.lower()
        if "es" in equipment_floors and re.search(r"maximum energy shield|increased (?:armour and |evasion and )?energy shield\b", ml) and "recharge" not in ml:
            return True
        if "ev" in equipment_floors and re.search(r"to evasion rating|increased (?:armour and )?evasion", ml):
            return True
        if "ar" in equipment_floors and re.search(r"to armour|increased armour", ml):
            return True
        if ("dps" in equipment_floors or "pdps" in equipment_floors) and re.search(r"increased physical damage|adds \d+ to \d+ .* damage", ml):
            return True
        return False

    for a in deep.get("affixes") or []:
        if a.get("excludeFromTradeLock") or a.get("role") == "dead" or a.get("isAnoint") or a.get("origin") == "implicit":
            continue
        line = str(a.get("line") or "").strip()
        if not line or _covered_by_eq_floor(line):
            continue
        da = dossier_affix_by_line.get(line.lower()) or {}
        if da.get("purposeKind") == "dead":
            continue
        # 计算该词缀的综合价值贡献分
        dps_l = max(0.0, float(da.get("dpsLossPct") or 0.0))
        ehp_l = max(0.0, float(da.get("ehpLossPct") or 0.0))
        mana_l = max(0.0, float(da.get("manaLossPct") or 0.0))
        sp_l = max(0.0, float(da.get("spiritLoss") or 0.0))
        res_l = max(0.0, float(da.get("maxResDrop") or 0.0))
        ctx_s = float(a.get("contextualScore") or 0.0)

        contrib = dps_l * 1.4 + ehp_l * 0.95 + mana_l * 0.4 + sp_l * 0.35 + res_l * 0.16
        # 对 PoB2 面板未直显或无消融账本时的静态高分机制/核心词缀给有区分度的基础贡献分
        if contrib < 1.5:
            if ctx_s >= 108:
                contrib = 18.0
            elif ctx_s >= 96:
                contrib = 14.5
            elif ctx_s >= 86:
                contrib = 11.0
            elif ctx_s >= 72:
                contrib = 7.5
            elif ctx_s >= 55:
                contrib = 4.0
            else:
                continue

        val = _extract_numeric_value(line)
        if val is None or val <= 0:
            continue

        # 单元素抗性（火/冰/电）合入可互换抗性池，不死锁某一单抗
        if re.search(r"to (fire|cold|lightning) resistance\b", line, re.I) and "all elemental" not in line.lower():
            has_ele_res_contrib += contrib
            has_ele_res_val += float(val)
            continue

        resolved = resolve_affix_to_trade_stat(line, slot=slot, base_type=base_type)
        if not resolved.get("matched") or not resolved.get("statId"):
            continue
        sid = str(resolved["statId"])
        if sid in seen_sids:
            continue
        seen_sids.add(sid)

        per_unit_w = round(max(0.05, min(25.0, contrib / max(1.0, float(val)))), 2)
        weighted_terms.append({
            "text": line,
            "zh": resolved.get("zh") or line,
            "statId": sid,
            "statText": resolved.get("statText") or line,
            "targetVal": float(val),
            "contrib": round(contrib, 2),
            "ctxScore": round(ctx_s, 1),
            "weight": per_unit_w,
            "isResPool": False,
        })

    # 若原装备带元素抗性，把火/冰/电单抗（同权）与全抗（3倍权）一并放入加权池，任意达标抗性组合皆可平替
    if has_ele_res_val >= 20:
        w_res = round(max(0.08, min(1.5, max(4.5, has_ele_res_contrib) / max(30.0, has_ele_res_val))), 2)
        for rkey, (rsid, rzh, mult) in _ELE_RES_STAT_IDS.items():
            if rsid in seen_sids:
                continue
            seen_sids.add(rsid)
            weighted_terms.append({
                "text": f"Interchangeable {rkey} resistance",
                "zh": f"{rzh}（抗性互换池）",
                "statId": rsid,
                "statText": rzh,
                "targetVal": round(has_ele_res_val / 3.0, 1) if rkey != "all_ele" else 0.0,
                "contrib": round((has_ele_res_contrib or 5.0) / 3.0, 2) if rkey != "all_ele" else 0.0,
                "weight": round(w_res * mult, 2),
                "isResPool": True,
            })

    weighted_terms.sort(key=lambda x: (x["isResPool"], -x["contrib"], -float(x.get("ctxScore") or 0.0)))
    primary_terms = [t for t in weighted_terms if not t["isResPool"]][:6]
    res_terms = [t for t in weighted_terms if t["isResPool"]]
    final_terms = primary_terms + res_terms

    # 计算目标装备在这套权重下的基准总分（仅计目标件实际拥有的词缀分 + 目标抗性总分）
    target_score = sum(t["weight"] * t["targetVal"] for t in primary_terms)
    if has_ele_res_val >= 20 and res_terms:
        w_single = res_terms[0]["weight"]
        target_score += w_single * has_ele_res_val
    target_score = round(target_score, 1)
    min_score = round(target_score * ratio_floor, 1) if target_score > 0 else None

    lock_base = bool(deep.get("requireBaseType"))
    # 原件模式锁超工匠上限孔位；平替模式默认不死锁瓦尔超孔避免把平替筛空
    min_socks = (
        int(sa.get("socketCount") or 0)
        if (kind_norm == "exact" and sa.get("requireSocketsInBudget"))
        else None
    )

    # Track A：官方 Trade2 浏览器直达加权求和查询 (`type: "weight"`)
    weighted_built = build_trade_search(
        league=league,
        slot=slot,
        item_name=name,
        base_type=base_type,
        rarity="RARE",
        action="buy_finished" if kind_norm == "exact" else "buy_budget",
        lock_base=lock_base,
        mandatory_stats=mandatory_skills_filters or None,
        weighted_stats=[{"id": t["statId"], "weight": t["weight"]} for t in final_terms] or None,
        min_weight_sum=min_score,
        equipment_floors=equipment_floors or None,
        min_sockets=min_socks,
        status_option=status_option,
    )

    # Track B：免登录 API 100% 兼容的 `type: "count"` + `equipment_filters` + `skill.*` 查价查询
    def _make_count_query(
        *,
        term_scale: float,
        min_count: int,
        eq_scale: float,
        keep_sockets: bool,
        keep_base: bool,
    ) -> dict[str, Any]:
        q_obj: dict[str, Any] = {"status": {"option": status_option or "available"}}
        f_obj: dict[str, Any] = {}
        if keep_base and base_type:
            q_obj["type"] = base_type
        else:
            cat = infer_trade_category(slot, base_type)
            if cat:
                f_obj.setdefault("type_filters", {"filters": {}})["filters"]["category"] = {"option": cat}
            elif base_type:
                q_obj["type"] = base_type
        if keep_sockets and min_socks and min_socks >= 2:
            f_obj.setdefault("equipment_filters", {"filters": {}})["filters"]["total_augment_sockets"] = {"min": min_socks}
        for ek, ev in equipment_floors.items():
            scaled_ev = ev * eq_scale
            if scaled_ev > 0:
                f_obj.setdefault("equipment_filters", {"filters": {}})["filters"][ek] = {
                    "min": round(scaled_ev, 2) if ek in ("aps", "crit") else int(math.floor(scaled_ev))
                }
        if f_obj:
            q_obj["filters"] = f_obj

        s_groups: list[dict[str, Any]] = []
        if mandatory_skills_filters:
            s_groups.append({"type": "and", "filters": [{"id": ms["id"], "disabled": False} for ms in mandatory_skills_filters]})

        # 取贡献最高的 4~5 条非抗性池词缀 + 若无非抗性词缀则用单抗池，组成 count 池
        pool_terms = primary_terms[:5]
        if len(pool_terms) < 2 and res_terms:
            for rt in res_terms:
                if rt["statId"] != _ELE_RES_STAT_IDS["all_ele"][0]:
                    pool_terms.append({**rt, "targetVal": min(40.0, max(25.0, has_ele_res_val / 2.0))})
        count_filters: list[dict[str, Any]] = []
        for t in pool_terms[:5]:
            raw_m = float(t["targetVal"]) * term_scale
            mv = max(1, int(math.floor(raw_m))) if _INT_STAT_RE.search(t["text"]) else round(raw_m, 1)
            count_filters.append({"id": t["statId"], "disabled": False, "value": {"min": mv}})
        if count_filters:
            eff_min = max(1, min(min_count, len(count_filters)))
            s_groups.append({"type": "count", "value": {"min": eff_min}, "filters": count_filters})
        if s_groups:
            q_obj["stats"] = s_groups
        return {"query": q_obj, "sort": {"price": "asc"}}

    n_primary = len(primary_terms)
    strict_min_cnt = (
        min(4, n_primary)
        if kind_norm == "exact"
        else (3 if (n_primary >= 5 and not equipment_floors) else (2 if n_primary >= 2 else 1))
    )
    api_query = _make_count_query(
        term_scale=0.80 if kind_norm == "exact" else 0.65,
        min_count=strict_min_cnt,
        eq_scale=1.0,
        keep_sockets=bool(min_socks),
        keep_base=lock_base,
    )
    relaxed_query = _make_count_query(
        term_scale=0.55,
        min_count=max(1, strict_min_cnt - 1),
        eq_scale=0.82,
        keep_sockets=False,
        keep_base=lock_base,
    )

    return {
        "mode": "weighted",
        "tradeUrl": weighted_built["tradeUrl"],
        "tradeQuery": weighted_built["tradeQuery"],
        "apiSearchQuery": api_query,
        "apiRelaxedQuery": relaxed_query,
        "weightedTerms": final_terms,
        "targetScore": target_score,
        "minScore": min_score or 0.0,
        "equipmentFloors": equipment_floors,
        "mandatorySkills": mandatory_skill_names,
        "matchedStatCount": weighted_built["matchedStatCount"],
    }


def enrich_copy_plan_items(
    raw_items: list[dict[str, Any]],
    *,
    league: str = "Standard",
    profile: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """为 AI 输出的造价抄作业清单逐件解析词条/面板底线/OR加权蓝图并生成一键直达官方市集 URL。

    同时支持两代格式：
      - 新版倒推加权蓝图：equipmentFloors + mandatoryAffixes (AND) + mandatoryAnyOf (OR) +
        offensePool / defensePool / weightedAffixes (Weighted Sum OR)，并自动融合 reverseSpec.slotSpecs；
      - 旧版 selectedAffixes（向后兼容）。
    """
    slot_lookup: dict[str, dict[str, Any]] = {}
    slot_spec_lookup: dict[str, dict[str, Any]] = {}
    if isinstance(profile, dict):
        build = profile.get("build") or {}
        for it in list(build.get("equippedItems") or []) + list(build.get("jewels") or []) + list(build.get("flasksAndCharms") or []):
            s = str(it.get("slot") or "").strip().lower()
            if s and s not in slot_lookup:
                slot_lookup[s] = it
        rev = (
            (profile.get("ablationDossier") or {}).get("reverseSpec")
            if isinstance(profile.get("ablationDossier"), dict)
            else None
        ) or profile.get("reverseSpec")
        if isinstance(rev, dict) and isinstance(rev.get("slotSpecs"), dict):
            for k, v in rev["slotSpecs"].items():
                if isinstance(v, dict):
                    slot_spec_lookup[str(k).strip().lower()] = v

    valid_actions = {"buy_unique", "buy_finished", "buy_budget", "craft_semi", "buy_base", "skip"}
    enriched: list[dict[str, Any]] = []

    def _resolve_blueprint_term(raw_t: Any, slot: str, base_type: str) -> dict[str, Any] | None:
        if isinstance(raw_t, str):
            raw_t = {"text": raw_t}
        if not isinstance(raw_t, dict):
            return None
        text = str(raw_t.get("text") or raw_t.get("en") or "").strip()
        zh = str(raw_t.get("zh") or "").strip()
        sid = str(raw_t.get("statId") or raw_t.get("id") or "").strip()
        if sid:
            res = {"matched": True, "statId": sid, "statText": str(raw_t.get("statText") or text), "statType": "explicit", "zh": zh or text}
        else:
            res = resolve_affix_to_trade_stat(text, zh=zh, slot=slot, base_type=base_type)
        if not res.get("matched") or not res.get("statId"):
            return None
        out: dict[str, Any] = {
            "statId": str(res["statId"]),
            "text": text or str(res.get("statText") or zh),
            "zh": zh or str(res.get("zh") or text),
            "statText": str(res.get("statText") or text),
        }
        w_val = raw_t.get("weight")
        if isinstance(w_val, (int, float)) and not isinstance(w_val, bool) and float(w_val) > 0:
            out["weight"] = round(float(w_val), 3)
        min_v = raw_t.get("min")
        if isinstance(min_v, (int, float)) and not isinstance(min_v, bool):
            out["min"] = int(math.floor(float(min_v))) if _INT_STAT_RE.search(out["text"]) else round(float(min_v), 2)
        if raw_t.get("bucket"):
            out["bucket"] = str(raw_t["bucket"])
        if raw_t.get("isResPool"):
            out["isResPool"] = True
        return out

    for idx, item in enumerate(raw_items or []):
        if not isinstance(item, dict):
            continue
        slot = str(item.get("slot") or "").strip()
        item_name = str(item.get("itemName") or item.get("name") or "").strip()
        if not slot and not item_name:
            continue

        ref = slot_lookup.get(slot.lower()) or {}
        spec = slot_spec_lookup.get(slot.lower()) or {}
        base_type = str(item.get("baseType") or ref.get("baseType") or "").strip()
        rarity = str(item.get("rarity") or ref.get("rarity") or "RARE").strip().upper()
        if rarity not in ("UNIQUE", "RARE", "MAGIC", "NORMAL"):
            rarity = "UNIQUE" if str(item.get("action") or "") == "buy_unique" else "RARE"

        action = str(item.get("action") or "").strip().lower()
        if action not in valid_actions:
            action = "buy_unique" if rarity == "UNIQUE" else "buy_budget"
        if rarity == "UNIQUE" and action in ("craft_semi", "buy_base"):
            action = "buy_unique"

        try:
            priority = int(item.get("priority") or (idx + 1))
        except (TypeError, ValueError):
            priority = idx + 1

        budget_raw = item.get("budgetDivine")
        try:
            budget_d = round(float(budget_raw), 2) if budget_raw is not None and str(budget_raw) != "" else None
        except (TypeError, ValueError):
            budget_d = None

        ilvl_raw = item.get("minIlvl")
        try:
            min_ilvl = int(ilvl_raw) if ilvl_raw is not None and int(ilvl_raw) > 0 else None
        except (TypeError, ValueError):
            min_ilvl = None

        sock_raw = item.get("minSockets")
        try:
            min_sockets = int(sock_raw) if sock_raw is not None and int(sock_raw) > 0 else None
        except (TypeError, ValueError):
            min_sockets = None

        corrupted = item.get("corrupted") if isinstance(item.get("corrupted"), bool) else None
        lock_base = item.get("lockBase") if isinstance(item.get("lockBase"), bool) else None
        reason = str(item.get("reason") or "").strip()
        socket_tip = str(item.get("socketTip") or "").strip()

        raw_affixes = item.get("selectedAffixes") or item.get("affixes") or []
        if not isinstance(raw_affixes, list):
            raw_affixes = []

        cut_affixes_raw = item.get("cutAffixes") or []
        cut_affixes = [str(x).strip() for x in cut_affixes_raw if str(x).strip()][:6] if isinstance(cut_affixes_raw, list) else []

        # --- 新版四段式声明式搜索蓝图解析 ---
        eq_floors_raw = item.get("equipmentFloors")
        eq_floors: dict[str, float] = {}
        if isinstance(eq_floors_raw, dict):
            valid_blueprint_eq = (
                "ar", "ev", "es", "ward", "dps", "pdps", "edps", "damage",
                "aps", "crit", "reload_time", "edps_max", "pdps_max", "spirit", "block",
            )
            for ek, ev in eq_floors_raw.items():
                if ek in valid_blueprint_eq and isinstance(ev, (int, float)) and not isinstance(ev, bool) and float(ev) > 0:
                    if ek in ("aps", "crit", "reload_time"):
                        eq_floors[ek] = round(float(ev), 2)
                    elif ek in ("edps_max", "pdps_max"):
                        eq_floors[ek] = int(math.ceil(float(ev)))
                    else:
                        eq_floors[ek] = int(math.floor(float(ev)))

        # 1) 硬绑定词条 (AND)
        mandatory_resolved: list[dict[str, Any]] = []
        mandatory_stats_payload: list[dict[str, Any]] = []
        for ma in item.get("mandatoryAffixes") or []:
            rt = _resolve_blueprint_term(ma, slot, base_type)
            if rt:
                mandatory_resolved.append(rt)
                f_obj: dict[str, Any] = {"id": rt["statId"]}
                if rt.get("min") is not None:
                    f_obj["value"] = {"min": rt["min"]}
                mandatory_stats_payload.append(f_obj)

        # 2) 等价核心门槛 (COUNT >= k / OR)
        any_of_resolved: list[dict[str, Any]] = []
        any_of_payload: list[dict[str, Any]] = []
        raw_any_list = item.get("mandatoryAnyOf")
        if isinstance(raw_any_list, dict):
            raw_any_list = [raw_any_list]
        if isinstance(raw_any_list, list):
            for grp in raw_any_list:
                if not isinstance(grp, dict):
                    continue
                terms = []
                p_filters = []
                for sub in grp.get("affixes") or grp.get("filters") or []:
                    rt = _resolve_blueprint_term(sub, slot, base_type)
                    if rt:
                        terms.append(rt)
                        pf: dict[str, Any] = {"id": rt["statId"]}
                        if rt.get("min") is not None:
                            pf["value"] = {"min": rt["min"]}
                        p_filters.append(pf)
                if p_filters:
                    min_c = max(1, min(len(p_filters), int(grp.get("minCount") or grp.get("min") or 1)))
                    any_of_resolved.append({
                        "label": str(grp.get("label") or f"至少满足 {min_c} 条"),
                        "min": min_c,
                        "terms": terms,
                    })
                    any_of_payload.append({"min": min_c, "filters": p_filters})

        # 3) 加权 OR 词缀池（支持攻防分离双组、单加权池、或自动引用 reverseSpec 按 ratio 编排）
        weighted_groups_resolved: list[dict[str, Any]] = []
        weighted_groups_payload: list[dict[str, Any]] = []

        def _append_weight_group(label: str, raw_terms: list[Any], min_score: float | None, bucket: str) -> None:
            r_terms: list[dict[str, Any]] = []
            p_filters: list[dict[str, Any]] = []
            seen_ids: set[str] = set()
            for raw_t in raw_terms:
                rt = _resolve_blueprint_term(raw_t, slot, base_type)
                if not rt or rt["statId"] in seen_ids:
                    continue
                seen_ids.add(rt["statId"])
                w = float(rt.get("weight") or 1.0)
                rt["weight"] = round(max(0.02, min(25.0, w)), 3)
                rt["bucket"] = bucket
                r_terms.append(rt)
                p_filters.append({"id": rt["statId"], "weight": rt["weight"]})
            if p_filters:
                ms = round(float(min_score), 1) if isinstance(min_score, (int, float)) and float(min_score) > 0 else None
                weighted_groups_resolved.append({"label": label, "bucket": bucket, "minScore": ms, "terms": r_terms})
                weighted_groups_payload.append({"label": label, "min": ms, "filters": p_filters})

        if isinstance(item.get("offensePool"), list) and item["offensePool"]:
            _append_weight_group("输出等价池", item["offensePool"], item.get("minOffenseScore"), "offense")
        elif isinstance(item.get("offenseTargetRatio"), (int, float)) and float(item["offenseTargetRatio"]) > 0 and spec.get("offensePool"):
            ratio = max(0.25, min(1.2, float(item["offenseTargetRatio"])))
            orig_off = float(spec.get("originalOffenseScore") or 0.0)
            _append_weight_group(
                "输出等价池",
                spec["offensePool"],
                round(orig_off * ratio, 1) if orig_off >= 2.0 else None,
                "offense",
            )

        if isinstance(item.get("defensePool"), list) and item["defensePool"]:
            _append_weight_group("防御/抗性池", item["defensePool"], item.get("minDefenseScore"), "defense")
        elif isinstance(item.get("defenseTargetRatio"), (int, float)) and float(item["defenseTargetRatio"]) > 0 and spec.get("defensePool"):
            ratio = max(0.25, min(1.2, float(item["defenseTargetRatio"])))
            orig_def = float(spec.get("originalDefenseScore") or 0.0)
            _append_weight_group(
                "防御/抗性池",
                spec["defensePool"],
                round(orig_def * ratio, 1) if orig_def >= 2.0 else None,
                "defense",
            )

        if not weighted_groups_payload and isinstance(item.get("weightedAffixes"), list) and item["weightedAffixes"]:
            _append_weight_group("等价加权池", item["weightedAffixes"], item.get("minWeightSum"), "combined")

        # 若 AI 使用了新版蓝图字段（equipmentFloors / mandatoryAnyOf / weightedGroups），不再退回死板 AND
        has_blueprint = bool(
            rarity != "UNIQUE"
            and action not in ("buy_base", "skip")
            and (weighted_groups_payload or any_of_payload or eq_floors or mandatory_stats_payload)
        )

        # 若 AI 指定了 equipmentFloors 但省略了词缀池，仍自动补齐 reverseSpec 的必保基底技能
        if has_blueprint and not mandatory_stats_payload and spec.get("mandatoryHard"):
            for mh in spec["mandatoryHard"]:
                if isinstance(mh, dict) and mh.get("id"):
                    mandatory_stats_payload.append({"id": str(mh["id"])})
                    mandatory_resolved.append({"statId": str(mh["id"]), "zh": str(mh.get("zh") or ""), "text": str(mh.get("text") or "")})

        trade_built = build_trade_search(
            league=league,
            slot=slot,
            item_name=item_name or str(ref.get("name") or base_type or slot),
            base_type=base_type,
            rarity=rarity,
            action=action,
            lock_base=lock_base,
            affixes=[] if has_blueprint else raw_affixes,
            mandatory_stats=mandatory_stats_payload or None,
            mandatory_any_of=any_of_payload or None,
            weighted_groups=weighted_groups_payload or None,
            equipment_floors=eq_floors or None,
            min_ilvl=min_ilvl,
            min_sockets=min_sockets,
            corrupted=corrupted,
        )

        effective_lock_base = (
            lock_base
            if lock_base is not None
            else (
                action in ("buy_base", "craft_semi")
                or bool(spec.get("mustLockBase"))
                or bool(base_type and _SPECIAL_BASE_RE.search(base_type))
            )
        )

        search_mode = (
            "unique"
            if rarity == "UNIQUE" or action == "buy_unique"
            else (
                "base"
                if action == "buy_base"
                else ("weighted_blueprint" if has_blueprint else "and_affixes")
            )
        )

        blueprint_obj = (
            {
                "equipmentFloors": eq_floors,
                "mandatoryHard": mandatory_resolved,
                "mandatoryAnyOf": any_of_resolved,
                "weightedGroups": weighted_groups_resolved,
            }
            if has_blueprint
            else None
        )

        enriched.append({
            "slot": slot or "Gear",
            "action": action,
            "priority": priority,
            "itemName": item_name or str(ref.get("name") or base_type or slot),
            "baseType": base_type,
            "rarity": rarity,
            "budgetDivine": budget_d,
            "minIlvl": min_ilvl,
            "minSockets": min_sockets,
            "corrupted": corrupted,
            "lockBase": effective_lock_base,
            "reason": reason,
            "searchMode": search_mode,
            "searchBlueprint": blueprint_obj,
            "selectedAffixes": trade_built["resolvedAffixes"],
            "cutAffixes": cut_affixes,
            "socketTip": socket_tip,
            "tradeUrl": trade_built["tradeUrl"],
            "tradeQuery": trade_built["tradeQuery"],
            "matchedStatCount": trade_built["matchedStatCount"],
        })

    enriched.sort(key=lambda x: (x["action"] == "skip", x["priority"]))
    return enriched[:14]
