"""后端领域层公共纯函数与 PoE2 技能/防御/估值常量（单一真源，消除跨模块重复定义）。"""
from __future__ import annotations

import re
from typing import Any


def safe_num(v: Any, default: float = 0.0) -> float:
    """安全将任意值转换为有限浮点数，NaN/None/非法字符串返回 default。"""
    try:
        n = float(v)
        return n if n == n else default
    except (TypeError, ValueError):
        return default


def pct_loss(base: float, after: float, *, ndigits: int = 2) -> float:
    """计算消融后相对基线的百分比损失（正值表示移除该项导致数值下降）。"""
    if base <= 1e-6:
        return 0.0
    return round(((base - after) / base) * 100.0, ndigits)


def pct_delta(new_val: float, base_val: float, *, ndigits: int = 2) -> float:
    """计算变更后相对基线的百分比增减（正值表示提升）。"""
    if base_val <= 1e-6:
        return 0.0
    return round(((new_val - base_val) / base_val) * 100.0, ndigits)


def is_inactive_weapon_slot(slot: str, active_ws: int) -> bool:
    """判断指定装备槽位是否归属于当前未激活的副武器组。"""
    s = (slot or "").strip().lower()
    if active_ws == 1:
        return s in ("weapon 1 swap", "weapon 2 swap")
    if active_ws == 2:
        return s in ("weapon 1", "weapon 2")
    return False


# ---------------------------------------------------------------------------
# PoE2 技能与防御语义公共正则/知识表（供 engine_facts / ablation_dossier 等共用）
# ---------------------------------------------------------------------------
TRAVEL_SKILL_RE = re.compile(
    r"Travel|Blink|Whirling Slash|Tempest Flurry|Shield Charge|Leap Slam|Lightning Warp|"
    r"Vaulting Impact|Dodge Roll|Dash|Roll|Stampede|Flicker Strike|Rhoa|Disengage|Escape Shot|"
    r"Thunderous Leap|Pounce",
    re.I,
)

CURSE_MARK_RE = re.compile(
    r"Curse|Mark|Conductivity|Flammability|Hypothermia|Despair|Vulnerability|Enfeeble|Temporal Chains|"
    r"Elemental Weakness|Voltaic Mark|Sniper's Mark|Freezing Mark|Assassins Mark|Blasphemy",
    re.I,
)

TRIGGER_RE = re.compile(
    r"Cast on |Spellslinger|Barrier Invocation|Elemental Invocation|Reaper's Invocation|Meta",
    re.I,
)

SPIRIT_AURA_RE = re.compile(
    r"^(Archmage|Trinity|Elemental Conflux|Mana Remnants|Siphon Elements|Grim Feast|Ghost Dance|"
    r"Wind Dancer|Herald of |Blasphemy|Lingering Illusion|Combat Frenzy|Alchemist's Boon|"
    r"Time of Need|Berserk|Scavenged Plating|Overwhelming Presence|Arctic Armour|Clarity|"
    r"Vitality|Precision|Cannibalism|Herbalism|Raging Spirits|Skeletal |Cast on |"
    r"Purity of |Discipline|Charge Regulation|Barkskin|Convalescence|Life Remnants|Meditate)",
    re.I,
)

DEFENSE_SKILL_RE = re.compile(
    r"Virtuous Barrier|Purity of |Discipline|Ghost Dance|Wind Dancer|Grim Feast|Life Remnants|"
    r"Convalescence|Meditate|Time of Need|Arctic Armour|Scavenged Plating|Barkskin|Temporal Rift|"
    r"Frost Wall|Raise Shield|Parry|Magma Barrier|Resonating Shield|Charge Regulation|Vitality|"
    r"Cannibalism|Herbalism|Enfeeble|Temporal Chains|Overwhelming Presence|Alchemist's Boon|"
    r"Bone Cage|Mana Remnants|Clarity|Blasphemy|Herald of Ice",
    re.I,
)

DEFENSE_SUPPORT_RE = re.compile(
    r"vitality|cannibalism|herbalism|clarity|blind|knockback|pin\b|freeze|glaciation|electrocute|"
    r"hinder|maim|taunt|meat shield|life leech|soul drain|life thief|energy barrier|olroth's conviction|"
    r"her declaration|atalui's bloodletting|ironwood|block|deflect|ward|second wind",
    re.I,
)

SKILL_DEFENSE_KNOWLEDGE: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"virtuous barrier", re.I), "防御屏障/护盾", "古灵升华赋予的专属防御屏障，吸收来袭伤害提升容错"),
    (re.compile(r"ghost dance", re.I), "受击瞬回护盾", "受击消耗鬼舞层数按闪避值瞬间恢复能量护盾（静态 EHP 未完全折算）"),
    (re.compile(r"wind dancer", re.I), "闪避/偏转+击退", "常驻叠层大幅抬高闪避/偏转，受击爆发击退并致盲周围近战怪"),
    (re.compile(r"charge regulation", re.I), "耐力球减伤/防御", "依据持有球类型常驻提供耐力球物减/防御与技能增益"),
    (re.compile(r"purity of (fire|ice|lightning|elements)", re.I), "元素抗性光环+挂件位", "提供高额元素抗性（及全抗/异常免疫），且零精魂基底款可白嫖挂载回复辅助"),
    (re.compile(r"\bdiscipline\b", re.I), "能量护盾光环", "直接提供能量护盾上限或护盾充能增益"),
    (re.compile(r"arctic armour", re.I), "极地装甲减伤/冰缓", "站立/受击提供物理与火焰减伤并对近战攻击者施加冰缓"),
    (re.compile(r"scavenged plating", re.I), "破甲叠护甲", "破坏敌人护甲时叠加大量额外护甲提升物理承伤"),
    (re.compile(r"\bbarkskin\b", re.I), "树皮护甲/闪避动态层", "动态在护甲与闪避间平衡承伤，强化防暴毙能力"),
    (re.compile(r"convalescence", re.I), "强制启动ES充能", "一键强制开启能量护盾充能且不被打断，护盾流关键保命技"),
    (re.compile(r"temporal rift", re.I), "时空回溯保命", "瞬移回溯至数秒前的位置并恢复当时的血/盾/蓝状态"),
    (re.compile(r"frost wall", re.I), "冰墙阻隔/控场", "阻挡敌人与投射物走位，叠加冰缓/冰冻控场（配合自动触发兼具防贴脸与引爆）"),
    (re.compile(r"time of need", re.I), "周期回血+解异常", "周期性回复大量生命并净化诅咒与元素异常状态"),
    (re.compile(r"grim feast", re.I), "护盾溢出翻倍", "刷图拾取残片使能量护盾溢出至 200%，大幅抬高实战一击线"),
    (re.compile(r"life remnants", re.I), "生命溢出恢复", "拾取生命残片实现生命回复与上限溢出"),
    (re.compile(r"\bmeditate\b", re.I), "引导护盾溢流", "主动引导快速将能量护盾充能并溢出上限"),
    (re.compile(r"\bvitality\b", re.I), "持续生命秒回", "常驻提供稳定生命秒回，对抗持续掉血与地面 dot"),
    (re.compile(r"\bcannibalism\b", re.I), "击杀回血续航", "刷图击杀按百分比稳定回血，显著提升高密图生存"),
    (re.compile(r"\bherbalism\b", re.I), "药剂回复强化", "提升生命药剂回复效率与残血救急能力"),
    (re.compile(r"\bclarity\b", re.I), "回蓝/MoM续航", "常驻提升魔力回复（MoM 体系下直接等效生存回复）"),
    (re.compile(r"mana remnants", re.I), "蓝球溢流/MoM池", "拾取魔力残片溢流蓝池（MoM/大法师体系兼顾输出与承伤池）"),
    (re.compile(r"\benfeeble\b", re.I), "衰弱降敌伤害", "诅咒直接降低敌人造成的伤害与暴击威胁"),
    (re.compile(r"temporal chains", re.I), "时空锁链减速", "大幅降低敌人行动速度，拉长躲避窗口与异常控场时间"),
    (re.compile(r"herald of ice", re.I), "碎尸防亡语", "冰冻击杀触发碎尸爆炸，消除高危怪物死后爆炸/亡语威胁"),
    (re.compile(r"atalui's bloodletting", re.I), "耗血转巨额秒回", "配合耗血机制提供超高额持续回复"),
    (re.compile(r"olroth's conviction", re.I), "结界防御联动", "利用/转化结界（Ward）提供额外防御缓冲或技能联动"),
    (re.compile(r"\bblind\b", re.I), "致盲降敌命中", "使敌人致盲（降低 20% 命中），同步拉高闪避与偏转有效收益"),
    (re.compile(r"\bknockback\b", re.I), "击退防贴脸", "高频击退贴脸怪物，大幅减少近战集火承伤"),
    (re.compile(r"\bpin\b", re.I), "定身硬控", "定身限制怪物移动与近身，提供安全输出空间"),
    (re.compile(r"\belectrocute\b", re.I), "感电硬控打断", "累积感电定身敌人并打断高危技能施放"),
    (re.compile(r"glaciation|\bfreeze\b", re.I), "冰冻控场", "加快冰冻积蓄冻结敌人，打断攻击动作"),
    (re.compile(r"raise shield|\bparry\b|magma barrier|resonating shield", re.I), "主动举盾/格挡反击", "主动格挡吸收伤害并触发反制或晕眩"),
    (re.compile(r"\btaunt\b|meat shield", re.I), "嘲讽/召唤承伤", "让图腾或召唤物吸引火力并分摊伤害"),
    (re.compile(r"life leech|life thief|soul drain", re.I), "生命/护盾偷取", "通过击中稳定偷取生命或护盾维持实战血线"),
    (re.compile(r"energy barrier", re.I), "防打断护盾充能", "引导/施法受晕眩时立刻启动护盾充能防止连吃暴毙"),
)

DESC_DEFENSE_KW_RE = re.compile(
    r"less damage taken|reduced damage taken|guard|barrier|absorb|maximum .* resistance|evasion|deflect|"
    r"armour|energy shield|recover .* life|regenerate .* life|recharge|blind|knockback|freeze|chill|"
    r"pin\b|electrocute|hinder|taunt|cleanse|ailment|cannot be stunned|\bward\b",
    re.I,
)

SUPPORT_CRIT_RE = re.compile(r"crit|inevitable|supercritical|ambush|pinpoint|precision", re.I)

SUPPORT_UTILITY_RE = re.compile(
    r"clarity|herbalism|cannibalism|vitality|inspiration|efficiency|second wind|ingenuity|persistence|"
    r"acceleration|mobility|blind|culling|rarity|area|magnified|fork|chain|pierce|scattershot|ricochet|"
    r"multishot|wildshards|unleash|cascade|echo|fast forward|arcane tempo|martial tempo",
    re.I,
)

FALLBACK_CHASE_ESTIMATES: dict[str, float] = {
    "temporalis": 180.0,
    "astramentis": 90.0,
    "mageblood": 120.0,
    "headhunter": 55.0,
    "kalandra's touch": 80.0,
    "the adorned": 60.0,
    "voices": 50.0,
    "heart of the well": 25.0,
    "against the darkness": 20.0,
    "prism of belief": 25.0,
    "rite of passage": 18.0,
    "ingenuity": 18.0,
    "dream fragments": 18.0,
    "morior invictus": 14.0,
    "svalinn": 15.0,
    "controlled metamorphosis": 12.0,
    "from nothing": 12.0,
    "megalomaniac": 12.0,
    "hand of wisdom and action": 8.0,
    "runeseeker's call": 25.0,
    "melting maelstrom": 6.0,
    "uhtred's chalice": 6.0,
    "the taming": 4.0,
    "yoke of suffering": 3.0,
}
