from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Sequence

from .models import Offer, SellerInfo


SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    checked_at TEXT NOT NULL,
    finished_at TEXT,
    error_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id INTEGER NOT NULL REFERENCES scans(id),
    observed_at TEXT NOT NULL,
    product_key TEXT NOT NULL,
    item_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    seller_id TEXT,
    seller_nickname TEXT,
    seller_level TEXT,
    seller_positive_percent REAL,
    seller_completed_transactions INTEGER,
    catalog_product_id TEXT,
    price REAL,
    original_price REAL,
    shipping_cost REAL,
    condition TEXT,
    available_quantity INTEGER,
    status TEXT,
    url TEXT,
    accepted INTEGER NOT NULL,
    decision_reason TEXT NOT NULL,
    UNIQUE(scan_id, product_key, item_id)
);
CREATE INDEX IF NOT EXISTS idx_observations_product_item_time
    ON observations(product_key, item_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_observations_product_time
    ON observations(product_key, observed_at DESC);
CREATE TABLE IF NOT EXISTS notification_state (
    product_key TEXT NOT NULL,
    item_id TEXT NOT NULL,
    last_notified_price REAL,
    last_notified_at TEXT,
    last_notified_available INTEGER,
    PRIMARY KEY(product_key, item_id)
);
CREATE TABLE IF NOT EXISTS bundle_notification_state (
    bundle_key TEXT PRIMARY KEY,
    last_notified_total REAL,
    last_notified_at TEXT
);
CREATE TABLE IF NOT EXISTS coupons (
    code TEXT PRIMARY KEY,
    minimum_subtotal REAL NOT NULL,
    discount REAL NOT NULL DEFAULT 0,
    expires_at TEXT
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class History:
    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.executescript(SCHEMA)
        self.path.chmod(0o600)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def start_scan(self) -> tuple[int, str]:
        checked_at = utc_now()
        with self.connection() as connection:
            cursor = connection.execute("INSERT INTO scans(checked_at) VALUES (?)", (checked_at,))
            return int(cursor.lastrowid), checked_at

    def finish_scan(self, scan_id: int, error_count: int = 0) -> None:
        with self.connection() as connection:
            connection.execute(
                "UPDATE scans SET finished_at = ?, error_count = ? WHERE id = ?",
                (utc_now(), error_count, scan_id),
            )

    def record_offer(self, scan_id: int, observed_at: str, offer: Offer) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO observations(
                    scan_id, observed_at, product_key, item_id, title,
                    seller_id, seller_nickname, seller_level, seller_positive_percent,
                    seller_completed_transactions, catalog_product_id, price, original_price,
                    shipping_cost, condition, available_quantity, status, url, accepted,
                    decision_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scan_id,
                    observed_at,
                    offer.product_key,
                    offer.item_id,
                    offer.title,
                    offer.seller.seller_id,
                    offer.seller.nickname,
                    offer.seller.level_id,
                    offer.seller.positive_percent,
                    offer.seller.completed_transactions,
                    offer.catalog_product_id,
                    offer.price,
                    offer.original_price,
                    offer.shipping_cost,
                    offer.condition,
                    offer.available_quantity,
                    offer.status,
                    offer.url,
                    int(offer.accepted),
                    offer.decision_reason,
                ),
            )

    def record_offers(self, scan_id: int, observed_at: str, offers: Sequence[Offer]) -> None:
        with self.connection() as connection:
            connection.executemany(
                """
                INSERT OR IGNORE INTO observations(
                    scan_id, observed_at, product_key, item_id, title,
                    seller_id, seller_nickname, seller_level, seller_positive_percent,
                    seller_completed_transactions, catalog_product_id, price, original_price,
                    shipping_cost, condition, available_quantity, status, url, accepted,
                    decision_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        scan_id,
                        observed_at,
                        offer.product_key,
                        offer.item_id,
                        offer.title,
                        offer.seller.seller_id,
                        offer.seller.nickname,
                        offer.seller.level_id,
                        offer.seller.positive_percent,
                        offer.seller.completed_transactions,
                        offer.catalog_product_id,
                        offer.price,
                        offer.original_price,
                        offer.shipping_cost,
                        offer.condition,
                        offer.available_quantity,
                        offer.status,
                        offer.url,
                        int(offer.accepted),
                        offer.decision_reason,
                    )
                    for offer in offers
                ],
            )

    @staticmethod
    def _offer_from_row(row: sqlite3.Row) -> Offer:
        return Offer(
            product_key=row["product_key"],
            item_id=row["item_id"],
            title=row["title"],
            seller=SellerInfo(
                seller_id=row["seller_id"],
                nickname=row["seller_nickname"],
                level_id=row["seller_level"],
                positive_percent=row["seller_positive_percent"],
                completed_transactions=row["seller_completed_transactions"],
            ),
            catalog_product_id=row["catalog_product_id"],
            price=row["price"],
            original_price=row["original_price"],
            shipping_cost=row["shipping_cost"],
            condition=row["condition"],
            available_quantity=row["available_quantity"],
            status=row["status"],
            url=row["url"],
            accepted=bool(row["accepted"]),
            decision_reason=row["decision_reason"],
            observed_at=row["observed_at"],
        )

    def previous_offer(self, scan_id: int, product_key: str, item_id: str) -> Offer | None:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM observations
                WHERE product_key = ? AND item_id = ? AND scan_id < ?
                ORDER BY scan_id DESC LIMIT 1
                """,
                (product_key, item_id, scan_id),
            ).fetchone()
        return self._offer_from_row(row) if row else None

    def current_offers(self, scan_id: int, product_key: str) -> list[Offer]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM observations
                WHERE scan_id = ? AND product_key = ? AND accepted = 1 AND price IS NOT NULL
                ORDER BY price ASC
                """,
                (scan_id, product_key),
            ).fetchall()
        return [self._offer_from_row(row) for row in rows]

    def lowest_historical_price(self, product_key: str) -> float | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT MIN(price) AS lowest FROM observations WHERE product_key = ? AND accepted = 1",
                (product_key,),
            ).fetchone()
        return row["lowest"] if row and row["lowest"] is not None else None

    def historical_bundle_low(self, product_keys: Sequence[str]) -> float | None:
        if not product_keys:
            return None
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT scan_id, product_key, MIN(price) AS price
                FROM observations
                WHERE accepted = 1 AND price IS NOT NULL AND product_key IN ({})
                GROUP BY scan_id, product_key
                """.format(",".join("?" for _ in product_keys)),
                tuple(product_keys),
            ).fetchall()
        grouped: dict[int, dict[str, float]] = {}
        for row in rows:
            grouped.setdefault(int(row["scan_id"]), {})[row["product_key"]] = float(row["price"])
        totals = [sum(values[key] for key in product_keys) for values in grouped.values() if all(key in values for key in product_keys)]
        return min(totals) if totals else None

    def notification_state(self, product_key: str, item_id: str) -> sqlite3.Row | None:
        with self.connection() as connection:
            return connection.execute(
                "SELECT * FROM notification_state WHERE product_key = ? AND item_id = ?",
                (product_key, item_id),
            ).fetchone()

    def mark_notified(self, offer: Offer) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO notification_state(product_key, item_id, last_notified_price, last_notified_at, last_notified_available)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(product_key, item_id) DO UPDATE SET
                    last_notified_price = excluded.last_notified_price,
                    last_notified_at = excluded.last_notified_at,
                    last_notified_available = excluded.last_notified_available
                """,
                (offer.product_key, offer.item_id, offer.price, utc_now(), int(offer.available)),
            )

    def bundle_notification_state(self, bundle_key: str = "default") -> sqlite3.Row | None:
        with self.connection() as connection:
            return connection.execute(
                "SELECT * FROM bundle_notification_state WHERE bundle_key = ?", (bundle_key,)
            ).fetchone()

    def mark_bundle_notified(self, total: float, bundle_key: str = "default") -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO bundle_notification_state(bundle_key, last_notified_total, last_notified_at)
                VALUES (?, ?, ?)
                ON CONFLICT(bundle_key) DO UPDATE SET
                    last_notified_total = excluded.last_notified_total,
                    last_notified_at = excluded.last_notified_at
                """,
                (bundle_key, total, utc_now()),
            )

    def save_coupon(self, code: str, minimum_subtotal: float, discount: float = 0.0, expires_at: str | None = None) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO coupons(code, minimum_subtotal, discount, expires_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET minimum_subtotal = excluded.minimum_subtotal,
                    discount = excluded.discount, expires_at = excluded.expires_at
                """,
                (code, minimum_subtotal, discount, expires_at),
            )

    def coupons(self) -> list[sqlite3.Row]:
        with self.connection() as connection:
            return connection.execute("SELECT * FROM coupons ORDER BY minimum_subtotal").fetchall()
