import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.alerts import AlertManager
from src.history import History
from src.metrics import bundle_metrics, product_metrics
from src.models import Offer, ProductConfig, SellerInfo


RYZEN = ProductConfig("ryzen_7_5700x", "Ryzen 7 5700X", ("Ryzen",), 900, max_total=1450)
BOARD = ProductConfig("b550m_aorus_elite", "B550M Aorus Elite", ("B550M",), 600)


def offer(product_key: str, item_id: str, price: float, *, available=True) -> Offer:
    return Offer(
        product_key=product_key,
        item_id=item_id,
        title="Oferta válida",
        seller=SellerInfo(seller_id="123", nickname="loja", positive_percent=99.0, completed_transactions=100),
        catalog_product_id="MLB-PRODUTO",
        price=price,
        original_price=None,
        shipping_cost=None,
        condition="new",
        available_quantity=1 if available else 0,
        status="active" if available else "paused",
        url=f"https://www.mercadolivre.com.br/{item_id}",
        accepted=True,
        decision_reason="válido",
    )


class FakeTelegram:
    def __init__(self):
        self.messages = []

    def send_telegram(self, token, chat_id, text):
        self.messages.append(text)


def settings():
    return SimpleNamespace(
        dry_run=False,
        notify_enabled=True,
        telegram_bot_token="bot-token",
        telegram_chat_id="chat-id",
        relevant_drop_brl=5.0,
        exceptional_total=1350.0,
        coupons=(),
    )


class HistoryAlertTests(unittest.TestCase):
    def test_observation_is_unique_per_scan_and_history_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            history = History(Path(directory) / "monitor.db")
            first_scan, first_time = history.start_scan()
            first = offer(RYZEN.key, "MLB1", 950)
            history.record_offer(first_scan, first_time, first)
            history.record_offer(first_scan, first_time, first)
            second_scan, second_time = history.start_scan()
            second = offer(RYZEN.key, "MLB1", 940)
            history.record_offer(second_scan, second_time, second)
            self.assertEqual(len(history.current_offers(first_scan, RYZEN.key)), 1)
            self.assertEqual(history.lowest_historical_price(RYZEN.key), 940)
            self.assertEqual(history.previous_offer(second_scan, RYZEN.key, "MLB1").price, 950)
            self.assertEqual(product_metrics(history, second_scan, RYZEN).cheapest.price, 940)

    def test_repeated_price_is_suppressed_but_relevant_drop_alerts(self):
        with tempfile.TemporaryDirectory() as directory:
            history = History(Path(directory) / "monitor.db")
            telegram = FakeTelegram()
            manager = AlertManager(settings(), history, telegram)
            scan_one, time_one = history.start_scan()
            first = offer(RYZEN.key, "MLB1", 950)
            history.record_offer(scan_one, time_one, first)
            self.assertTrue(manager.consider_offer(scan_one, RYZEN, first))
            scan_two, time_two = history.start_scan()
            same = offer(RYZEN.key, "MLB1", 950)
            history.record_offer(scan_two, time_two, same)
            self.assertFalse(manager.consider_offer(scan_two, RYZEN, same))
            scan_three, time_three = history.start_scan()
            lower = offer(RYZEN.key, "MLB1", 940)
            history.record_offer(scan_three, time_three, lower)
            self.assertTrue(manager.consider_offer(scan_three, RYZEN, lower))
            self.assertEqual(len(telegram.messages), 2)

    def test_bundle_total_and_historical_total(self):
        with tempfile.TemporaryDirectory() as directory:
            history = History(Path(directory) / "monitor.db")
            scan, checked = history.start_scan()
            history.record_offers(scan, checked, [offer(RYZEN.key, "CPU", 850), offer(BOARD.key, "BOARD", 550)])
            bundle = bundle_metrics(history, scan, (RYZEN, BOARD))
            self.assertEqual(bundle.subtotal, 1400)
            self.assertEqual(bundle.historical_low, 1400)


if __name__ == "__main__":
    unittest.main()
