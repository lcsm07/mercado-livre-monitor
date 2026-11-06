from __future__ import annotations

from .config import Settings
from .history import History
from .models import BundleMetrics, Offer, ProductConfig, ProductMetrics


def product_metrics(history: History, scan_id: int, product: ProductConfig) -> ProductMetrics:
    offers = tuple(history.current_offers(scan_id, product.key))
    cheapest = offers[0] if offers else None
    most_reliable = None
    if offers:
        most_reliable = max(
            offers,
            key=lambda offer: (
                offer.seller.positive_percent if offer.seller.positive_percent is not None else -1.0,
                offer.seller.completed_transactions if offer.seller.completed_transactions is not None else -1,
                -(offer.price or float("inf")),
            ),
        )
    return ProductMetrics(
        product_key=product.key,
        current_offers=offers,
        cheapest=cheapest,
        most_reliable=most_reliable,
        historical_low=history.lowest_historical_price(product.key),
    )


def bundle_metrics(history: History, scan_id: int, products: tuple[ProductConfig, ...]) -> BundleMetrics:
    offers: dict[str, Offer] = {}
    for product in products:
        current = history.current_offers(scan_id, product.key)
        if current:
            offers[product.key] = current[0]
    subtotal = sum(offer.price for offer in offers.values() if offer.price is not None) if len(offers) == len(products) else None
    historical_low = history.historical_bundle_low([product.key for product in products])
    return BundleMetrics(offers=offers, subtotal=subtotal, historical_low=historical_low)


def bundle_limit(products: tuple[ProductConfig, ...]) -> float:
    limits = [product.max_total for product in products if product.max_total is not None]
    return min(limits) if limits else 1450.0


def format_brl(value: float | None) -> str:
    if value is None:
        return "não informado"
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

