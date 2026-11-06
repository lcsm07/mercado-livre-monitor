import unittest
from types import SimpleNamespace

from src.models import ProductConfig
from src.search import OfferSearcher


class FakeClient:
    def __init__(self):
        self.item_batches = []
        self.user_batches = []

    def search_items(self, site, query, offset, limit):
        if offset == 0:
            return {"paging": {"total": 3}, "results": [{"id": "MLB1"}, {"id": "MLB2"}]}
        return {"paging": {"total": 3}, "results": [{"id": "MLB2"}, {"id": "MLB3"}]}

    def bulk_items(self, ids):
        self.item_batches.append(ids)
        return [
            {"id": item_id, "status_code": 200, "body": {
                "id": item_id,
                "title": "AMD Ryzen 7 5700X OEM AM4",
                "seller_id": 10,
                "price": 850,
                "condition": "new",
                "status": "active",
                "available_quantity": 1,
                "permalink": f"https://example/{item_id}",
            }}
            for item_id in ids
        ]

    def bulk_users(self, ids):
        self.user_batches.append(ids)
        return [{"id": "10", "status_code": 200, "body": {"id": 10, "nickname": "seller"}}]


class SearchTests(unittest.TestCase):
    def test_paginates_deduplicates_and_uses_bulk(self):
        client = FakeClient()
        settings = SimpleNamespace(
            site_id="MLB",
            search_page_size=2,
            max_pages_per_query=3,
            max_items_per_request=2,
        )
        product = ProductConfig("ryzen_7_5700x", "Ryzen", ("query",), 900)
        offers = OfferSearcher(client, settings).offers_for_product(product)
        self.assertEqual({offer.item_id for offer in offers}, {"MLB1", "MLB2", "MLB3"})
        self.assertEqual(len(client.item_batches), 2)
        self.assertTrue(all(offer.accepted for offer in offers))
        self.assertEqual(offers[0].seller.nickname, "seller")


if __name__ == "__main__":
    unittest.main()
