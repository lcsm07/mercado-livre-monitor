from __future__ import annotations

import logging
from typing import Any

from .api import ApiError, MeliClient
from .config import Settings
from .filters import evaluate_item
from .models import Decision, Offer, ProductConfig, SellerInfo, seller_from_payload

LOGGER = logging.getLogger(__name__)


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _integer(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


class OfferSearcher:
    def __init__(self, client: MeliClient, settings: Settings) -> None:
        self.client = client
        self.settings = settings

    def discover_ids(self, product: ProductConfig) -> list[str]:
        ids: set[str] = set(product.reference_item_ids)
        for query in product.queries:
            for page in range(self.settings.max_pages_per_query):
                offset = page * self.settings.search_page_size
                response = self.client.search_items(self.settings.site_id, query, offset, self.settings.search_page_size)
                results = response.get("results") or []
                before = len(ids)
                for result in results:
                    if isinstance(result, dict) and result.get("id"):
                        ids.add(str(result["id"]))
                    elif isinstance(result, str):
                        ids.add(result)
                paging = response.get("paging") or {}
                total = paging.get("total")
                if not results or len(results) < self.settings.search_page_size:
                    break
                if isinstance(total, int) and offset + len(results) >= total:
                    break
                # A response that repeats an entire page should not create a
                # request loop when the API is under load.
                if len(ids) == before and page:
                    break
        return sorted(ids)

    def _bulk_item_map(self, item_ids: list[str]) -> dict[str, dict[str, Any]]:
        items: dict[str, dict[str, Any]] = {}
        for start in range(0, len(item_ids), self.settings.max_items_per_request):
            batch = item_ids[start : start + self.settings.max_items_per_request]
            for entry in self.client.bulk_items(batch):
                item_id = str(entry.get("id") or (entry.get("body") or {}).get("id") or "")
                status_code = entry.get("status_code", entry.get("code"))
                body = entry.get("body")
                if item_id and status_code in (None, 200) and isinstance(body, dict):
                    items[item_id] = body
        return items

    def _bulk_seller_map(self, seller_ids: list[str]) -> dict[str, SellerInfo]:
        sellers: dict[str, SellerInfo] = {}
        for start in range(0, len(seller_ids), self.settings.max_items_per_request):
            batch = seller_ids[start : start + self.settings.max_items_per_request]
            try:
                entries = self.client.bulk_users(batch)
            except ApiError:
                LOGGER.warning("não foi possível consultar reputações de vendedores neste ciclo")
                return sellers
            for entry in entries:
                seller_id = str(entry.get("id") or "")
                body = entry.get("body")
                if seller_id and isinstance(body, dict) and entry.get("status_code", entry.get("code", 200)) in (None, 200):
                    sellers[seller_id] = seller_from_payload(body, seller_id)
        return sellers

    def offers_for_product(self, product: ProductConfig) -> list[Offer]:
        item_ids = self.discover_ids(product)
        if not item_ids:
            return []
        item_map = self._bulk_item_map(item_ids)
        seller_ids = sorted(
            {str(item.get("seller_id")) for item in item_map.values() if item.get("seller_id") is not None}
        )
        seller_map = self._bulk_seller_map(seller_ids)
        offers: list[Offer] = []
        for item_id in item_ids:
            item = item_map.get(item_id)
            if item is None:
                offers.append(
                    Offer(
                        product_key=product.key,
                        item_id=item_id,
                        title="",
                        seller=SellerInfo(),
                        catalog_product_id=None,
                        price=None,
                        original_price=None,
                        shipping_cost=None,
                        condition=None,
                        available_quantity=None,
                        status=None,
                        url=None,
                        accepted=False,
                        decision_reason="detalhes do anúncio não retornados pelo /items/bulk",
                    )
                )
                continue
            seller_id = str(item.get("seller_id")) if item.get("seller_id") is not None else None
            seller = seller_map.get(seller_id or "", seller_from_payload(None, seller_id))
            decision = evaluate_item(product, item)
            offers.append(self._offer_from_item(product, item, seller, decision))
        return offers

    @staticmethod
    def _offer_from_item(
        product: ProductConfig, item: dict[str, Any], seller: SellerInfo, decision: Decision
    ) -> Offer:
        shipping = item.get("shipping") or {}
        return Offer(
            product_key=product.key,
            item_id=str(item.get("id", "")),
            title=str(item.get("title", "")),
            seller=seller,
            catalog_product_id=item.get("catalog_product_id"),
            price=_number(item.get("price")),
            original_price=_number(item.get("original_price")),
            shipping_cost=_number(shipping.get("cost")),
            condition=item.get("condition"),
            available_quantity=_integer(item.get("available_quantity")),
            status=item.get("status"),
            url=item.get("permalink") or f"https://www.mercadolivre.com.br/{item.get('id', '')}",
            accepted=decision.accepted,
            decision_reason=decision.reason,
        )
