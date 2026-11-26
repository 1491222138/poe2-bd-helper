"""插件化做装策略抽象接口（支持普通/高阶/完美通货、预兆、精华、破裂锁词与 MDP 策略求解）。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CraftingContext:
    slot: str
    item_class: str
    ilvl: int
    pool: dict[str, Any]
    matched_wanted: list[dict[str, Any]]
    matched_existing: list[dict[str, Any]]
    semi_base_price: float | None
    finished_buy_price: float | None
    rates: dict[str, Any]
    available_essences: list[dict[str, Any]] = field(default_factory=list)
    essence_name: str | None = None
    essence_price: float | None = None
    white_base_price: float | None = None
    min_tier_mode: str = "any"
    fractured_group: str | None = None


class CraftingStrategy(ABC):
    """统一做装策略接口：每个策略输出符合 CraftStrategyEstimate 契约的估算结果。"""

    kind: str = "semi_exalt"
    label: str = ""

    @abstractmethod
    def estimate(self, ctx: CraftingContext) -> dict[str, Any]:
        """计算单次命中概率、期望尝试次数、期望造价（Divine）与 P80 风险成本。"""
        raise NotImplementedError
