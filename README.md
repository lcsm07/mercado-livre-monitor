# Monitor privado de ofertas do Mercado Livre

Monitor local para Ryzen 7 5700X e Gigabyte B550M Aorus Elite DDR4. Ele busca anúncios ativos de vários vendedores pela API oficial, rejeita modelos parecidos, grava cada observação em SQLite e pode enviar alertas pelo Telegram.

O monitor não faz login no Mercado Livre, não usa scraping e não testa cupons automaticamente. Frete e cupons continuam sujeitos à validação manual no carrinho.

## Instalação

```bash
cd /home/lucas/mercado-livre-monitor
cp config.example.toml config.toml
cp .env.example .env
chmod 600 .env
```

Crie uma aplicação no portal de desenvolvedores do Mercado Livre, configure no aplicativo uma URL de redirecionamento estática e preencha `ML_CLIENT_ID` e `ML_CLIENT_SECRET` no `.env`. O fluxo local é:

```bash
python3 -m src.main auth-url --redirect-uri 'https://localhost.com/redirect'
```

Abra a URL impressa, autorize com a conta principal e troque o `code` recebido no redirecionamento:

```bash
python3 -m src.main exchange-code \
  --code 'COLE_O_CODE_AQUI' \
  --redirect-uri 'https://localhost.com/redirect'
```

O `refresh_token` rotativo é salvo no `.env` sem ser exibido. O access token de curta duração fica em cache no SQLite local (também com permissão `600`) e só provoca nova rotação quando expira ou é rejeitado; nenhum token é escrito em logs.

### Página de callback HTTPS

O DevCenter pode rejeitar URLs locais. O arquivo `docs/callback.html` é uma página estática sem dependências externas que mostra o `code` recebido pelo OAuth. Publique o diretório `docs` no GitHub Pages e cadastre a URL resultante, por exemplo:

```text
https://SEU_USUARIO.github.io/mercado-livre-monitor/callback.html
```

Use exatamente essa URL na autorização e na troca do código. A página não recebe `Client Secret`, access token ou refresh token.

Preencha também `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID` quando quiser alertas reais. Para obter o chat ID, envie uma mensagem ao bot e consulte `getUpdates` pelo método documentado pelo Telegram. Deixe-os vazios durante a observação.

## Primeiro ciclo e modo de observação

O exemplo começa com `dry_run = true` e `notify_enabled = false`:

```bash
python3 -m src.main check
sqlite3 data/monitor.db 'select product_key, item_id, accepted, decision_reason from observations;'
```

O monitor registra anúncios aceitos e descartados, inclusive o motivo do descarte. Valide alguns anúncios manualmente. O ID `MLB4513151668` é apenas uma referência; as buscas continuam coletando outros IDs.

Depois da validação, altere no `config.toml`:

```toml
dry_run = false
notify_enabled = true
```

Ative o timer do usuário:

```bash
mkdir -p ~/.config/systemd/user
cp systemd/mercado-livre-monitor.service ~/.config/systemd/user/
cp systemd/mercado-livre-monitor.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now mercado-livre-monitor.timer
systemctl --user list-timers mercado-livre-monitor.timer
```

Para acompanhar uma execução:

```bash
journalctl --user -u mercado-livre-monitor.service -n 100 --no-pager
```

Se o PC não mantiver a sessão do usuário ativa, habilite `loginctl enable-linger "$USER"` conscientemente; isso faz parte da configuração do sistema, não é executado pelo monitor.

## Configuração

As queries, alvos, limite do conjunto, paginação, espaçamento, backoff e filtros ficam em `config.toml`. O limite inicial é Ryzen até R$ 900, placa até R$ 600 e conjunto até R$ 1.450; totais de até R$ 1.350 recebem destaque.

Cupons podem ser declarados no TOML ou registrados no banco:

```bash
python3 -m src.main add-coupon CUPOM100 1000 --discount 100
```

Eles só aparecem como oportunidades possíveis quando o subtotal atinge o mínimo. Nenhuma sessão, cookie ou senha de comprador é armazenada.

## Detalhes técnicos

- `/sites/MLB/search` é paginado por query e os IDs são deduplicados.
- Detalhes usam `/items/bulk` em lotes de até 20; vendedores usam `/users/bulk` para reputação.
- HTTP 429, falhas de rede e respostas 5xx recebem espera progressiva; `Retry-After` é respeitado quando existe.
- O banco registra histórico por ciclo, sem duplicar o mesmo item no mesmo ciclo.
- Alertas de anúncio são suprimidos até uma nova queda relevante, reabertura de estoque ou cruzamento do alvo. Falha no Telegram não marca o alerta como enviado.
- O preço de frete pode não estar disponível na API sem contexto de destino e é explicitamente marcado para conferência no carrinho.

Os links oficiais de referência são [autenticação e autorização](https://developers.mercadolivre.com.br/autenticacao-e-autorizacao), [itens e buscas](https://developers.mercadolivre.com.br/pt_br/itens-e-buscas) e [buscador de produtos](https://developers.mercadolivre.com.br/buscador-de-produtos).

## Testes locais

```bash
python3 -m unittest discover -v
```
