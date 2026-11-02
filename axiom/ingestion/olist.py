"""Adapter: Olist Brazilian e-commerce CSVs -> Axiom's per-day parquet layout (M8, PRD §9.2).

Olist (https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce) only contains COMPLETED orders: there are
no sessions or funnel events. Each order becomes one session with a full-funnel event trail, so revenue,
orders, AOV and the dimensions country (customer state), category and payment_method are real, while
traffic and conversion metrics are degenerate (conversion is 100% by construction). Dimensions Olist does not
have (platform, os, app_version, traffic_source, device_type, new_vs_returning) are the constant "n/a".
"""
from pathlib import Path

import pandas as pd

FUNNEL = ["view", "add_to_cart", "checkout_start", "payment_attempt", "payment_success"]
CONST = ["platform", "os", "app_version", "traffic_source", "device_type", "new_vs_returning"]


def convert(src: Path, out: Path) -> int:
    """Write out/{sessions,events,orders}/<date>.parquet from the Olist CSVs in `src`. Returns #days."""
    rd = lambda n: pd.read_csv(Path(src) / f"olist_{n}_dataset.csv")
    orders = rd("orders")
    orders = orders[orders.order_status.isin(["delivered", "shipped", "invoiced", "approved"])].copy()
    orders["ts"] = pd.to_datetime(orders.order_purchase_timestamp)
    cust = rd("customers")[["customer_id", "customer_state"]]
    pay = rd("order_payments").groupby("order_id").agg(
        payment_method=("payment_type", "first"), amount=("payment_value", "sum")).reset_index()
    items = rd("order_items").merge(rd("products")[["product_id", "product_category_name"]], on="product_id")
    items = items.groupby("order_id").agg(items=("product_id", "count"),
                                          category=("product_category_name", "first")).reset_index()
    df = (orders.merge(cust, on="customer_id").merge(pay, on="order_id").merge(items, on="order_id")
          .rename(columns={"customer_state": "country"}).fillna({"category": "unknown"}))
    df["session_id"] = "s_" + df.order_id
    df["day"] = df.ts.dt.strftime("%Y-%m-%d")
    for name in ("sessions", "events", "orders"):
        (Path(out) / name).mkdir(parents=True, exist_ok=True)
    for day, d in df.groupby("day"):
        sessions = d[["session_id", "ts", "country", "category"]].assign(**dict.fromkeys(CONST, "n/a"))
        events = pd.concat([d[["session_id", "ts", "category", "payment_method"]].assign(
            event_type=e, error_code=None,
            payment_method=lambda x, e=e: x.payment_method if e.startswith("payment") else None,
            event_id=lambda x, e=e: e + "_" + x.session_id) for e in FUNNEL], ignore_index=True)
        sessions.to_parquet(Path(out) / "sessions" / f"{day}.parquet", index=False)
        events.to_parquet(Path(out) / "events" / f"{day}.parquet", index=False)
        d[["order_id", "session_id", "ts", "payment_method", "amount", "items"]].to_parquet(
            Path(out) / "orders" / f"{day}.parquet", index=False)
    return df.day.nunique()
