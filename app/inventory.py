import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Document,
    Event,
    Idempotency,
    Position,
    Posting,
    Product,
    Store,
    Warehouse,
)

STORE_ID = "00000000-0000-4000-8000-000000000001"


class DomainError(Exception):
    def __init__(self, code, message, status=409, **details):
        self.code, self.message, self.status, self.details = (
            code,
            message,
            status,
            details,
        )


def document_json(doc):
    return {
        key: getattr(doc, key)
        for key in (
            "id",
            "product_id",
            "kind",
            "status",
            "quantity",
            "source_id",
            "destination_id",
            "source_name",
            "destination_name",
        )
    } | {"created_at": doc.created_at.isoformat()}


class Inventory:
    def __init__(self, session: Session, store_id=STORE_ID):
        self.db, self.store_id = session, store_id

    def execute(self, key, action, payload, command):
        store = self.db.scalar(
            select(Store).where(Store.id == self.store_id).with_for_update()
        )
        if not store:
            raise DomainError("store_not_found", "Магазин не найден.", 404)
        fingerprint = hashlib.sha256(
            json.dumps(
                [action, payload],
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        previous = self.db.scalar(
            select(Idempotency).where(
                Idempotency.store_id == self.store_id, Idempotency.key == key
            )
        )
        if previous:
            if previous.fingerprint != fingerprint:
                raise DomainError(
                    "idempotency_conflict",
                    "Этот ключ уже использован для другой операции.",
                )
            return previous.response | {"replayed": True}
        result = command()
        self.db.flush()
        self.db.add(
            Idempotency(
                store_id=self.store_id,
                key=key,
                fingerprint=fingerprint,
                response=result,
            )
        )
        self.db.flush()
        return result | {"replayed": result.get("replayed", False)}

    def product(self, product_id):
        product = self.db.scalar(
            select(Product).where(
                Product.id == product_id, Product.store_id == self.store_id
            )
        )
        if not product:
            raise DomainError(
                "product_not_found", "Товар не найден в этом магазине.", 404
            )
        return product

    def warehouse(self, warehouse_id, active=True):
        warehouse = self.db.scalar(
            select(Warehouse).where(
                Warehouse.id == warehouse_id, Warehouse.store_id == self.store_id
            )
        )
        if not warehouse:
            raise DomainError(
                "warehouse_not_found", "Склад не найден в этом магазине.", 404
            )
        if active and warehouse.archived:
            raise DomainError(
                "warehouse_archived",
                "Склад в архиве. Доступны вывоз и завершение начатых операций.",
            )
        return warehouse

    def position(self, product_id, account, warehouse_id=None):
        position = self.db.scalar(
            select(Position)
            .where(
                Position.store_id == self.store_id,
                Position.product_id == product_id,
                Position.account == account,
            )
            .with_for_update()
        )
        if not position:
            position = Position(
                store_id=self.store_id,
                product_id=product_id,
                account=account,
                warehouse_id=warehouse_id,
                quantity=0,
                reserved=0,
            )
            self.db.add(position)
            self.db.flush()
        return position

    def ff(self, product_id, warehouse_id):
        return self.position(product_id, f"ff:{warehouse_id}", warehouse_id)

    def require_available(self, position, quantity):
        available = position.quantity - position.reserved
        if quantity <= available:
            return
        alternatives = []
        rows = self.db.execute(
            select(Position, Warehouse)
            .join(Warehouse, Position.warehouse_id == Warehouse.id)
            .where(
                Position.store_id == self.store_id,
                Position.product_id == position.product_id,
                Position.warehouse_id != position.warehouse_id,
            )
        ).all()
        for other, warehouse in rows:
            free = other.quantity - other.reserved
            if free > 0:
                alternatives.append(
                    {
                        "warehouse_id": warehouse.id,
                        "name": warehouse.name,
                        "available": free,
                        "archived": warehouse.archived,
                        "quantity": min(quantity - available, free),
                    }
                )
        alternatives.sort(key=lambda x: (-x["available"], x["name"]))
        raise DomainError(
            "insufficient_stock",
            f"На выбранном ФФ доступно {available} из {quantity} шт.",
            requested=quantity,
            available=available,
            shortage=quantity - available,
            total_available=available + sum(x["available"] for x in alternatives),
            alternatives=alternatives,
        )

    def event(self, action, doc=None, source="", destination="", details=None):
        event = Event(
            store_id=self.store_id,
            action=action,
            document_id=doc.id if doc else None,
            product_id=doc.product_id if doc else None,
            quantity=doc.quantity if doc else 0,
            source=source or (doc.source_name if doc else ""),
            destination=destination or (doc.destination_name if doc else ""),
            details=details or {},
        )
        self.db.add(event)
        self.db.flush()
        return event

    def post(self, event, position, quantity=0, reserved=0):
        position.quantity += quantity
        position.reserved += reserved
        self.db.add(
            Posting(
                event_id=event.id,
                position_id=position.id,
                quantity_delta=quantity,
                reserved_delta=reserved,
            )
        )
        self.db.flush()

    def create_document(
        self,
        kind,
        status,
        product_id,
        quantity,
        source=None,
        destination=None,
        partner=None,
    ):
        self.product(product_id)
        doc = Document(
            store_id=self.store_id,
            kind=kind,
            status=status,
            product_id=product_id,
            quantity=quantity,
            source_id=source.id if source else None,
            destination_id=destination.id if destination else None,
            source_name=source.name if source else "Поставщик",
            destination_name=destination.name if destination else (partner or ""),
        )
        self.db.add(doc)
        self.db.flush()
        return doc

    def receipt(self, product_id, warehouse_id, quantity):
        warehouse = self.warehouse(warehouse_id)
        doc = self.create_document(
            "RECEIPT", "RECEIVED", product_id, quantity, destination=warehouse
        )
        event = self.event("RECEIPT", doc)
        self.post(event, self.ff(product_id, warehouse_id), quantity)
        return document_json(doc)

    def transfer(self, product_id, source_id, destination_id, quantity):
        if source_id == destination_id:
            raise DomainError(
                "same_warehouse", "Выберите разные склады отправления и приёмки."
            )
        source = self.warehouse(source_id, active=False)
        destination = self.warehouse(destination_id)
        self.product(product_id)
        stock = self.ff(product_id, source_id)
        self.require_available(stock, quantity)
        doc = self.create_document(
            "TRANSFER", "IN_TRANSIT", product_id, quantity, source, destination
        )
        event = self.event("TRANSFER_SENT", doc)
        self.post(event, stock, -quantity)
        self.post(event, self.position(product_id, "transfer"), quantity)
        return document_json(doc)

    def reserve(self, product_id, warehouse_id, quantity):
        warehouse = self.warehouse(warehouse_id)
        self.product(product_id)
        stock = self.ff(product_id, warehouse_id)
        self.require_available(stock, quantity)
        doc = self.create_document(
            "FBS",
            "RESERVED",
            product_id,
            quantity,
            source=warehouse,
            partner="Клиент FBS",
        )
        event = self.event("FBS_RESERVED", doc)
        self.post(event, stock, reserved=quantity)
        return document_json(doc)

    def fbo(self, product_id, warehouse_id, quantity, destination):
        warehouse = self.warehouse(warehouse_id)
        self.product(product_id)
        stock = self.ff(product_id, warehouse_id)
        self.require_available(stock, quantity)
        doc = self.create_document(
            "FBO",
            "IN_TRANSIT",
            product_id,
            quantity,
            source=warehouse,
            partner=destination,
        )
        event = self.event("FBO_SENT", doc)
        self.post(event, stock, -quantity)
        self.post(event, self.position(product_id, "fbo_transit"), quantity)
        return document_json(doc)

    def transition(self, document_id, kind, action):
        doc = self.db.scalar(
            select(Document)
            .where(
                Document.id == document_id,
                Document.store_id == self.store_id,
                Document.kind == kind,
            )
            .with_for_update()
        )
        if not doc:
            raise DomainError("document_not_found", "Документ не найден.", 404)
        target = {"receive": "RECEIVED", "ship": "SHIPPED", "cancel": "CANCELLED"}[
            action
        ]
        if doc.status == target:
            return document_json(doc) | {"replayed": True}
        expected = "RESERVED" if kind == "FBS" else "IN_TRANSIT"
        if doc.status != expected:
            raise DomainError(
                "invalid_transition", "Документ уже завершён другим действием."
            )
        qty, pid = doc.quantity, doc.product_id
        if kind == "FBS":
            stock = self.ff(pid, doc.source_id)
            event = self.event(
                "FBS_SHIPPED" if action == "ship" else "FBS_CANCELLED", doc
            )
            self.post(event, stock, -qty if action == "ship" else 0, -qty)
            if action == "ship":
                self.post(event, self.position(pid, "fbs_transit"), qty)
        elif kind == "TRANSFER":
            event = self.event("TRANSFER_RECEIVED", doc)
            self.post(event, self.position(pid, "transfer"), -qty)
            self.post(event, self.ff(pid, doc.destination_id), qty)
        else:
            event = self.event("FBO_RECEIVED", doc)
            self.post(event, self.position(pid, "fbo_transit"), -qty)
            self.post(event, self.position(pid, "marketplace"), qty)
        doc.status = target
        self.db.flush()
        return document_json(doc)

    def create_warehouse(self, name):
        self.check_name(name)
        warehouse = Warehouse(store_id=self.store_id, name=name)
        self.db.add(warehouse)
        self.db.flush()
        self.event(
            "WAREHOUSE_CREATED",
            destination=name,
            details={"warehouse_id": warehouse.id},
        )
        return {"id": warehouse.id, "name": warehouse.name, "archived": False}

    def check_name(self, name, exclude=None):
        from sqlalchemy import func

        existing = self.db.scalar(
            select(Warehouse).where(
                Warehouse.store_id == self.store_id,
                func.lower(Warehouse.name) == name.lower(),
                Warehouse.id != exclude if exclude else True,
            )
        )
        if existing:
            raise DomainError(
                "duplicate_warehouse",
                "Склад с таким названием уже существует, в том числе в архиве.",
            )

    def rename_warehouse(self, warehouse_id, name):
        warehouse = self.warehouse(warehouse_id, active=False)
        if warehouse.name != name:
            self.check_name(name, exclude=warehouse_id)
            self.event(
                "WAREHOUSE_RENAMED",
                source=warehouse.name,
                destination=name,
                details={"warehouse_id": warehouse.id},
            )
            warehouse.name = name
        return {"id": warehouse.id, "name": name, "archived": warehouse.archived}

    def archive_warehouse(self, warehouse_id):
        warehouse = self.warehouse(warehouse_id, active=False)
        replayed = warehouse.archived
        if not warehouse.archived:
            self.event(
                "WAREHOUSE_ARCHIVED",
                source=warehouse.name,
                details={"warehouse_id": warehouse.id},
            )
            warehouse.archived = True
        return {
            "id": warehouse.id,
            "name": warehouse.name,
            "archived": True,
            "replayed": replayed,
        }
