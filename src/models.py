from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProductConfig:
    key: str
    name: str
    queries: tuple[str, ...]
    target_price: float
    max_total: float | None = None
    reference_item_ids: tuple[str, ...] = ()
    allow_ax: bool = False
    require_ddr4: bool = True
    alert_new_offers: bool = True


@dataclass(frozen=True)
class Coupon:
    code: str
    minimum_subtotal: float
    discount: float = 0.0
    expires_at: str | None = None


@dataclass(frozen=True)
class SellerInfo:
    seller_id: str | None = None
    nickname: str | None = None
    level_id: str | None = None
    positive_percent: float | None = None
    completed_transactions: int | None = None

    @property
    def reputation_label(self) -> str:
        parts: list[str] = []
        if self.level_id:
            parts.append(self.level_id)
        if self.positive_percent is not None:
            parts.append(f"{self.positive_percent:.0f}% positivas")
        if self.completed_transactions is not None:
            parts.append(f"{self.completed_transactions} vendas concluídas")
        return ", ".join(parts) if parts else "não disponível"


@dataclass(frozen=True)
class Offer:
    product_key: str
    item_id: str
    title: str
    seller: SellerInfo
    catalog_product_id: str | None
    price: float | None
    original_price: float | None
    shipping_cost: float | None
    condition: str | None
    available_quantity: int | None
    status: str | None
    url: str | None
    accepted: bool
    decision_reason: str
    observed_at: str | None = None

    @property
    def available(self) -> bool:
        return self.status == "active" and (
            self.available_quantity is None or self.available_quantity > 0
        )


@dataclass(frozen=True)
class Decision:
    accepted: bool
    reason: str


@dataclass(frozen=True)
class ProductMetrics:
    product_key: str
    current_offers: tuple[Offer, ...]
    cheapest: Offer | None
    most_reliable: Offer | None
    historical_low: float | None


@dataclass(frozen=True)
class BundleMetrics:
    offers: dict[str, Offer]
    subtotal: float | None
    historical_low: float | None


def seller_from_payload(payload: dict[str, Any] | None, seller_id: str | None = None) -> SellerInfo:
    """Extract only public reputation fields, keeping the rest of the API response out of logs."""

    payload = payload or {}
    reputation = payload.get("seller_reputation") or {}
    transactions = reputation.get("transactions") or {}
    ratings = transactions.get("ratings") or {}
    positive = ratings.get("positive")
    total_ratings = sum(
        value for value in (ratings.get("positive"), ratings.get("neutral"), ratings.get("negative"))
        if isinstance(value, (int, float))
    )
    positive_percent = None
    if isinstance(positive, (int, float)) and total_ratings:
        positive_percent = float(positive) * 100 / total_ratings
    completed = transactions.get("completed")
    return SellerInfo(
        seller_id=str(payload.get("id") or seller_id) if (payload.get("id") or seller_id) else None,
        nickname=payload.get("nickname"),
        level_id=reputation.get("level_id"),
        positive_percent=positive_percent,
        completed_transactions=int(completed) if isinstance(completed, (int, float)) else None,
    )
