"""Read-only acceptance of the initial/final video state; never executes commands."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen


def verify(data, stage):
    final = stage == "final"
    total = 30 if final else 25
    expected_summary = dict(
        physical=23 if final else 25,
        reserved=0,
        available_active=23 if final else 25,
        archived_physical=0,
        transfer=0,
        fbo_transit=0,
        fbs_transit=3 if final else 0,
        marketplace=4 if final else 0,
    )
    assert data["summary"] == expected_summary, data["summary"]
    assert data["integrity"] == dict(
        ok=True, received=total, accounted=total, discrepancies=[]
    ), data["integrity"]
    assert len(data["documents"]) == (11 if final else 4)
    assert len(data["history"]) == (20 if final else 4)
    assert len(data["products"]) == 3
    warehouses = {w["name"]: w for w in data["warehouses"]}
    assert set(warehouses) == ({"Северный", "Юг", "Восток"} if final else {"Север", "Юг"})
    for name, warehouse in warehouses.items():
        archived = final and name == "Северный"
        assert warehouse["archived"] == archived
        assert warehouse["visible"] == (not archived)
    expected = (
        {"SKU-101": {"Восток": 9}, "SKU-102": {"Восток": 5}, "SKU-103": {"Юг": 4, "Восток": 5}}
        if final
        else {"SKU-101": {"Север": 10, "Юг": 6}, "SKU-102": {"Север": 5}, "SKU-103": {"Юг": 4}}
    )
    assert {s["sku"] for s in data["stocks"]} == set(expected)
    for stock in data["stocks"]:
        for name, warehouse in warehouses.items():
            quantity = expected[stock["sku"]].get(name, 0)
            assert stock["warehouses"][warehouse["id"]] == dict(
                physical=quantity, reserved=0, available=quantity
            ), (stock["sku"], name)
        for account in ("transfer", "fbo_transit", "fbs_transit", "marketplace"):
            wanted = (
                {"fbs_transit": 3, "marketplace": 4}.get(account, 0)
                if final and stock["sku"] == "SKU-101" else 0
            )
            assert stock[account] == wanted, (stock["sku"], account)
    if final:
        first = next(d for d in data["documents"] if d["kind"] == "TRANSFER" and d["quantity"] == 2)
        assert first["source_name"] == "Юг" and first["destination_name"] == "Север"
        events = [e for e in data["history"] if e["document_id"] == first["id"]]
        assert {e["action"] for e in events} == {"TRANSFER_SENT", "TRANSFER_RECEIVED"}
        assert len(events) == 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", choices=("recording", "rehearsal"), default="recording")
    parser.add_argument("--stage", choices=("initial", "final"), default="initial")
    args = parser.parse_args()
    port = 8002 if args.environment == "recording" else 8003
    url = f"http://127.0.0.1:{port}/api/v1/state"
    with urlopen(url, timeout=15) as response:
        data = json.load(response)
    verify(data, args.stage)
    report = dict(
        checked_at_utc=datetime.now(timezone.utc).isoformat(),
        url=url, environment=args.environment, stage=args.stage, passed=True, state=data,
    )
    destination = Path(__file__).resolve().parents[1] / "tmp" / "video"
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / f"{args.environment}-{args.stage}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"PASS: {args.environment} / {args.stage}; {len(data['documents'])} documents; {len(data['history'])} events")
    print(json.dumps(data["summary"], ensure_ascii=False))
    print(f"Report: {path}")


if __name__ == "__main__":
    main()
