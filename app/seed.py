from sqlalchemy import select, text

from app.db import Session
from app.inventory import Inventory, STORE_ID
from app.models import Product, Store, Warehouse

PRODUCTS = [
    ("00000000-0000-4000-8000-000000000101", "SKU-101", "Органайзер", 10, 6),
    ("00000000-0000-4000-8000-000000000102", "SKU-102", "Контейнер", 5, 0),
    ("00000000-0000-4000-8000-000000000103", "SKU-103", "Корзина", 0, 4),
]
NORTH = "00000000-0000-4000-8000-000000000201"
SOUTH = "00000000-0000-4000-8000-000000000202"


def seed():
    with Session.begin() as db:
        db.execute(text("SELECT pg_advisory_xact_lock(73190601)"))
        if db.scalar(select(Store).where(Store.id == STORE_ID)):
            return False
        db.add(Store(id=STORE_ID, name="Демо-магазин"))
        db.flush()
        db.add_all(
            [
                Warehouse(id=NORTH, store_id=STORE_ID, name="Север"),
                Warehouse(id=SOUTH, store_id=STORE_ID, name="Юг"),
            ]
        )
        for pid, sku, name, _, _ in PRODUCTS:
            db.add(Product(id=pid, store_id=STORE_ID, sku=sku, name=name))
        db.flush()
        inventory = Inventory(db)
        for pid, _, _, north, south in PRODUCTS:
            for wid, qty in [(NORTH, north), (SOUTH, south)]:
                if qty:
                    payload = {"product_id": pid, "warehouse_id": wid, "quantity": qty}
                    inventory.execute(
                        f"seed:{pid}:{wid}",
                        "receipt",
                        payload,
                        lambda p=payload: inventory.receipt(**p),
                    )
    return True
