from __future__ import annotations

import logging

from .api import ApiError, MeliClient
from .config import ConfigError, Settings
from .history import History
from .metrics import bundle_limit, format_brl
from .models import BundleMetrics, Coupon, Offer, ProductConfig

LOGGER = logging.getLogger(__name__)


def _seller_text(offer: Offer) -> str:
    return offer.seller.nickname or offer.seller.seller_id or "não informado"


def _shipping_text(offer: Offer) -> str:
    if offer.shipping_cost is None:
        return "não informado pela API; confirme no carrinho"
    return format_brl(offer.shipping_cost)


def offer_message(
    product: ProductConfig,
    offer: Offer,
    previous: Offer | None,
    historical_low: float | None,
    reason: str,
) -> str:
    previous_price = previous.price if previous and previous.price is not None else None
    label = "oferta nova" if previous is None else "queda/reabertura"
    return "\n".join(
        [
            f"🔔 {product.name}: {label}",
            f"Preço: {format_brl(offer.price)} (anterior: {format_brl(previous_price)})",
            f"Menor histórico: {format_brl(historical_low)}",
            f"Vendedor: {_seller_text(offer)}",
            f"Reputação: {offer.seller.reputation_label}",
            f"Frete: {_shipping_text(offer)}",
            f"Motivo: {reason}",
            f"Anúncio: {offer.title or offer.item_id}",
            f"Link: {offer.url or 'não informado'}",
            "Preço público; valide frete, cupom e total no carrinho. O monitor não faz login.",
        ]
    )


def bundle_message(
    products: tuple[ProductConfig, ...],
    bundle: BundleMetrics,
    historical_low: float | None,
    limit: float,
    exceptional_total: float,
    coupon_lines: list[str],
) -> str:
    lines = [
        "🛒 Oferta de conjunto Ryzen + placa-mãe",
        f"Total estimado: {format_brl(bundle.subtotal)} (limite: {format_brl(limit)})",
        f"Menor total histórico: {format_brl(historical_low)}",
    ]
    if bundle.subtotal is not None and bundle.subtotal <= exceptional_total:
        lines.append(f"⭐ Excelente negócio: total abaixo de {format_brl(exceptional_total)}")
    for product in products:
        offer = bundle.offers.get(product.key)
        if offer:
            lines.extend(
                [
                    f"{product.name}: {format_brl(offer.price)} — {_seller_text(offer)}",
                    f"  {offer.url or 'link não informado'}",
                ]
            )
    if coupon_lines:
        lines.append("Possíveis cupons para testar manualmente:")
        lines.extend(f"- {line}" for line in coupon_lines)
    lines.append("Total é preço público estimado; confirme frete e cupom no carrinho.")
    return "\n".join(lines)


class AlertManager:
    def __init__(self, settings: Settings, history: History, client: MeliClient) -> None:
        self.settings = settings
        self.history = history
        self.client = client

    def _send(self, text: str) -> bool:
        if self.settings.dry_run or not self.settings.notify_enabled:
            LOGGER.info("alerta não enviado (modo observação): %s", text.splitlines()[0])
            return False
        if not self.settings.telegram_bot_token or not self.settings.telegram_chat_id:
            raise ConfigError("alertas habilitados, mas Telegram não está configurado")
        self.client.send_telegram(self.settings.telegram_bot_token, self.settings.telegram_chat_id, text)
        return True

    def consider_offer(self, scan_id: int, product: ProductConfig, offer: Offer) -> bool:
        if not offer.accepted or offer.price is None:
            return False
        previous = self.history.previous_offer(scan_id, product.key, offer.item_id)
        state = self.history.notification_state(product.key, offer.item_id)
        price_drop = (
            previous is not None
            and previous.accepted
            and previous.price is not None
            and previous.price - offer.price >= self.settings.relevant_drop_brl
        )
        crossed_target = (
            offer.price <= product.target_price
            and (previous is None or previous.price is None or previous.price > product.target_price)
        )
        reopened = previous is not None and not previous.available and offer.available
        first_seen = state is None
        should_alert = (first_seen and product.alert_new_offers) or price_drop or crossed_target or reopened
        if state is not None and state["last_notified_price"] is not None:
            if offer.price >= float(state["last_notified_price"]) - self.settings.relevant_drop_brl and not reopened:
                should_alert = False
        if not should_alert:
            return False
        historical_low = self.history.lowest_historical_price(product.key)
        reason_parts = []
        if first_seen:
            reason_parts.append("anúncio novo")
        if price_drop:
            reason_parts.append(f"queda de {format_brl(previous.price - offer.price)}")
        if crossed_target:
            reason_parts.append(f"atingiu o alvo de {format_brl(product.target_price)}")
        if reopened:
            reason_parts.append("estoque reaberto")
        text = offer_message(product, offer, previous, historical_low, "; ".join(reason_parts))
        try:
            sent = self._send(text)
        except (ApiError, ConfigError):
            LOGGER.exception("falha ao enviar alerta do anúncio %s; ele continuará elegível", offer.item_id)
            return False
        if sent:
            self.history.mark_notified(offer)
        return sent

    def consider_bundle(
        self,
        products: tuple[ProductConfig, ...],
        bundle: BundleMetrics,
    ) -> bool:
        if bundle.subtotal is None:
            return False
        limit = bundle_limit(products)
        if bundle.subtotal > limit:
            return False
        state = self.history.bundle_notification_state()
        last_total = state["last_notified_total"] if state else None
        is_new = last_total is None
        relevant_drop = last_total is not None and float(last_total) - bundle.subtotal >= self.settings.relevant_drop_brl
        exceptional_crossed = bundle.subtotal <= self.settings.exceptional_total and (
            last_total is None or float(last_total) > self.settings.exceptional_total
        )
        if not (is_new or relevant_drop or exceptional_crossed):
            return False
        configured_coupons = list(self.settings.coupons)
        known_codes = {coupon.code for coupon in configured_coupons}
        configured_coupons.extend(
            Coupon(
                code=row["code"],
                minimum_subtotal=row["minimum_subtotal"],
                discount=row["discount"],
                expires_at=row["expires_at"],
            )
            for row in self.history.coupons()
            if row["code"] not in known_codes
        )
        coupon_lines = []
        for coupon in configured_coupons:
            if bundle.subtotal >= coupon.minimum_subtotal:
                discount = f"; desconto indicado {format_brl(coupon.discount)}" if coupon.discount else ""
                coupon_lines.append(f"{coupon.code} (mínimo {format_brl(coupon.minimum_subtotal)}{discount})")
        text = bundle_message(
            products,
            bundle,
            bundle.historical_low,
            limit,
            self.settings.exceptional_total,
            coupon_lines,
        )
        try:
            sent = self._send(text)
        except (ApiError, ConfigError):
            LOGGER.exception("falha ao enviar alerta do conjunto; ele continuará elegível")
            return False
        if sent:
            self.history.mark_bundle_notified(bundle.subtotal)
        return sent
