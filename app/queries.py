from collections import defaultdict

from sqlalchemy import func, select

from app.inventory import STORE_ID, document_json
from app.models import Document, Event, Position, Posting, Product, Store, Warehouse


def reconcile(db, store_id=STORE_ID):
    positions = list(db.scalars(select(Position).where(Position.store_id == store_id)))
    sums = {
        pid: (q or 0, r or 0)
        for pid, q, r in db.execute(
            select(
                Posting.position_id,
                func.sum(Posting.quantity_delta),
                func.sum(Posting.reserved_delta),
            )
            .join(Position)
            .where(Position.store_id == store_id)
            .group_by(Posting.position_id)
        )
    }
    docs = list(db.scalars(select(Document).where(Document.store_id == store_id)))
    discrepancies = []
    for pos in positions:
        if (pos.quantity, pos.reserved) != sums.get(pos.id, (0, 0)):
            discrepancies.append(f"journal:{pos.id}")
    received = sum(d.quantity for d in docs if d.kind == "RECEIPT")
    actual = sum(p.quantity for p in positions)
    if received != actual:
        discrepancies.append("conservation")
    expected = defaultdict(int)
    for doc in docs:
        if doc.kind == "FBS" and doc.status == "RESERVED":
            expected[(doc.product_id, f"reserve:{doc.source_id}")] += doc.quantity
        account = {
            ("TRANSFER", "IN_TRANSIT"): "transfer",
            ("FBO", "IN_TRANSIT"): "fbo_transit",
            ("FBO", "RECEIVED"): "marketplace",
            ("FBS", "SHIPPED"): "fbs_transit",
        }.get((doc.kind, doc.status))
        if account:
            expected[(doc.product_id, account)] += doc.quantity
    actual_docs = defaultdict(int)
    for pos in positions:
        if pos.warehouse_id:
            actual_docs[(pos.product_id, f"reserve:{pos.warehouse_id}")] += pos.reserved
        else:
            actual_docs[(pos.product_id, pos.account)] += pos.quantity
    if {k: v for k, v in expected.items() if v} != {
        k: v for k, v in actual_docs.items() if v
    }:
        discrepancies.append("document_positions")
    # Per-SKU conservation prevents one SKU from hiding a discrepancy in another.
    for pid in {p.product_id for p in positions} | {d.product_id for d in docs}:
        if sum(p.quantity for p in positions if p.product_id == pid) != sum(
            d.quantity for d in docs if d.product_id == pid and d.kind == "RECEIPT"
        ):
            discrepancies.append(f"conservation:{pid}")
    return {
        "ok": not discrepancies,
        "received": received,
        "accounted": actual,
        "discrepancies": discrepancies,
    }


def state(db, store_id=STORE_ID):
    store = db.get(Store, store_id)
    products = list(
        db.scalars(
            select(Product).where(Product.store_id == store_id).order_by(Product.sku)
        )
    )
    warehouses = list(
        db.scalars(
            select(Warehouse)
            .where(Warehouse.store_id == store_id)
            .order_by(Warehouse.created_at, Warehouse.id)
        )
    )
    positions = list(db.scalars(select(Position).where(Position.store_id == store_id)))
    docs = list(
        db.scalars(
            select(Document)
            .where(Document.store_id == store_id)
            .order_by(Document.created_at.desc(), Document.id)
        )
    )
    stocks = []
    summary = dict(
        physical=0,
        reserved=0,
        available_active=0,
        archived_physical=0,
        transfer=0,
        fbo_transit=0,
        fbs_transit=0,
        marketplace=0,
    )
    by_key = {(p.product_id, p.account): p for p in positions}
    for product in products:
        item = {
            "product_id": product.id,
            "sku": product.sku,
            "name": product.name,
            "warehouses": {},
            "physical": 0,
            "reserved": 0,
            "available_active": 0,
        }
        for warehouse in warehouses:
            pos = by_key.get((product.id, f"ff:{warehouse.id}"))
            qty, reserved = (pos.quantity, pos.reserved) if pos else (0, 0)
            item["warehouses"][warehouse.id] = {
                "physical": qty,
                "reserved": reserved,
                "available": qty - reserved,
            }
            item["physical"] += qty
            item["reserved"] += reserved
            if not warehouse.archived:
                item["available_active"] += qty - reserved
            else:
                summary["archived_physical"] += qty
        for account in ("transfer", "fbo_transit", "fbs_transit", "marketplace"):
            pos = by_key.get((product.id, account))
            item[account] = pos.quantity if pos else 0
            summary[account] += item[account]
        for field in ("physical", "reserved", "available_active"):
            summary[field] += item[field]
        stocks.append(item)
    events = list(
        db.scalars(
            select(Event)
            .where(Event.store_id == store_id)
            .order_by(Event.created_at.desc(), Event.id)
            .limit(200)
        )
    )
    pending_warehouse_ids = {
        wid
        for d in docs
        if d.status in ("RESERVED", "IN_TRANSIT")
        for wid in (d.source_id, d.destination_id)
        if wid
    }
    visible_warehouse_ids = {
        p.warehouse_id for p in positions if p.quantity or p.reserved
    } | pending_warehouse_ids
    return {
        "store": {"id": store.id, "name": store.name} if store else None,
        "products": [{"id": p.id, "sku": p.sku, "name": p.name} for p in products],
        "warehouses": [
            {
                "id": w.id,
                "name": w.name,
                "archived": w.archived,
                "visible": not w.archived or w.id in visible_warehouse_ids,
            }
            for w in warehouses
        ],
        "stocks": stocks,
        "summary": summary,
        "documents": [document_json(d) for d in docs],
        "history": [
            {
                "id": e.id,
                "document_id": e.document_id,
                "product_id": e.product_id,
                "action": e.action,
                "quantity": e.quantity,
                "source": e.source,
                "destination": e.destination,
                "details": e.details,
                "created_at": e.created_at.isoformat(),
            }
            for e in events
        ],
        "integrity": reconcile(db, store_id),
    }
