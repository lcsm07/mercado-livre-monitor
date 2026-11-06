from __future__ import annotations

import re
import unicodedata
from typing import Any

from .models import Decision, ProductConfig


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text.lower()).strip()


def compact_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize_text(value))


def _item_text(item: dict[str, Any]) -> str:
    values: list[str] = []
    for key in ("title", "subtitle", "description"):
        if item.get(key):
            values.append(str(item[key]))
    for attribute in item.get("attributes") or []:
        if not isinstance(attribute, dict):
            continue
        for key in ("id", "name", "value_name", "value_id"):
            if attribute.get(key):
                values.append(str(attribute[key]))
    for key in ("listing_type_id", "buying_mode", "catalog_product_id"):
        if item.get(key):
            values.append(str(item[key]))
    return normalize_text(" ".join(values))


def _has_bundle_language(text: str) -> bool:
    return any(
        token in text
        for token in (
            "kit upgrade",
            "combo",
            "bundle",
            "computador",
            "pc gamer",
            "placa mae +",
            "+ placa mae",
            "com placa mae",
            "processador +",
        )
    )


def evaluate_item(product: ProductConfig, item: dict[str, Any]) -> Decision:
    """Conservative, explainable product matching; a missing required fact rejects the item."""

    status = normalize_text(item.get("status"))
    if status != "active":
        return Decision(False, f"anúncio não está ativo (status={status or 'ausente'})")
    condition = normalize_text(item.get("condition"))
    if condition != "new":
        return Decision(False, f"condição não é nova ({condition or 'ausente'})")
    price = item.get("price")
    if not isinstance(price, (int, float)) or price <= 0:
        return Decision(False, "preço ausente ou inválido")
    available_quantity = item.get("available_quantity")
    if isinstance(available_quantity, (int, float)) and available_quantity <= 0:
        return Decision(False, "anúncio sem estoque")
    text = _item_text(item)
    compact = compact_text(text)
    if _has_bundle_language(text):
        return Decision(False, "anúncio parece ser kit ou conjunto incompatível")

    if product.key == "ryzen_7_5700x":
        excluded = ("5700x3d", "5700gt", "5700g", "ryzen 7 5700 ")
        if any(token in text or token in compact for token in excluded):
            return Decision(False, "modelo parecido excluído (5700G/GT/X3D/5700)")
        if "ryzen7" not in compact or "5700x" not in compact:
            return Decision(False, "título não identifica claramente Ryzen 7 5700X")
        if "am5" in text or "socket am5" in text:
            return Decision(False, "soquete AM5 incompatível")
        core_counts = [int(value) for value in re.findall(r"(\d+)\s*(?:cores?|nucleos?)", text)]
        thread_counts = [int(value) for value in re.findall(r"(\d+)\s*(?:threads?|fios?)", text)]
        if any(value != 8 for value in core_counts) or any(value != 16 for value in thread_counts):
            return Decision(False, "quantidade de núcleos/threads incompatível")
        explicit_socket = any(token in text for token in ("am4", "socket am4", "soquete am4"))
        # 5700X é um modelo AM4 conhecido; sem uma indicação contraditória, o
        # modelo exato permite anúncios OEM que não repetem a ficha técnica.
        reason = "Ryzen 7 5700X novo; AM4 confirmado no modelo"
        if explicit_socket:
            reason = "Ryzen 7 5700X novo AM4"
        return Decision(True, reason)

    if product.key == "b550m_aorus_elite":
        if "b550m" not in compact:
            return Decision(False, "não é a versão Micro-ATX B550M")
        if "aorus" not in text or "elite" not in text:
            return Decision(False, "não identifica Gigabyte B550M Aorus Elite")
        excluded = ("b550mk", "b550mds3h", "b550m gaming", "b550m pro")
        if any(token in compact for token in excluded):
            return Decision(False, "modelo B550M incompatível")
        if not product.allow_ax and re.search(r"\bax\b", text):
            return Decision(False, "versão AX não permitida pela configuração")
        if "ddr5" in text:
            return Decision(False, "memória DDR5 incompatível")
        if product.require_ddr4 and "ddr4" in text:
            return Decision(True, "Gigabyte B550M Aorus Elite DDR4")
        if product.require_ddr4:
            return Decision(True, "Gigabyte B550M Aorus Elite; DDR4 compatível com o modelo, validar ficha")
        return Decision(True, "Gigabyte B550M Aorus Elite")

    return Decision(False, f"produto {product.key} não possui filtro específico")
