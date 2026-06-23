---
name: shioaji-taiwan-equity-trading
description: Use when building or reviewing a Taiwan equity quant-trading project with SinoPac Shioaji Python SDK, focused on listed/TPEX/emerging stocks and excluding futures/options.
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [taiwan-stocks, shioaji, sinopac, trading-api, cloud-run]
    related_skills: [codex]
---

# Shioaji Taiwan Equity Trading

## Overview

Shioaji is SinoPac Securities' Python SDK / API server for Taiwan trading. Use this skill for a deployable quant-trading service that trades Taiwan cash equities only: listed stocks (TSE), TPEx stocks (OTC), and emerging stocks (OES). Do not include futures or options unless the user explicitly changes scope.

Primary package: `shioaji`.

Official docs consulted:
- https://sinotrade.github.io/tutor/login/
- https://sinotrade.github.io/tutor/contract/
- https://sinotrade.github.io/tutor/order/Stock/
- https://sinotrade.github.io/tutor/order/IntradayOdd/
- https://sinotrade.github.io/tutor/order/order_deal_event/stocks/
- https://sinotrade.github.io/tutor/callback/orderdeal_event/
- https://sinotrade.github.io/tutor/market_data/streaming/stocks/
- https://sinotrade.github.io/tutor/market_data/snapshot/
- https://sinotrade.github.io/tutor/market_data/historical/
- https://sinotrade.github.io/tutor/accounting/position/
- https://sinotrade.github.io/tutor/accounting/account_balance/
- https://sinotrade.github.io/tutor/accounting/trading_limits/
- https://sinotrade.github.io/tutor/prepare/terms/

## When to Use

Use when:
- Designing adapters around SinoPac Shioaji for Taiwan cash-stock trading.
- Implementing order placement, order state tracking, fills, positions, account balance, or trading limits.
- Fetching live quote/tick/bid-ask/snapshot/historical tick/kbar data for stocks.
- Planning a Cloud Run deployment that must run reliably with credentials, CA activation, callbacks, and state reconciliation.

Do not use for:
- Futures, options, or overseas products.
- Backtest-only research with no brokerage execution path.
- Non-SinoPac broker APIs.

## Scope and Product Mapping

Equity scope:
- Listed stocks: `Exchange.TSE`, contracts under `api.Contracts.Stocks.TSE` or `api.Contracts.Stocks['2330']`.
- TPEx stocks: `Exchange.OTC`, contracts under `api.Contracts.Stocks.OTC` or by code lookup.
- Emerging stocks: `Exchange.OES`. Shioaji 1.5.0 exposes `Exchange.OES` and `StockOrderCond.Emerging` in SDK type stubs/enums, and contracts list includes `Stocks = (OES, OTC, TSE)`. However, the official stock order docs currently document order `contract.exchange` as `{TSE, OTC}` and `order_cond` as `{Cash, MarginTrading, ShortSelling}`; OES order placement is therefore not fully documented. Treat OES live ordering as requiring broker/simulation verification before production. Market-data streaming docs do list exchange `{TSE, OTC, OES, TAIFEX}` for stock subscriptions.

Explicitly excluded:
- `SecurityType.Future`, `SecurityType.Option`, futures account APIs, options account APIs, combo orders.

Important enums observed in Shioaji 1.5.0:
- `Action`: `Buy`, `Sell`.
- `Exchange`: `TSE`, `OTC`, `OES`, `TAIFEX`, `TIM`; use only `TSE`, `OTC`, `OES` for this project.
- `SecurityType.Stock`: `STK`.
- `StockPriceType`: `LMT`, `MKT`.
- `OrderType`: `ROD`, `IOC`, `FOK`.
- `StockOrderLot`: `Common`, `Fixing`, `Odd`, `IntradayOdd`, `BlockTrade`.
- `StockOrderCond`: `Cash`, `MarginTrading`, `ShortSelling`, `Netting`, `Emerging`.
- `Unit`: `Common`, `Share` for position quantity units.
- `QuoteType`: `Tick`, `BidAsk`, `Quote`.

## Authentication, Signing, and Environment

Basic Python login:

```python
import shioaji as sj

api = sj.Shioaji(simulation=False)
api.login(
    api_key=os.environ['SJ_API_KEY'],
    secret_key=os.environ['SJ_SECRET_KEY'],
    contracts_timeout=10000,
    subscribe_trade=True,
)
```

Login arguments to model in config:
- `api_key`: API Key.
- `secret_key`: Secret Key.
- `fetch_contract`: default true; set false only if fetching contracts later.
- `contracts_timeout`: block until contracts loaded; use a bounded timeout on service startup.
- `contracts_cb`: optional progress callback.
- `subscribe_trade`: default true; keep true for order/deal events unless a separate reconciliation loop is authoritative.
- `receive_window`: default 30000 ms; if sign timeout occurs, verify system time first.

Pre-production requirements:
- User must have SinoPac Securities account.
- Complete API document signing and simulation-mode API test.
- Stock and futures are signed/tested separately; for this project only stock signing/test is required.
- Simulation test service constraints in docs: Monday-Friday 08:00-20:00, with Taiwan-IP restriction 18:00-20:00; version >= 1.2.

Cloud Run notes:
- Store API key, secret key, and CA/certificate password in Secret Manager, not source code.
- Cloud Run containers are ephemeral; do not depend on local contract cache or local token cache unless explicitly mounted/managed.
- Ensure system time is correct; Cloud Run is usually NTP-synced, but still handle Shioaji sign timeout errors explicitly.
- Treat startup as idempotent: login, activate CA, load contracts, register callbacks, reconcile open orders/positions.
- Websocket/SSE-style quote streams may be fragile under request-scoped Cloud Run. Prefer Cloud Run Jobs/Scheduler for batch strategies, or a single always-on instance/min instances for intraday live execution if streaming is required.

## Contracts

Contracts are required for placing orders and subscribing to market data.

Contract update schedule in docs:
- 07:50 futures contract update.
- 08:00 full-market contract update.
- 14:45 futures night-session update.
- 17:15 futures night-session update.

For this project, the relevant schedule is the 08:00 full-market contract update.

Fetch contracts at login:

```python
api.login(
    api_key=api_key,
    secret_key=secret_key,
    contracts_timeout=10000,
)
assert api.Contracts.status  # inspect/verify in implementation
```

Or defer:

```python
api.login(api_key=api_key, secret_key=secret_key, fetch_contract=False)
api.fetch_contracts(contract_download=True)
```

Common contract lookup:

```python
contract = api.Contracts.Stocks['2330']
# or explicit exchange containers when needed:
contract = api.Contracts.Stocks.TSE.TSE2330
```

Contract fields relevant to risk/order validation:
- `security_type`, `exchange`, `code`, `symbol`, `name`, `category`, `currency`.
- `limit_up`, `limit_down`, `reference`.
- `update_date`.
- `margin_trading_balance`, `short_selling_balance`.
- `day_trade`: `Yes`, `No`, `OnlyBuy`.

## Market Data Needed

### Ad-hoc TWSE daily data and SAR stop checks

For quick listed-stock OHLC/SAR calculations when Shioaji credentials are not needed, TWSE's public monthly endpoint is usually sufficient:

```text
https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?date=YYYYMM01&stockNo=2330&response=json
```

Important details:
- TWSE dates are ROC years in the payload (`115/06/01` => `2026-06-01`).
- Fetch every calendar month covering the requested range; filter rows after parsing.
- For long-side SAR stop checks, compare each day's low against that day's SAR **before** updating EP/AF for the next day: `low <= current_sar` means touched/stopped.
- Long-side update order used in prior verified calculations: initialize with user-provided SAR and EP; check hit for the current row; if no hit and current high exceeds EP, set EP to current high and raise AF by Increment up to Max; compute next SAR as `SAR + AF * (EP - SAR)`; constrain next SAR with `min(next_sar, current_low, previous_low)` when a previous low exists.
- Report the stop date if hit; otherwise report latest SAR, latest low/close, EP, and AF. Keep the output concise and include only the recent rows needed to audit the conclusion.

If the TWSE daily path does not cover the code/date span cleanly, use the fallback notes in `references/sar-data-fallback.md` and fetch daily bars from Yahoo Finance chart data (`{code}.TW`) before computing SAR.

Reusable helper: `scripts/twse_sar.py` computes this workflow for TWSE listed stocks with configurable `--initial-sar`, `--initial-ep`, `--af-start`, `--af-step`, and `--af-max`.

### Snapshots

Use for batch quote checks across symbols, up to 500 contracts/request:

```python
contracts = [api.Contracts.Stocks['2330'], api.Contracts.Stocks['2317']]
snapshots = api.snapshots(contracts)
rows = [s.dict() for s in snapshots]
```

HTTP server endpoint equivalent:
`POST /api/v1/data/snapshots` with `contracts` list.

### Historical ticks and kbars

Ticks:

```python
ticks = api.ticks(
    contract=api.Contracts.Stocks['2330'],
    date='2026-05-18',
)
```

Range ticks:

```python
ticks = api.ticks(
    contract=api.Contracts.Stocks['2330'],
    date='2026-05-18',
    query_type=sj.constant.TicksQueryType.RangeTime,
    time_start='09:00:00',
    time_end='09:20:01',
)
```

Last N ticks:

```python
ticks = api.ticks(
    contract=api.Contracts.Stocks['2330'],
    date='2026-05-18',
    query_type=sj.constant.TicksQueryType.LastCount,
    last_cnt=4,
)
```

Kbars:

```python
kbars = api.kbars(
    contract=api.Contracts.Stocks['2330'],
    start='2026-05-17',
    end='2026-05-18',
)
```

### Streaming quotes

Subscribe to stock tick, bid/ask, or quote:

```python
api.subscribe(
    api.Contracts.Stocks.TSE.TSE2890,
    quote_type=sj.QuoteType.Tick,
)

api.subscribe(
    api.Contracts.Stocks.TSE.TSE2890,
    quote_type=sj.QuoteType.BidAsk,
)

api.subscribe(
    api.Contracts.Stocks.TSE.TSE2890,
    quote_type=sj.QuoteType.Quote,
)
```

Intraday odd-lot quotes:

```python
api.subscribe(
    api.Contracts.Stocks.TSE.TSE2890,
    quote_type=sj.QuoteType.Tick,
    intraday_odd=True,
)
```

Unsubscribe symmetrically with `api.unsubscribe(contract, quote_type=..., intraday_odd=...)`.

Tick units to remember:
- Common stock tick volume is in lots.
- Intraday odd-lot tick volume is in shares.

## Stock Orders

CA activation is required before placing orders. Implement a broker adapter that refuses live orders unless both login and CA activation are verified.

Core API:

```python
trade = api.place_order(contract, order)
```

Stock order fields:
- `action`: `sj.Action.Buy` or `sj.Action.Sell`.
- `price`: float/int.
- `quantity`: int.
- `price_type`: `sj.StockPriceType.LMT` or `sj.StockPriceType.MKT`.
- `order_type`: `sj.OrderType.ROD`, `IOC`, `FOK`.
- `order_lot`: `sj.StockOrderLot.Common`, `Fixing`, `Odd`, `IntradayOdd`.
- `order_cond`: `sj.StockOrderCond.Cash`, `MarginTrading`, `ShortSelling`; for emerging stocks inspect/verify `Emerging` routing behavior before production.
- `daytrade_short`: bool, sell first then buy back.
- `custom_field`: letters/digits only, max length 6; useful for strategy/order correlation.
- `account`: normally `api.stock_account`.

Basic cash stock order:

```python
contract = api.Contracts.Stocks.TSE.TSE2890
order = sj.StockOrder(
    action=sj.Action.Buy,
    price=27.1,
    quantity=2,
    price_type=sj.StockPriceType.LMT,
    order_type=sj.OrderType.ROD,
    order_lot=sj.StockOrderLot.Common,
    order_cond=sj.StockOrderCond.Cash,
    account=api.stock_account,
)
trade = api.place_order(contract, order)
```

Intraday odd-lot order:

```python
order = sj.StockOrder(
    action=sj.Action.Buy,
    price=27.1,
    quantity=10,
    price_type=sj.StockPriceType.LMT,
    order_type=sj.OrderType.ROD,
    order_lot=sj.StockOrderLot.IntradayOdd,
    order_cond=sj.StockOrderCond.Cash,
    account=api.stock_account,
)
trade = api.place_order(contract, order)
```

Order quantity unit caveat:
- Common-lot orders represent board-lot units as used by Shioaji docs/examples.
- Intraday odd-lot examples use share quantities. Normalize strategy quantity units explicitly in the adapter.

## Update, Cancel, and Status Reconciliation

Typical update/cancel actions:

```python
api.update_order(trade=trade, price=27.0)  # price update where supported
api.update_order(trade=trade, qty=2)       # quantity reduction/update where supported
api.cancel_order(trade)
api.update_status(api.stock_account)
```

Implement order state as event-sourced plus reconciliation:
- Store `trade.order.id` as internal broker trade id.
- Store `seqno` and `ordno` when available.
- After any place/update/cancel, call or schedule `api.update_status(api.stock_account)` and reconcile open orders.
- Persist raw order/deal callback payloads before deriving state.

## Order and Deal Callbacks

Register callback before live trading:

```python
@api.on_order
def order_cb(stat, msg):
    # stat is OrderState, msg is dict-like payload
    persist_raw_event(stat, msg)
    dispatch_order_event(stat, msg)

# or:
api.set_order_callback(order_cb)
```

Stock order event includes:
- `operation`: `op_type`, `op_code`, `op_msg`.
- `order`: `id`, `seqno`, `ordno`, `account`, `action`, `price`, `quantity`, `order_type`, `price_type`, `order_cond`, `order_lot`, `custom_field`.
- `status`: `id`, `exchange_ts`, `modified_price`, `cancel_quantity`, `order_quantity`, `web_id`.
- `contract`: `exchange`, `code`, `security_type`, `symbol`, `name`, `currency`.

Stock deal event includes:
- `trade_id`, `seqno`, `ordno`, `exchange_seq`, `broker_id`, `account_id`.
- `action`, `code`, `order_cond`, `order_lot`, `price`, `quantity`, `web_id`, `custom_field`, `ts`.

Design rule:
- Order events update order lifecycle.
- Deal events update fills/executions and positions/cash projections.
- Never assume a placed order is filled; always wait for deal events or reconcile against broker state.

## Account, Cash, Positions, and Limits

Account balance:

```python
balance = api.account_balance(account=api.stock_account)
# fields: status, acc_balance, date, errmsg
```

Trading limits:

```python
limits = api.trading_limits(account=api.stock_account)
# fields: trading_limit, trading_used, trading_available,
# margin_limit, margin_used, margin_available,
# short_limit, short_used, short_available
```

Positions:

```python
positions = api.list_positions(account=api.stock_account)
```

Share-unit positions for odd-lot precision:

```python
from shioaji import Unit
positions = api.list_positions(account=api.stock_account, unit=Unit.Share)
```

Stock position fields:
- `id`, `code`, `direction`, `quantity`, `price`, `last_price`, `pnl`, `yd_quantity`.
- `cond`: `Cash`, `Netting`, `MarginTrading`, `ShortSelling`, `Emerging`.
- margin/short fields when relevant.

Position detail:

```python
details = api.list_position_detail(account=api.stock_account, detail_id=0)
```

## Minimal Broker Adapter Interface

Implement this boundary before strategies call Shioaji directly:

```python
class BrokerGateway:
    def login(self) -> None: ...
    def activate_ca(self) -> None: ...
    def load_contracts(self) -> None: ...
    def get_contract(self, code: str, exchange: str | None = None): ...
    def get_snapshot(self, codes: list[str]): ...
    def get_kbars(self, code: str, start: str, end: str): ...
    def subscribe_quotes(self, code: str, quote_type: str, intraday_odd: bool = False): ...
    def place_stock_order(self, request): ...
    def cancel_order(self, broker_order_id: str): ...
    def refresh_order_status(self) -> None: ...
    def list_positions(self, unit: str = 'Common'): ...
    def account_balance(self): ...
    def trading_limits(self): ...
```

Keep strategy code independent of Shioaji classes by mapping to internal dataclasses/Pydantic models at the gateway edge.

## Risk Controls Required Before Live Trading

Before any production order:
- Environment must be explicitly `production`, not accidentally live from a default.
- CA activation verified.
- Account id/broker id match configured allowlist.
- Symbol is in allowlist/trading universe and resolved contract exchange is `TSE`, `OTC`, or `OES`.
- Futures/options contracts are rejected.
- Price is inside `limit_down`/`limit_up` for limit orders where available.
- Quantity unit normalized (`Common` vs `IntradayOdd` / shares).
- Estimated notional fits `trading_available` and project-level max order size.
- Duplicate order key/idempotency key checked.
- Market hours and odd-lot trading session rules checked.
- Kill switch checked.
- Dry-run/simulation mode supported at the same interface.

## Cloud Run Architecture Notes

Recommended deployable components:
- `strategy-runner`: Cloud Run Job or scheduled service that computes signals and creates order intents.
- `broker-executor`: service/job that owns Shioaji login/CA activation and turns approved intents into orders.
- `event-ingestor`: receives/persists order/deal callbacks in-process; if using Shioaji callback only, it must run in the same process that placed/subscribed.
- `reconciler`: scheduled job that calls account balance, trading limits, positions, and order status to heal missed events.
- `risk-engine`: pure module used before every order.
- Persistent store: Cloud SQL or Firestore for orders, fills, positions snapshots, strategy state, and audit logs.

Cloud Run pitfalls:
- Autoscaling can create multiple executor instances. For live order routing, enforce a singleton/lease/lock or use Cloud Run Jobs with max parallelism 1.
- Do not keep secrets or CA files in container image.
- Do not rely only on callbacks; containers can restart. Reconcile broker state on startup and on schedule.
- Long-lived streaming quote subscriptions need min instances and careful timeout settings, or move quote ingestion to a more persistent runtime.

## FinLab Historical Data for Emerging Stocks

FinLab package/catalog reconnaissance showed that regular listed/TPEx OHLCV fields use `price:*`, while emerging-stock datasets are exposed separately under `rotc_price:*`:
- `rotc_price:開盤價`
- `rotc_price:最高價`
- `rotc_price:最低價`
- `rotc_price:收盤價`
- `rotc_price:成交股數`
- `rotc_price:成交金額`
- `rotc_price:日均價`
- `rotc_price:成交筆數`
- `rotc_price:最後揭示買價`
- `rotc_price:最後揭示賣價`

For an OES strategy, use `rotc_price:*` for historical OHLCV instead of `price:*`. Do not rely on `data.universe(market='ALL')` alone for emerging-stock OHLCV because `price:*` may not be the emerging dataset. Access to actual values still requires a valid FinLab login/token; verify coverage for target symbols during Phase 0 data sync.

## Codex Usage for This Project

When implementing with Codex CLI:
- Run Codex inside the target git repository with `pty=true`.
- Include this skill name in the prompt as required Shioaji brokerage context.
- Also instruct Codex to use its FinLab skill pack if installed in the Codex environment for research/backtest/data workflows.
- Keep broker/risk/execution modules separated from strategy and FinLab research modules.

Example Codex prompt fragment:

```text
Use the Codex finlab skill if available for data/research/backtesting conventions.
For brokerage execution, follow the Shioaji Taiwan equity trading constraints:
listed/TPEX/emerging stocks only; no futures/options; broker adapter boundary;
mandatory risk checks before live orders; Cloud Run singleton/reconciliation design.
```

## Verification Checklist

- [ ] `shioaji` version pinned and importable in the runtime image.
- [ ] Login works in simulation and production modes with explicit environment selection.
- [ ] API signing and simulation test completed for stock account.
- [ ] CA activation works from Cloud Run secret-mounted material.
- [ ] Contracts load and can resolve `TSE`, `OTC`, and `OES` stock contracts.
- [ ] Futures/options contracts are rejected by adapter tests.
- [ ] Snapshot/tick/kbar retrieval works for representative listed and TPEx codes.
- [ ] Order placement works in simulation for cash stock and, if needed, intraday odd-lot.
- [ ] Order/deal callbacks persist raw events and update internal state.
- [ ] Reconciler can rebuild open orders/fills/positions from broker/account APIs.
- [ ] Account balance, trading limits, and positions are checked before orders.
- [ ] Cloud Run deployment prevents multiple live executors from sending duplicate orders.

## Common Pitfalls

1. Confusing lots and shares. Common-lot and odd-lot flows differ; normalize quantities at the adapter boundary.
2. Treating callbacks as durable. They are process-local events; persist immediately and reconcile regularly.
3. Accidentally enabling futures/options because Shioaji loads all contracts. Filter `SecurityType.Stock` and exchanges `TSE/OTC/OES`.
4. Assuming contract cache is fresh. Full-market contract update is around 08:00; reload on startup and record contract `update_date`.
5. Running multiple Cloud Run instances as executors. Use a lock/singleton design for live order placement.
6. Ignoring API signing/test prerequisites. Production live trading requires stock API signing and simulation test approval.
7. Storing CA/API secrets in source or image. Use Secret Manager and least-privilege runtime access.
8. Not checking price limits/day-trade flags from contract metadata before placing orders.
