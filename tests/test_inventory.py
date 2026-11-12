from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.inventory import Inventory, STORE_ID
from app.models import Document, Event, Position, Product, Store, Warehouse
from app.seed import NORTH, SOUTH, PRODUCTS

P1, P2, P3 = [p[0] for p in PRODUCTS]


def snapshot(client):
    response = client.get("/api/v1/state")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["integrity"]["ok"], result["integrity"]
    return result


def write(client, path, body=None, key=None, expected=200, method="POST"):
    response = client.request(
        method,
        "/api/v1/" + path,
        json=body,
        headers={"Idempotency-Key": key or str(uuid4())},
    )
    assert response.status_code == expected, response.text
    snapshot(client)
    return response.json()


def values(client, pid=P1, wid=NORTH):
    return next(s for s in snapshot(client)["stocks"] if s["product_id"] == pid)[
        "warehouses"
    ][wid]


def reserve(client, qty, pid=P1, wid=NORTH, **kwargs):
    return write(
        client,
        "fbs-orders",
        {"product_id": pid, "warehouse_id": wid, "quantity": qty},
        **kwargs,
    )


def transfer(client, qty, source=NORTH, destination=SOUTH, pid=P1, **kwargs):
    return write(
        client,
        "transfers",
        {
            "product_id": pid,
            "source_id": source,
            "destination_id": destination,
            "quantity": qty,
        },
        **kwargs,
    )


def fbo(client, qty, **kwargs):
    return write(
        client,
        "fbo-shipments",
        {
            "product_id": P1,
            "warehouse_id": SOUTH,
            "quantity": qty,
            "destination": "WB · Склад",
        },
        **kwargs,
    )


def test_seed_and_repeat_seed(client):
    from app.seed import seed

    before = snapshot(client)
    assert before["summary"] == {
        "physical": 25,
        "reserved": 0,
        "available_active": 25,
        "archived_physical": 0,
        "transfer": 0,
        "fbo_transit": 0,
        "fbs_transit": 0,
        "marketplace": 0,
    }
    assert len(before["history"]) == 4
    assert values(client, P3, NORTH)["physical"] == 0
    assert seed() is False
    assert snapshot(client) == before


def test_receipt_is_auditable_and_idempotent(client):
    body = {"product_id": P2, "warehouse_id": SOUTH, "quantity": 7}
    key = str(uuid4())
    doc = write(client, "receipts", body, key=key)
    repeated = write(client, "receipts", body, key=key)
    assert repeated["id"] == doc["id"] and repeated["replayed"]
    assert values(client, P2, SOUTH) == {"physical": 7, "reserved": 0, "available": 7}
    assert len(snapshot(client)["history"]) == 5
    write(client, "receipts", body | {"quantity": 8}, key=key, expected=409)
    assert values(client, P2, SOUTH)["physical"] == 7


def test_fbs_reserve_cancel_ship(client):
    doc = reserve(client, 3)
    assert values(client) == {"physical": 10, "reserved": 3, "available": 7}
    write(client, f"fbs-orders/{doc['id']}/cancel")
    write(client, f"fbs-orders/{doc['id']}/cancel")
    assert values(client) == {"physical": 10, "reserved": 0, "available": 10}
    write(client, f"fbs-orders/{doc['id']}/ship", expected=409)
    doc = reserve(client, 3)
    write(client, f"fbs-orders/{doc['id']}/ship")
    before = snapshot(client)
    assert values(client) == {"physical": 7, "reserved": 0, "available": 7}
    assert before["summary"]["fbs_transit"] == 3
    assert write(client, f"fbs-orders/{doc['id']}/ship")["replayed"]
    write(client, f"fbs-orders/{doc['id']}/cancel", expected=409)
    assert snapshot(client) == before


def test_transfer_dispatch_and_receive_once(client):
    doc = transfer(client, 4)
    assert values(client)["physical"] == 6
    assert values(client, wid=SOUTH)["physical"] == 6
    assert snapshot(client)["summary"]["transfer"] == 4
    write(client, f"transfers/{doc['id']}/receive")
    assert values(client, wid=SOUTH)["physical"] == 10
    assert snapshot(client)["summary"]["transfer"] == 0
    before = snapshot(client)
    assert write(client, f"transfers/{doc['id']}/receive")["replayed"]
    assert snapshot(client) == before


def test_fbo_receipt_keeps_marketplace_stock(client):
    doc = fbo(client, 4)
    assert values(client, wid=SOUTH)["physical"] == 2
    assert snapshot(client)["summary"]["fbo_transit"] == 4
    write(client, f"fbo-shipments/{doc['id']}/receive")
    summary = snapshot(client)["summary"]
    assert summary["fbo_transit"] == 0 and summary["marketplace"] == 4
    before = snapshot(client)
    assert write(client, f"fbo-shipments/{doc['id']}/receive")["replayed"]
    assert snapshot(client) == before


@pytest.mark.parametrize("operation", ["reserve", "transfer", "fbo"])
def test_reserved_goods_cannot_be_spent(client, operation):
    reserve(client, 9)
    before = snapshot(client)
    if operation == "reserve":
        reserve(client, 2, expected=409)
    elif operation == "transfer":
        transfer(client, 2, expected=409)
    else:
        write(
            client,
            "fbo-shipments",
            {"product_id": P1, "warehouse_id": NORTH, "quantity": 2},
            expected=409,
        )
    assert snapshot(client) == before


def test_shortage_selected_ff_and_guided_replenishment(client):
    before = snapshot(client)
    error = reserve(client, 12, expected=409)["error"]
    assert (error["available"], error["shortage"], error["total_available"]) == (
        10,
        2,
        16,
    )
    assert error["alternatives"][0]["warehouse_id"] == SOUTH
    assert error["alternatives"][0]["quantity"] == 2
    assert snapshot(client) == before
    doc = transfer(client, 2, SOUTH, NORTH)
    reserve(client, 12, expected=409)  # Goods in transit are not available yet.
    write(client, f"transfers/{doc['id']}/receive")
    reserve(client, 12)
    assert values(client) == {"physical": 12, "reserved": 12, "available": 0}


def test_empty_warehouse_and_insufficient_total(client):
    before = snapshot(client)
    error = reserve(client, 10, pid=P3, expected=409)["error"]
    assert error["total_available"] == 4 and error["shortage"] == 10
    assert snapshot(client) == before
    transfer(client, 1, pid=P2, source=SOUTH, destination=NORTH, expected=409)


def test_warehouse_lifecycle_history_and_duplicate_names(client):
    new = write(client, "warehouses", {"name": "  Запад  "})
    assert new["name"] == "Запад"
    write(client, "warehouses", {"name": "запад"}, expected=409)
    write(client, f"warehouses/{new['id']}", {"name": "Центр"}, method="PATCH")
    write(client, f"warehouses/{new['id']}", method="DELETE")
    before = snapshot(client)
    assert write(client, f"warehouses/{new['id']}", method="DELETE")["replayed"]
    assert snapshot(client) == before
    write(client, "warehouses", {"name": "Центр"}, expected=409)
    events = snapshot(client)["history"]
    rename = next(e for e in events if e["action"] == "WAREHOUSE_RENAMED")
    assert rename["source"] == "Запад" and rename["destination"] == "Центр"


def test_archive_with_stock_allows_evacuation_not_new_work(client):
    write(client, f"warehouses/{NORTH}", method="DELETE")
    state = snapshot(client)
    assert state["summary"]["archived_physical"] == 15
    assert state["summary"]["available_active"] == 10
    assert next(w for w in state["warehouses"] if w["id"] == NORTH)["visible"]
    reserve(client, 1, expected=409)
    write(
        client,
        "receipts",
        {"product_id": P1, "warehouse_id": NORTH, "quantity": 1},
        expected=409,
    )
    write(
        client,
        "fbo-shipments",
        {"product_id": P1, "warehouse_id": NORTH, "quantity": 1},
        expected=409,
    )
    doc = transfer(client, 10)
    write(client, f"transfers/{doc['id']}/receive")
    assert values(client)["physical"] == 0
    assert values(client, wid=SOUTH)["physical"] == 16


@pytest.mark.parametrize("action", ["ship", "cancel"])
def test_archive_allows_existing_fbs_completion(client, action):
    doc = reserve(client, 3)
    write(client, f"warehouses/{NORTH}", method="DELETE")
    write(client, f"fbs-orders/{doc['id']}/{action}")
    assert values(client)["reserved"] == 0
    assert values(client)["physical"] == (7 if action == "ship" else 10)


def test_archive_allows_preexisting_incoming_transfer(client):
    doc = transfer(client, 2)
    write(client, f"warehouses/{SOUTH}", method="DELETE")
    write(client, f"transfers/{doc['id']}/receive")
    assert values(client, wid=SOUTH)["physical"] == 8
    transfer(
        client, 1, expected=409
    )  # New incoming transfers into archive are forbidden.


def test_renaming_preserves_document_names(client):
    doc = transfer(client, 1)
    write(client, f"warehouses/{NORTH}", {"name": "Северный"}, method="PATCH")
    write(client, f"transfers/{doc['id']}/receive")
    events = [e for e in snapshot(client)["history"] if e["document_id"] == doc["id"]]
    assert all(e["source"] == "Север" for e in events)


@pytest.mark.parametrize("quantity", [0, -1, 1.5, "2", True, 1000001])
def test_invalid_quantities_never_change_stock(client, quantity):
    before = snapshot(client)
    reserve(client, quantity, expected=422)
    assert snapshot(client) == before


def test_invalid_identifiers_and_routes(client):
    reserve(client, 1, wid=str(uuid4()), expected=404)
    reserve(client, 1, pid=str(uuid4()), expected=404)
    reserve(client, 1, wid="bad", expected=422)
    transfer(client, 1, destination=NORTH, expected=409)
    doc = reserve(client, 1)
    write(client, f"fbo-shipments/{doc['id']}/receive", expected=404)
    write(client, f"transfers/{uuid4()}/receive", expected=404)
    assert client.post("/api/v1/warehouses", json={"name": "Новый"}).status_code == 422
    write(client, "warehouses", {"name": "  "}, expected=422)


def concurrently(*commands):
    barrier = Barrier(len(commands))

    def invoke(command):
        barrier.wait(timeout=10)
        return command()

    with ThreadPoolExecutor(max_workers=len(commands)) as executor:
        return list(executor.map(invoke, commands))


def raw_reserve(client, quantity, key=None):
    return client.post(
        "/api/v1/fbs-orders",
        json={"product_id": P1, "warehouse_id": NORTH, "quantity": quantity},
        headers={"Idempotency-Key": key or str(uuid4())},
    )


def test_concurrent_last_units_have_single_winner(client):
    responses = concurrently(
        lambda: raw_reserve(client, 8), lambda: raw_reserve(client, 8)
    )
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert values(client) == {"physical": 10, "reserved": 8, "available": 2}


def test_concurrent_identical_key_creates_one_document(client):
    key = str(uuid4())
    responses = concurrently(
        lambda: raw_reserve(client, 3, key), lambda: raw_reserve(client, 3, key)
    )
    assert all(r.status_code == 200 for r in responses)
    assert responses[0].json()["id"] == responses[1].json()["id"]
    assert sorted(r.json()["replayed"] for r in responses) == [False, True]
    assert values(client)["reserved"] == 3


def test_concurrent_receive_is_applied_once(client):
    doc = transfer(client, 3)

    def receive():
        return client.post(
            f"/api/v1/transfers/{doc['id']}/receive",
            headers={"Idempotency-Key": str(uuid4())},
        )

    responses = concurrently(receive, receive)
    assert all(r.status_code == 200 for r in responses)
    assert values(client, wid=SOUTH)["physical"] == 9
    assert snapshot(client)["summary"]["transfer"] == 0


def test_concurrent_ship_and_cancel_are_exclusive(client):
    doc = reserve(client, 3)

    def act(action):
        return client.post(
            f"/api/v1/fbs-orders/{doc['id']}/{action}",
            headers={"Idempotency-Key": str(uuid4())},
        )

    responses = concurrently(lambda: act("ship"), lambda: act("cancel"))
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert values(client)["reserved"] == 0


def test_unexpected_failure_rolls_back_all_postings(client, monkeypatch):
    original = Inventory.post

    def fail_after_post(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise RuntimeError("simulated interruption")

    before = snapshot(client)
    monkeypatch.setattr(Inventory, "post", fail_after_post)
    with pytest.raises(RuntimeError, match="simulated"):
        transfer(client, 3)
    assert snapshot(client) == before


def test_database_rejects_invalid_balance_and_journal_edits(client):
    with pytest.raises(IntegrityError):
        with client.db_factory.begin() as db:
            db.execute(
                text(
                    "UPDATE positions SET reserved = quantity + 1 WHERE warehouse_id IS NOT NULL"
                )
            )
    with pytest.raises(DBAPIError, match="append-only"):
        with client.db_factory.begin() as db:
            db.execute(text("UPDATE events SET quantity = 99"))
    with pytest.raises(DBAPIError, match="append-only"):
        with client.db_factory.begin() as db:
            db.execute(text("DELETE FROM postings"))
    snapshot(client)


def test_store_isolation(client):
    with client.db_factory.begin() as db:
        other = Store(name="Другой магазин")
        db.add(other)
        db.flush()
        w = Warehouse(store_id=other.id, name="Север")
        p = Product(store_id=other.id, sku="SKU-101", name="Другой товар")
        db.add_all([w, p])
        db.flush()
        other_wid, other_pid = w.id, p.id
    reserve(client, 1, wid=other_wid, expected=404)
    reserve(client, 1, pid=other_pid, expected=404)
    assert snapshot(client)["summary"]["physical"] == 25


def test_complete_demo_scenario(client):
    reserve(client, 12, expected=409)
    doc = transfer(client, 2, SOUTH, NORTH)
    write(client, f"transfers/{doc['id']}/receive")
    write(client, f"transfers/{doc['id']}/receive")
    doc = reserve(client, 3)
    write(client, f"fbs-orders/{doc['id']}/ship")
    doc = fbo(client, 4)
    write(client, f"fbo-shipments/{doc['id']}/receive")
    write(client, f"warehouses/{NORTH}", method="DELETE")
    sku = next(s for s in snapshot(client)["stocks"] if s["product_id"] == P1)
    assert (sku["physical"], sku["fbs_transit"], sku["marketplace"]) == (9, 3, 4)
    assert (
        sum(
            sku[k]
            for k in [
                "physical",
                "fbs_transit",
                "marketplace",
                "transfer",
                "fbo_transit",
            ]
        )
        == 16
    )


def test_page_health_and_openapi(client):
    assert client.get("/").status_code == 200
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/static/app.js").status_code == 200
    schema = client.get("/openapi.json").json()
    assert "/api/v1/transfers/{document_id}/receive" in schema["paths"]
