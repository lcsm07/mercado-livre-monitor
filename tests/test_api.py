import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.api import HttpResult, MeliClient
from src.config import Settings


class FakeTransport:
    def __init__(self):
        self.calls = []

    def request(self, method, url, headers, body, timeout):
        self.calls.append((method, url, headers, body))
        if url.endswith("/oauth/token"):
            return HttpResult(200, {}, json.dumps({
                "access_token": "access-test",
                "refresh_token": "refresh-rotated",
                "expires_in": 3600,
            }).encode())
        if len(self.calls) == 2:
            return HttpResult(429, {"Retry-After": "0"}, b"{}")
        return HttpResult(200, {}, json.dumps({"paging": {"total": 0}, "results": []}).encode())


def make_settings(env_path: Path) -> Settings:
    return Settings(
        site_id="MLB",
        api_base_url="https://api.example",
        oauth_base_url="https://auth.example",
        client_id="client",
        client_secret="secret",
        refresh_token="refresh-old",
        telegram_bot_token="",
        telegram_chat_id="",
        config_path=env_path.parent / "config.toml",
        env_path=env_path,
        database_path=env_path.parent / "monitor.db",
        products=(),
        coupons=(),
        request_spacing_seconds=0,
        request_timeout_seconds=1,
        max_retries=2,
        backoff_base_seconds=0,
        search_page_size=50,
        max_pages_per_query=1,
        max_items_per_request=20,
        relevant_drop_brl=5,
        exceptional_total=1350,
        notify_enabled=False,
        dry_run=True,
    )


class ApiTests(unittest.TestCase):
    def test_rotates_refresh_token_and_retries_429(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("ML_REFRESH_TOKEN=refresh-old\n", encoding="utf-8")
            env_path.chmod(0o600)
            transport = FakeTransport()
            sleeps = []
            client = MeliClient(make_settings(env_path), transport=transport, sleeper=sleeps.append)
            response = client.search_items("MLB", "Ryzen 7 5700X", 0, 50)
            self.assertEqual(response["paging"]["total"], 0)
            self.assertEqual(len(transport.calls), 3)
            self.assertEqual(transport.calls[-1][2]["Authorization"], "Bearer access-test")
            self.assertIn("refresh-rotated", env_path.read_text(encoding="utf-8"))
            self.assertEqual(sleeps, [0])

            # O segundo processo reaproveita o access token válido e não
            # consome o refresh token rotativo novamente.
            second_transport = FakeTransport()
            second_client = MeliClient(make_settings(env_path), transport=second_transport, sleeper=sleeps.append)
            second_client.search_items("MLB", "Ryzen 7 5700X", 0, 50)
            self.assertEqual(len(second_transport.calls), 1)
            self.assertNotIn("/oauth/token", second_transport.calls[0][1])


if __name__ == "__main__":
    unittest.main()
