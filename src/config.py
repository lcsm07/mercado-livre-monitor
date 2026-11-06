from __future__ import annotations

import os
import shlex
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import Coupon, ProductConfig


class ConfigError(ValueError):
    """Configuration is missing or invalid."""


def read_env_file(path: str | Path) -> dict[str, str]:
    return _parse_dotenv(Path(path).expanduser())


def _parse_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ConfigError(f"linha inválida em {path}:{line_number}")
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not key or not key.replace("_", "").isalnum():
            raise ConfigError(f"nome de variável inválido em {path}:{line_number}")
        raw_value = raw_value.strip()
        if raw_value:
            try:
                parts = shlex.split(raw_value, comments=True, posix=True)
                value = parts[0] if parts else ""
            except ValueError as exc:
                raise ConfigError(f"valor inválido em {path}:{line_number}") from exc
        else:
            value = ""
        values[key] = value
    return values


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.lower() in {"1", "true", "yes", "sim", "on"}:
            return True
        if value.lower() in {"0", "false", "no", "não", "off"}:
            return False
    raise ConfigError(f"booleano inválido: {value!r}")


def _float(value: Any, field: str, default: float | None = None) -> float | None:
    if value is None:
        return default
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field} deve ser numérico") from exc
    if result < 0:
        raise ConfigError(f"{field} não pode ser negativo")
    return result


def _positive_int(value: Any, field: str, default: int) -> int:
    try:
        result = int(value if value is not None else default)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field} deve ser inteiro") from exc
    if result <= 0:
        raise ConfigError(f"{field} deve ser maior que zero")
    return result


@dataclass(frozen=True)
class Settings:
    site_id: str
    api_base_url: str
    oauth_base_url: str
    client_id: str
    client_secret: str
    refresh_token: str
    telegram_bot_token: str
    telegram_chat_id: str
    config_path: Path
    env_path: Path
    database_path: Path
    products: tuple[ProductConfig, ...]
    coupons: tuple[Coupon, ...]
    request_spacing_seconds: float
    request_timeout_seconds: float
    max_retries: int
    backoff_base_seconds: float
    search_page_size: int
    max_pages_per_query: int
    max_items_per_request: int
    relevant_drop_brl: float
    exceptional_total: float
    notify_enabled: bool
    dry_run: bool

    @classmethod
    def from_files(cls, config_path: str | Path = "config.toml", env_path: str | Path = ".env") -> "Settings":
        config_file = Path(config_path).expanduser().resolve()
        env_file = Path(env_path).expanduser().resolve()
        if not config_file.exists():
            raise ConfigError(
                f"configuração ausente: copie config.example.toml para {config_file} e ajuste os limites"
            )
        try:
            raw = tomllib.loads(config_file.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"não foi possível ler {config_file}") from exc
        env_values = _parse_dotenv(env_file)

        def env(name: str, required: bool = False) -> str:
            value = os.environ.get(name, env_values.get(name, "")).strip()
            if required and not value:
                raise ConfigError(f"variável obrigatória ausente: {name}")
            return value

        api = raw.get("api", {})
        runtime = raw.get("runtime", {})
        products_raw = raw.get("products") or {
            key: value
            for key, value in raw.items()
            if isinstance(value, dict) and key not in {"api", "runtime", "coupons"}
        }
        if not isinstance(products_raw, dict) or not products_raw:
            raise ConfigError("defina pelo menos um produto em [products.<chave>]")
        products: list[ProductConfig] = []
        for key, value in products_raw.items():
            if not isinstance(value, dict):
                raise ConfigError(f"configuração inválida para produto {key}")
            queries = tuple(str(q).strip() for q in value.get("queries", ()) if str(q).strip())
            if not queries:
                raise ConfigError(f"produto {key} precisa de pelo menos uma query")
            target_price = _float(value.get("target_price"), f"{key}.target_price")
            if target_price is None:
                raise ConfigError(f"produto {key} precisa de target_price")
            products.append(
                ProductConfig(
                    key=str(key),
                    name=str(value.get("name", key)),
                    queries=queries,
                    target_price=target_price,
                    max_total=_float(value.get("max_total"), f"{key}.max_total"),
                    reference_item_ids=tuple(str(i) for i in value.get("reference_item_ids", ())),
                    allow_ax=_bool(value.get("allow_ax"), False),
                    require_ddr4=_bool(value.get("require_ddr4"), True),
                    alert_new_offers=_bool(value.get("alert_new_offers"), True),
                )
            )

        coupons_raw = raw.get("coupons") or {}
        coupons: list[Coupon] = []
        if isinstance(coupons_raw, dict):
            for code, value in coupons_raw.items():
                if not isinstance(value, dict):
                    raise ConfigError(f"configuração inválida para cupom {code}")
                minimum = _float(value.get("minimum_subtotal"), f"coupons.{code}.minimum_subtotal")
                if minimum is None:
                    raise ConfigError(f"cupom {code} precisa de minimum_subtotal")
                coupons.append(
                    Coupon(
                        code=str(value.get("code", code)),
                        minimum_subtotal=minimum,
                        discount=_float(value.get("discount"), f"coupons.{code}.discount", 0.0) or 0.0,
                        expires_at=value.get("expires_at"),
                    )
                )

        database_value = str(runtime.get("database_path", "data/monitor.db"))
        database_path = Path(database_value).expanduser()
        if not database_path.is_absolute():
            database_path = config_file.parent / database_path
        page_size = _positive_int(runtime.get("search_page_size"), "runtime.search_page_size", 50)
        if page_size > 50:
            raise ConfigError("runtime.search_page_size não pode exceder 50")
        batch_size = _positive_int(runtime.get("max_items_per_request"), "runtime.max_items_per_request", 20)
        if batch_size > 20:
            raise ConfigError("runtime.max_items_per_request não pode exceder 20")
        spacing = _float(runtime.get("request_spacing_seconds"), "runtime.request_spacing_seconds", 0.35)
        timeout = _float(runtime.get("request_timeout_seconds"), "runtime.request_timeout_seconds", 30.0)
        backoff = _float(runtime.get("backoff_base_seconds"), "runtime.backoff_base_seconds", 2.0)
        drop = _float(runtime.get("relevant_drop_brl"), "runtime.relevant_drop_brl", 5.0)
        exceptional = _float(runtime.get("exceptional_total"), "runtime.exceptional_total", 1350.0)
        assert spacing is not None and timeout is not None and backoff is not None and drop is not None
        assert exceptional is not None

        try:
            mode = stat.S_IMODE(env_file.stat().st_mode) if env_file.exists() else None
        except OSError:
            mode = None
        if mode is not None and mode & 0o077:
            raise ConfigError(f"permissões inseguras em {env_file}; use chmod 600 {env_file}")

        return cls(
            site_id=str(api.get("site_id", "MLB")),
            api_base_url=str(api.get("base_url", "https://api.mercadolibre.com")).rstrip("/"),
            oauth_base_url=str(api.get("oauth_base_url", "https://auth.mercadolivre.com.br")).rstrip("/"),
            client_id=env("ML_CLIENT_ID", required=True),
            client_secret=env("ML_CLIENT_SECRET", required=True),
            refresh_token=env("ML_REFRESH_TOKEN", required=True),
            telegram_bot_token=env("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=env("TELEGRAM_CHAT_ID"),
            config_path=config_file,
            env_path=env_file,
            database_path=database_path,
            products=tuple(products),
            coupons=tuple(coupons),
            request_spacing_seconds=spacing,
            request_timeout_seconds=timeout,
            max_retries=_positive_int(runtime.get("max_retries"), "runtime.max_retries", 4),
            backoff_base_seconds=backoff,
            search_page_size=page_size,
            max_pages_per_query=_positive_int(runtime.get("max_pages_per_query"), "runtime.max_pages_per_query", 4),
            max_items_per_request=batch_size,
            relevant_drop_brl=drop,
            exceptional_total=exceptional,
            notify_enabled=_bool(runtime.get("notify_enabled"), False),
            dry_run=_bool(runtime.get("dry_run"), True),
        )


def update_refresh_token(env_path: Path, refresh_token: str) -> None:
    """Rotate ML_REFRESH_TOKEN without printing or rewriting unrelated secrets."""

    if not refresh_token or "\n" in refresh_token or "\r" in refresh_token:
        raise ConfigError("refresh token inválido")
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    replaced = False
    output: list[str] = []
    for line in lines:
        if line.lstrip().startswith("ML_REFRESH_TOKEN="):
            output.append(f"ML_REFRESH_TOKEN={refresh_token}")
            replaced = True
        else:
            output.append(line)
    if not replaced:
        output.append(f"ML_REFRESH_TOKEN={refresh_token}")
    temp = env_path.with_name(f".{env_path.name}.tmp")
    temp.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    temp.chmod(0o600)
    temp.replace(env_path)
    env_path.chmod(0o600)
