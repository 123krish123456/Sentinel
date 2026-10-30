import pandas as pd

from sentinel.ingestion.olist import convert


def test_convert_tiny_olist(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    src.mkdir()
    pd.DataFrame({"order_id": ["o1", "o2"], "customer_id": ["c1", "c2"], "order_status": ["delivered", "canceled"],
                  "order_purchase_timestamp": ["2018-01-05 10:00:00"] * 2}).to_csv(src / "olist_orders_dataset.csv", index=False)
    pd.DataFrame({"customer_id": ["c1", "c2"], "customer_state": ["SP", "RJ"]}).to_csv(src / "olist_customers_dataset.csv", index=False)
    pd.DataFrame({"order_id": ["o1"], "payment_type": ["credit_card"], "payment_value": [50.0]}).to_csv(src / "olist_order_payments_dataset.csv", index=False)
    pd.DataFrame({"order_id": ["o1"], "product_id": ["p1"]}).to_csv(src / "olist_order_items_dataset.csv", index=False)
    pd.DataFrame({"product_id": ["p1"], "product_category_name": ["toys"]}).to_csv(src / "olist_products_dataset.csv", index=False)
    assert convert(src, out) == 1  # canceled order is dropped
    orders = pd.read_parquet(out / "orders" / "2018-01-05.parquet")
    events = pd.read_parquet(out / "events" / "2018-01-05.parquet")
    assert orders.amount.tolist() == [50.0] and len(events) == 5
    assert set(pd.read_parquet(out / "sessions" / "2018-01-05.parquet").country) == {"SP"}
