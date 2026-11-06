from __future__ import annotations

import json
import logging
import secrets
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from .config import ConfigError, Settings, update_refresh_token

LOGGER = logging.getLogger(__name__)


class ApiError(RuntimeError):
    def __init__(self, status: int | None, path: str, message: str = "falha na API") -> None:
        self.status = status
        self.path = path
        self.retryable = status == 429 or status is None or status >= 500
        super().__init__(f"{message} ({status or 'rede'}) em {path}")


@dataclass(frozen=True)
class HttpResult:
    status: int
    headers: Mapping[str, str]
    body: bytes


class UrllibTransport:
    def request(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float
    ) -> HttpResult:
        request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return HttpResult(response.status, dict(response.headers.items()), response.read())
        except urllib.error.HTTPError as exc:
            return HttpResult(exc.code, dict(exc.headers.items()), exc.read())
        except (urllib.error.URLError, TimeoutError, OSError):
            # Não propague a URL: ela pode conter o token do bot do Telegram.
            raise ApiError(None, urllib.parse.urlsplit(url).path or "/", "falha de rede") from None


def _retry_after(headers: Mapping[str, str]) -> float | None:
    value = headers.get("Retry-After") or headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            return max(0.0, date.timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            return None


class TokenProvider:
    """Obtain short-lived access tokens and rotate the one-time refresh token."""

    def __init__(self, settings: Settings, send: Callable[..., dict[str, Any]]) -> None:
        self.settings = settings
        self._send = send
        self._access_token: str | None = None
        self._expires_at = 0.0
        self._refresh_token = settings.refresh_token
        self._load_cached_access_token()

    def _cache_connection(self) -> sqlite3.Connection:
        self.settings.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.settings.database_path)
        connection.execute(
            "CREATE TABLE IF NOT EXISTS oauth_cache (id INTEGER PRIMARY KEY CHECK(id = 1), access_token TEXT NOT NULL, expires_at REAL NOT NULL)"
        )
        return connection

    def _load_cached_access_token(self) -> None:
        try:
            connection = self._cache_connection()
            row = connection.execute("SELECT access_token, expires_at FROM oauth_cache WHERE id = 1").fetchone()
            connection.commit()
            connection.close()
        except sqlite3.Error:
            return
        try:
            if row and isinstance(row[0], str) and time.time() < float(row[1]) - 60:
                self._access_token = row[0]
                self._expires_at = float(row[1])
        except (TypeError, ValueError):
            self._access_token = None
            self._expires_at = 0.0

    def _store_cached_access_token(self) -> None:
        try:
            connection = self._cache_connection()
            connection.execute(
                "INSERT INTO oauth_cache(id, access_token, expires_at) VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET access_token = excluded.access_token, expires_at = excluded.expires_at",
                (self._access_token, self._expires_at),
            )
            connection.commit()
            connection.close()
        except sqlite3.Error:
            LOGGER.warning("não foi possível persistir o cache temporário do access token")

    def _clear_cached_access_token(self) -> None:
        try:
            connection = self._cache_connection()
            connection.execute("DELETE FROM oauth_cache WHERE id = 1")
            connection.commit()
            connection.close()
        except sqlite3.Error:
            pass

    def invalidate(self) -> None:
        self._access_token = None
        self._expires_at = 0.0
        self._clear_cached_access_token()

    def get(self) -> str:
        if self._access_token and time.time() < self._expires_at - 60:
            return self._access_token
        payload = self._send(
            "POST",
            "/oauth/token",
            form={
                "grant_type": "refresh_token",
                "client_id": self.settings.client_id,
                "client_secret": self.settings.client_secret,
                "refresh_token": self._refresh_token,
            },
            authenticated=False,
        )
        access_token = payload.get("access_token")
        new_refresh_token = payload.get("refresh_token")
        if not isinstance(access_token, str) or not access_token:
            raise ConfigError("resposta OAuth sem access_token; faça uma nova autorização")
        if not isinstance(new_refresh_token, str) or not new_refresh_token:
            raise ConfigError("resposta OAuth sem refresh_token rotativo")
        expires_in = payload.get("expires_in", 21600)
        try:
            expires_seconds = max(60, int(expires_in))
        except (TypeError, ValueError) as exc:
            raise ConfigError("resposta OAuth com expires_in inválido") from exc
        update_refresh_token(self.settings.env_path, new_refresh_token)
        self._refresh_token = new_refresh_token
        self._access_token = access_token
        self._expires_at = time.time() + expires_seconds
        self._store_cached_access_token()
        return access_token


class MeliClient:
    def __init__(
        self,
        settings: Settings,
        transport: UrllibTransport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.transport = transport or UrllibTransport()
        self.sleeper = sleeper
        self._last_request_at = 0.0
        self.tokens = TokenProvider(settings, self._send_json)

    def _wait_for_spacing(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        wait = self.settings.request_spacing_seconds - elapsed
        if wait > 0:
            self.sleeper(wait)

    def _send_json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
        form: Mapping[str, Any] | None = None,
        authenticated: bool = True,
        base_url: str | None = None,
        _allow_401_retry: bool = True,
    ) -> dict[str, Any] | list[Any]:
        if not path.startswith("/"):
            path = "/" + path
        query = urllib.parse.urlencode(
            [(key, value) for key, value in (params or {}).items() if value is not None],
            doseq=True,
        )
        url = f"{(base_url or self.settings.api_base_url).rstrip('/')}{path}"
        if query:
            url += f"?{query}"
        headers = {"Accept": "application/json", "User-Agent": "mercado-livre-monitor/0.1"}
        data: bytes | None = None
        if form is not None:
            data = urllib.parse.urlencode(form).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if authenticated:
            headers["Authorization"] = f"Bearer {self.tokens.get()}"

        last_error: ApiError | None = None
        for attempt in range(self.settings.max_retries + 1):
            self._wait_for_spacing()
            self._last_request_at = time.monotonic()
            try:
                result = self.transport.request(method, url, headers, data, self.settings.request_timeout_seconds)
            except ApiError as exc:
                last_error = exc
                if not exc.retryable or attempt >= self.settings.max_retries:
                    raise
                delay = self.settings.backoff_base_seconds * (2**attempt)
                LOGGER.warning("falha temporária de rede; nova tentativa em %.1fs", delay)
                self.sleeper(delay)
                continue
            if result.status == 401 and authenticated and _allow_401_retry:
                self.tokens.invalidate()
                return self._send_json(
                    method,
                    path,
                    params=params,
                    json_body=json_body,
                    form=form,
                    authenticated=True,
                    base_url=base_url,
                    _allow_401_retry=False,
                )
            if result.status == 429 or result.status >= 500:
                last_error = ApiError(result.status, path, "resposta temporariamente indisponível")
                if attempt >= self.settings.max_retries:
                    raise last_error
                retry_after = _retry_after(result.headers)
                delay = retry_after if retry_after is not None else self.settings.backoff_base_seconds * (2**attempt)
                LOGGER.warning("API respondeu %s; nova tentativa em %.1fs", result.status, delay)
                self.sleeper(delay)
                continue
            if result.status < 200 or result.status >= 300:
                raise ApiError(result.status, path, "resposta rejeitada")
            try:
                parsed = json.loads(result.body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ApiError(result.status, path, "resposta JSON inválida") from exc
            if not isinstance(parsed, (dict, list)):
                raise ApiError(result.status, path, "resposta JSON inesperada")
            return parsed
        raise last_error or ApiError(None, path)

    def search_items(self, site_id: str, query: str, offset: int, limit: int) -> dict[str, Any]:
        response = self._send_json(
            "GET", f"/sites/{urllib.parse.quote(site_id)}/search", params={"q": query, "offset": offset, "limit": limit}
        )
        if not isinstance(response, dict):
            raise ApiError(200, "/sites/.../search", "resposta de busca inesperada")
        return response

    def bulk_items(self, item_ids: list[str]) -> list[dict[str, Any]]:
        if not item_ids:
            return []
        if len(item_ids) > self.settings.max_items_per_request:
            raise ValueError("/items/bulk aceita no máximo o lote configurado")
        response = self._send_json("GET", "/items/bulk", params={"ids": ",".join(item_ids)})
        if not isinstance(response, list):
            raise ApiError(200, "/items/bulk", "resposta bulk inesperada")
        return [entry for entry in response if isinstance(entry, dict)]

    def bulk_users(self, user_ids: list[str]) -> list[dict[str, Any]]:
        if not user_ids:
            return []
        if len(user_ids) > self.settings.max_items_per_request:
            raise ValueError("/users/bulk aceita no máximo o lote configurado")
        response = self._send_json("GET", "/users/bulk", params={"ids": ",".join(user_ids)})
        if not isinstance(response, list):
            raise ApiError(200, "/users/bulk", "resposta bulk inesperada")
        return [entry for entry in response if isinstance(entry, dict)]

    def send_telegram(self, bot_token: str, chat_id: str, text: str) -> dict[str, Any]:
        if not bot_token or not chat_id:
            raise ConfigError("TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID são necessários para alertas")
        response = self._send_json(
            "POST",
            "/sendMessage",
            form={"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"},
            authenticated=False,
            base_url=f"https://api.telegram.org/bot{bot_token}",
        )
        if not isinstance(response, dict) or not response.get("ok"):
            raise ApiError(200, "/sendMessage", "Telegram rejeitou a mensagem")
        return response


def authorization_url(oauth_base_url: str, client_id: str, redirect_uri: str, state: str | None = None) -> str:
    state = state or secrets.token_urlsafe(24)
    query = urllib.parse.urlencode(
        {"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri, "state": state}
    )
    return f"{oauth_base_url.rstrip('/')}/authorization?{query}"


def exchange_authorization_code(
    settings: Settings,
    code: str,
    redirect_uri: str,
    transport: UrllibTransport | None = None,
) -> str:
    transport = transport or UrllibTransport()
    url = f"{settings.api_base_url}/oauth/token"
    form = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "client_id": settings.client_id,
            "client_secret": settings.client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        }
    ).encode("utf-8")
    result = transport.request(
        "POST", url, {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"}, form, settings.request_timeout_seconds
    )
    if result.status < 200 or result.status >= 300:
        raise ApiError(result.status, "/oauth/token", "código OAuth rejeitado")
    try:
        payload = json.loads(result.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiError(result.status, "/oauth/token", "resposta OAuth inválida") from exc
    refresh_token = payload.get("refresh_token") if isinstance(payload, dict) else None
    if not isinstance(refresh_token, str) or not refresh_token:
        raise ConfigError("resposta OAuth sem refresh_token")
    update_refresh_token(settings.env_path, refresh_token)
    return refresh_token


def exchange_authorization_code_values(
    *,
    client_id: str,
    client_secret: str,
    api_base_url: str,
    env_path: Path,
    code: str,
    redirect_uri: str,
    timeout: float = 30.0,
    transport: UrllibTransport | None = None,
) -> str:
    """OAuth bootstrap variant that works before ML_REFRESH_TOKEN exists."""

    transport = transport or UrllibTransport()
    form = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        }
    ).encode("utf-8")
    result = transport.request(
        "POST",
        f"{api_base_url.rstrip('/')}/oauth/token",
        {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
        form,
        timeout,
    )
    if result.status < 200 or result.status >= 300:
        raise ApiError(result.status, "/oauth/token", "código OAuth rejeitado")
    try:
        payload = json.loads(result.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiError(result.status, "/oauth/token", "resposta OAuth inválida") from exc
    refresh_token = payload.get("refresh_token") if isinstance(payload, dict) else None
    if not isinstance(refresh_token, str) or not refresh_token:
        raise ConfigError("resposta OAuth sem refresh_token")
    update_refresh_token(env_path, refresh_token)
    return refresh_token
