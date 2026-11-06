from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import replace
from pathlib import Path

from .alerts import AlertManager
from .api import MeliClient, authorization_url, exchange_authorization_code_values
from .config import ConfigError, Settings, read_env_file
from .history import History
from .metrics import bundle_metrics, format_brl, product_metrics
from .search import OfferSearcher

LOGGER = logging.getLogger("mercado_livre_monitor")


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings.from_files(args.config, args.env)
    if args.no_alerts:
        settings = replace(settings, notify_enabled=False, dry_run=True)
    return settings


def run_once(settings: Settings) -> int:
    history = History(settings.database_path)
    scan_id, checked_at = history.start_scan()
    client = MeliClient(settings)
    searcher = OfferSearcher(client, settings)
    alerts = AlertManager(settings, history, client)
    error_count = 0
    try:
        for product in settings.products:
            try:
                offers = searcher.offers_for_product(product)
                history.record_offers(scan_id, checked_at, offers)
                metrics = product_metrics(history, scan_id, product)
                LOGGER.info(
                    "%s: %d anúncios aceitos; menor atual=%s; menor histórico=%s",
                    product.name,
                    len(metrics.current_offers),
                    format_brl(metrics.cheapest.price if metrics.cheapest else None),
                    format_brl(metrics.historical_low),
                )
                for offer in metrics.current_offers:
                    alerts.consider_offer(scan_id, product, offer)
            except Exception as exc:  # one product must not hide the other one
                error_count += 1
                LOGGER.error("falha ao consultar %s: %s", product.name, type(exc).__name__)
        bundle = bundle_metrics(history, scan_id, settings.products)
        if len(bundle.offers) == len(settings.products):
            alerts.consider_bundle(settings.products, bundle)
        return error_count
    finally:
        history.finish_scan(scan_id, error_count)


def _read_bootstrap_values(config_path: Path, env_path: Path) -> tuple[str, str, str, str]:
    import tomllib

    raw = tomllib.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    api = raw.get("api", {})
    values = read_env_file(env_path)
    client_id = os.environ.get("ML_CLIENT_ID", values.get("ML_CLIENT_ID", "")).strip()
    client_secret = os.environ.get("ML_CLIENT_SECRET", values.get("ML_CLIENT_SECRET", "")).strip()
    if not client_id or not client_secret:
        raise ConfigError("ML_CLIENT_ID e ML_CLIENT_SECRET são necessários para o fluxo OAuth")
    return (
        client_id,
        client_secret,
        str(api.get("base_url", "https://api.mercadolibre.com")).rstrip("/"),
        str(api.get("oauth_base_url", "https://auth.mercadolivre.com.br")).rstrip("/"),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Monitor privado de ofertas do Mercado Livre")
    parser.add_argument("--config", default="config.toml", help="arquivo TOML de configuração")
    parser.add_argument("--env", default=".env", help="arquivo de credenciais")
    parser.add_argument("--no-alerts", action="store_true", help="força modo observação neste ciclo")
    subparsers = parser.add_subparsers(dest="command", required=False)

    subparsers.add_parser("check", help="executa uma verificação")

    auth_url_parser = subparsers.add_parser("auth-url", help="gera a URL para autorizar a aplicação")
    auth_url_parser.add_argument("--redirect-uri", required=True)
    auth_url_parser.add_argument("--state")

    exchange_parser = subparsers.add_parser("exchange-code", help="troca o code OAuth e salva o refresh token")
    exchange_parser.add_argument("--code", required=True)
    exchange_parser.add_argument("--redirect-uri", required=True)

    coupon_parser = subparsers.add_parser("add-coupon", help="registra um cupom para sugestões manuais")
    coupon_parser.add_argument("code")
    coupon_parser.add_argument("minimum_subtotal", type=float)
    coupon_parser.add_argument("--discount", type=float, default=0.0)
    coupon_parser.add_argument("--expires-at")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "check"
    try:
        config_path = Path(args.config).expanduser().resolve()
        env_path = Path(args.env).expanduser().resolve()
        if command == "auth-url":
            client_id, _, _, oauth_base = _read_bootstrap_values(config_path, env_path)
            print(authorization_url(oauth_base, client_id, args.redirect_uri, args.state))
            return 0
        if command == "exchange-code":
            client_id, client_secret, api_base, _ = _read_bootstrap_values(config_path, env_path)
            exchange_authorization_code_values(
                client_id=client_id,
                client_secret=client_secret,
                api_base_url=api_base,
                env_path=env_path,
                code=args.code,
                redirect_uri=args.redirect_uri,
            )
            print(f"refresh token salvo em {env_path}; o valor não será exibido")
            return 0
        if command == "add-coupon":
            settings = _settings(args)
            History(settings.database_path).save_coupon(
                args.code, args.minimum_subtotal, args.discount, args.expires_at
            )
            print("cupom registrado; ele será apenas sugerido para validação manual")
            return 0
        settings = _settings(args)
        errors = run_once(settings)
        return 1 if errors == len(settings.products) else 0
    except (ConfigError, OSError, ValueError) as exc:
        LOGGER.error("não foi possível executar: %s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
