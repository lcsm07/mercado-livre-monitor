import unittest

from src.filters import evaluate_item
from src.models import ProductConfig


RYZEN = ProductConfig("ryzen_7_5700x", "Ryzen", ("Ryzen",), 900)
BOARD = ProductConfig("b550m_aorus_elite", "B550M", ("B550M",), 600)


def item(title: str, **extra):
    return {
        "id": "MLB1",
        "title": title,
        "status": "active",
        "condition": "new",
        "available_quantity": 1,
        "price": 850,
        **extra,
    }


class FilterTests(unittest.TestCase):
    def test_accepts_oem_5700x(self):
        decision = evaluate_item(RYZEN, item("AMD Ryzen 7 5700X OEM sem cooler AM4"))
        self.assertTrue(decision.accepted)

    def test_rejects_similar_cpu_models(self):
        for title in ("Ryzen 7 5700G", "Ryzen 7 5700GT", "Ryzen 7 5700X3D", "Ryzen 7 5700"):
            with self.subTest(title=title):
                self.assertFalse(evaluate_item(RYZEN, item(title)).accepted)

    def test_rejects_cpu_kit_and_non_new(self):
        self.assertFalse(evaluate_item(RYZEN, item("Ryzen 7 5700X + placa mãe")).accepted)
        self.assertFalse(evaluate_item(RYZEN, item("Ryzen 7 5700X", condition="used")).accepted)

    def test_accepts_ddr4_aorus_board(self):
        decision = evaluate_item(BOARD, item("Gigabyte B550M Aorus Elite DDR4 AM4"))
        self.assertTrue(decision.accepted)

    def test_rejects_other_aorus_boards(self):
        for title in (
            "Gigabyte B550 Aorus Elite DDR4",
            "Gigabyte B550M Aorus Elite AX DDR4",
            "Gigabyte B550M K DDR4",
            "Gigabyte B550M DS3H DDR4",
            "Gigabyte B550M Aorus Elite DDR5",
        ):
            with self.subTest(title=title):
                self.assertFalse(evaluate_item(BOARD, item(title)).accepted)


if __name__ == "__main__":
    unittest.main()
