#!/usr/bin/env python3
"""
Compute long-side Parabolic SAR stop status for Taiwan listed stocks using TWSE
monthly STOCK_DAY data.

Example:
  python scripts/twse_sar.py \
    --stock 6239 \
    --start-date 2026-05-04 \
    --initial-sar 185.5 \
    --initial-ep 223 \
    --af-start 0.001 \
    --af-step 0.005 \
    --af-max 0.10

Notes:
- Designed for TWSE listed stocks. TPEx/OTC uses a different endpoint.
- Checks each trading day with: low <= current_day_sar.
- For long trend, new highs update EP and increase AF after the day is checked.
- Next SAR is constrained to not exceed the current and previous lows.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import ssl
import urllib.request


def month_starts(start: dt.date, end: dt.date) -> list[dt.date]:
    cur = dt.date(start.year, start.month, 1)
    out = []
    while cur <= end:
        out.append(cur)
        if cur.month == 12:
            cur = dt.date(cur.year + 1, 1, 1)
        else:
            cur = dt.date(cur.year, cur.month + 1, 1)
    return out


def parse_twse_date(s: str) -> dt.date:
    y, m, d = map(int, s.split("/"))
    return dt.date(y + 1911, m, d)


def parse_price(s: str) -> float:
    return float(s.replace(",", "").replace("X", "").strip())


def fetch_twse_stock_day(stock: str, month: dt.date) -> list[dict]:
    date_param = month.strftime("%Y%m01")
    url = (
        "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY"
        f"?date={date_param}&stockNo={stock}&response=json"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    # Some enterprise/macOS networks inject a local CA; unverified context keeps this
    # script useful as an ad-hoc calculation helper. Prefer verified TLS in services.
    ctx = ssl._create_unverified_context()
    with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("stat") != "OK":
        raise RuntimeError(f"TWSE fetch failed for {stock} {date_param}: {payload}")
    rows = []
    for raw in payload["data"]:
        rows.append(
            {
                "date": parse_twse_date(raw[0]),
                "open": parse_price(raw[3]),
                "high": parse_price(raw[4]),
                "low": parse_price(raw[5]),
                "close": parse_price(raw[6]),
            }
        )
    return rows


def compute_long_sar(rows: list[dict], initial_sar: float, initial_ep: float, af_start: float, af_step: float, af_max: float) -> tuple[list[dict], dict | None]:
    sar = initial_sar
    ep = initial_ep
    af = af_start
    prev_low = None
    calc = []
    hit = None

    for row in rows:
        is_hit = row["low"] <= sar + 1e-12
        record = {**row, "sar": sar, "ep": ep, "af": af, "hit": is_hit}
        calc.append(record)
        if is_hit:
            hit = record
            break

        if row["high"] > ep:
            ep = row["high"]
            af = min(af + af_step, af_max)

        next_sar = sar + af * (ep - sar)
        lows = [row["low"]]
        if prev_low is not None:
            lows.append(prev_low)
        sar = min([next_sar] + lows)
        prev_low = row["low"]

    return calc, hit


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute long-side SAR stop for a TWSE listed stock")
    parser.add_argument("--stock", required=True, help="TWSE stock code, e.g. 6239 or 3711")
    parser.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end-date", default=dt.date.today().isoformat(), help="YYYY-MM-DD, default today")
    parser.add_argument("--initial-sar", type=float, required=True)
    parser.add_argument("--initial-ep", type=float, required=True)
    parser.add_argument("--af-start", type=float, required=True)
    parser.add_argument("--af-step", type=float, required=True)
    parser.add_argument("--af-max", type=float, required=True)
    args = parser.parse_args()

    start = dt.date.fromisoformat(args.start_date)
    end = dt.date.fromisoformat(args.end_date)
    rows = []
    for month in month_starts(start, end):
        rows.extend(fetch_twse_stock_day(args.stock, month))
    rows = sorted([r for r in rows if start <= r["date"] <= end], key=lambda r: r["date"])
    if not rows:
        raise SystemExit("No rows in requested date range")

    calc, hit = compute_long_sar(rows, args.initial_sar, args.initial_ep, args.af_start, args.af_step, args.af_max)

    print(
        f"PARAMS stock={args.stock} start={start} end={end} "
        f"initial_sar={args.initial_sar} initial_ep={args.initial_ep} "
        f"af_start={args.af_start} af_step={args.af_step} af_max={args.af_max}"
    )
    print("date,high,low,close,sar,ep,af,hit")
    for r in calc:
        print(
            f"{r['date']},{r['high']:.2f},{r['low']:.2f},{r['close']:.2f},"
            f"{r['sar']:.4f},{r['ep']:.2f},{r['af']:.3f},{r['hit']}"
        )

    if hit:
        print(
            f"RESULT hit_date={hit['date']} low={hit['low']:.2f} "
            f"sar={hit['sar']:.4f} close={hit['close']:.2f} ep={hit['ep']:.2f} af={hit['af']:.3f}"
        )
    else:
        last = calc[-1]
        print(
            f"RESULT no_hit latest_date={last['date']} latest_sar={last['sar']:.4f} "
            f"latest_low={last['low']:.2f} latest_close={last['close']:.2f} ep={last['ep']:.2f} af={last['af']:.3f}"
        )


if __name__ == "__main__":
    main()
