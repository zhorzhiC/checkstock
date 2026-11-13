"""Execute the 15-minute scenario ONLY against rehearsal at 127.0.0.1:8003."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from runpy import run_path

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8003/api/v1/"
PRODUCT = "00000000-0000-4000-8000-000000000101"
CONTAINER = "00000000-0000-4000-8000-000000000102"
BASKET = "00000000-0000-4000-8000-000000000103"
NORTH = "00000000-0000-4000-8000-000000000201"
SOUTH = "00000000-0000-4000-8000-000000000202"
CHECKPOINTS = []


def request(path, body=None, *, key=None, method="GET", expected=200):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Idempotency-Key"] = "video-rehearsal:" + key
    req = Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers, method=method,
    )
    try:
        with urlopen(req, timeout=15) as response:
            status, data = response.status, json.load(response)
    except HTTPError as error:
        status, data = error.code, json.load(error)
    assert status == expected, (path, status, expected, data)
    return data


def capture(label, **expected_summary):
    data = request("state")
    assert data["integrity"]["ok"], data["integrity"]
    for field, value in expected_summary.items():
        assert data["summary"][field] == value, (label, field, data["summary"])
    CHECKPOINTS.append(dict(label=label, state=data))
    print(f"PASS: {label}; documents={len(data['documents'])}; events={len(data['history'])}")
    return data


def write(path, key, body=None, method="POST"):
    return request(path, body, key=key, method=method)


def reject(path, key, body, code, **details):
    before = request("state")
    result = request(path, body, key=key, method="POST", expected=409)
    assert result["error"]["code"] == code, result
    for field, value in details.items():
        assert result["error"][field] == value, result
    after = capture(key)
    assert after == before, (key, "Rejected operation changed API state")


def shipment(quantity, warehouse_id=NORTH):
    return dict(product_id=PRODUCT, warehouse_id=warehouse_id, quantity=quantity)


def transfer(quantity, source_id, destination_id, product_id=PRODUCT):
    return dict(product_id=product_id, source_id=source_id, destination_id=destination_id, quantity=quantity)


def stock(data, sku, wid):
    return next(s for s in data["stocks"] if s["sku"] == sku)["warehouses"][wid]


def run(resume_transfer_sent):
    verify = run_path(str(ROOT / "scripts" / "video-verify.py"))["verify"]
    initial = request("state")
    if resume_transfer_sent:
        # Only the exact partial UI rehearsal is resumable; never guess a target.
        assert len(initial["documents"]) == 6 and len(initial["history"]) == 7
        assert initial["summary"] == dict(physical=28, reserved=0, available_active=28,
            archived_physical=0, transfer=2, fbo_transit=0, fbs_transit=0, marketplace=0)
        east = next(w["id"] for w in initial["warehouses"] if w["name"] == "Восток")
        assert len(initial["warehouses"]) == 3
        assert stock(initial, "SKU-103", east)["physical"] == 5
        candidates = [d for d in initial["documents"] if d["kind"] == "TRANSFER"]
        assert len(candidates) == 1
        first = candidates[0]
        assert (first["product_id"], first["quantity"], first["source_id"], first["destination_id"], first["status"]) == (
            PRODUCT, 2, SOUTH, NORTH, "IN_TRANSIT")
        capture("resume-after-ui-transfer-sent", transfer=2)
    else:
        verify(initial, "initial")  # Refuse to replay against an already-used database.
        capture("initial", physical=25)
        east = write("warehouses", "east", dict(name="Восток"))["id"]
        write("receipts", "receipt", dict(product_id=BASKET, warehouse_id=east, quantity=5))
        capture("receipt", physical=30, reserved=0)
        reject("fbs-orders", "shortage", shipment(12), "insufficient_stock", available=10, shortage=2)
        first = write("transfers", "transfer", transfer(2, SOUTH, NORTH))
        capture("transfer-sent", physical=28, transfer=2)

    write(f"transfers/{first['id']}/receive", "receive")
    received = capture("transfer-received", physical=30, transfer=0)
    assert stock(received, "SKU-101", NORTH)["physical"] == 12
    repeated = write(f"transfers/{first['id']}/receive", "receive-with-new-key")
    assert repeated["replayed"] is True
    assert capture("receive-repeated") == received

    order = write("fbs-orders", "reserve-first", shipment(3))
    reserved = capture("reserved", physical=30, reserved=3, available_active=27)
    assert stock(reserved, "SKU-101", NORTH) == dict(physical=12, reserved=3, available=9)
    reject("transfers", "reserved-stock-rejected", transfer(10, NORTH, east),
        "insufficient_stock", available=9, shortage=1)
    write(f"fbs-orders/{order['id']}/cancel", "cancel")
    capture("cancelled", reserved=0, available_active=30)
    order = write("fbs-orders", "reserve-second", shipment(3))
    write(f"fbs-orders/{order['id']}/ship", "ship")
    shipped = capture("fbs-shipped", physical=27, reserved=0, available_active=27, fbs_transit=3)
    assert stock(shipped, "SKU-101", NORTH)["physical"] == 9

    fbo = write("fbo-shipments", "fbo", shipment(4, SOUTH) | dict(destination="Склад маркетплейса"))
    capture("fbo-sent", physical=23, fbo_transit=4, fbs_transit=3)
    write(f"fbo-shipments/{fbo['id']}/receive", "fbo-receive")
    capture("fbo-received", physical=23, fbo_transit=0, marketplace=4)
    write(f"warehouses/{NORTH}", "rename", dict(name="Северный"), method="PATCH")
    renamed = capture("renamed")
    original = next(d for d in renamed["documents"] if d["id"] == first["id"])
    assert original["destination_name"] == "Север"
    write(f"warehouses/{NORTH}", "archive", method="DELETE")
    capture("archived", physical=23, archived_physical=14, available_active=9)
    reject("fbs-orders", "archive-rejected", shipment(1), "warehouse_archived")

    evacuation = write("transfers", "evacuate-organizers", transfer(9, NORTH, east))
    capture("evacuate-organizers-sent", physical=14, transfer=9, archived_physical=5)
    write(f"transfers/{evacuation['id']}/receive", "evacuate-organizers-receive")
    capture("evacuate-organizers-received", physical=23, transfer=0, archived_physical=5, available_active=18)
    evacuation = write("transfers", "evacuate-containers", transfer(5, NORTH, east, CONTAINER))
    capture("evacuate-containers-sent", physical=18, transfer=5, archived_physical=0)
    write(f"transfers/{evacuation['id']}/receive", "evacuate-containers-receive")
    final = capture("final", physical=23, transfer=0, available_active=23)
    verify(final, "final")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume-transfer-sent", action="store_true",
        help="Continue only the exact checked UI checkpoint: 6 documents / 7 events / 2 in transit")
    args = parser.parse_args()
    path = ROOT / "tmp" / "video" / "rehearsal-checkpoints.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    report = dict(started_at_utc=datetime.now(timezone.utc).isoformat(),
        base_url=BASE, resumed_after_ui_transfer=args.resume_transfer_sent, passed=False, checkpoints=CHECKPOINTS)
    try:
        run(args.resume_transfer_sent)
        report["passed"] = True
    except Exception as error:
        report["error"] = repr(error)
        # Preserve the successful rehearsal evidence if a later invocation is refused.
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = path.with_name(f"rehearsal-failed-{stamp}.json")
        raise
    finally:
        report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"PASS: full remaining video scenario; report: {path}")


if __name__ == "__main__":
    main()
