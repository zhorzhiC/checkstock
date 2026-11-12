from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def uid():
    return str(uuid4())


def now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Store(Base):
    __tablename__ = "stores"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(120))


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("store_id", "sku"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    sku: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(160))


class Warehouse(Base):
    __tablename__ = "warehouses"
    __table_args__ = (UniqueConstraint("store_id", "name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (
        UniqueConstraint("store_id", "product_id", "account"),
        CheckConstraint("quantity >= 0", name="quantity_nonnegative"),
        CheckConstraint("reserved >= 0 AND reserved <= quantity", name="valid_reserve"),
        CheckConstraint(
            "warehouse_id IS NOT NULL OR reserved = 0", name="reserve_only_ff"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    account: Mapped[str] = mapped_column(String(64))
    warehouse_id: Mapped[str | None] = mapped_column(ForeignKey("warehouses.id"))
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    reserved: Mapped[int] = mapped_column(Integer, default=0)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="document_positive_quantity"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[int] = mapped_column(Integer)
    source_id: Mapped[str | None] = mapped_column(ForeignKey("warehouses.id"))
    destination_id: Mapped[str | None] = mapped_column(ForeignKey("warehouses.id"))
    source_name: Mapped[str] = mapped_column(String(120), default="")
    destination_name: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, index=True
    )


class Event(Base):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id"))
    product_id: Mapped[str | None] = mapped_column(ForeignKey("products.id"))
    action: Mapped[str] = mapped_column(String(40))
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(120), default="")
    destination: Mapped[str] = mapped_column(String(120), default="")
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, index=True
    )


class Posting(Base):
    __tablename__ = "postings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    position_id: Mapped[str] = mapped_column(ForeignKey("positions.id"), index=True)
    quantity_delta: Mapped[int] = mapped_column(Integer)
    reserved_delta: Mapped[int] = mapped_column(Integer)


class Idempotency(Base):
    __tablename__ = "idempotency"
    __table_args__ = (UniqueConstraint("store_id", "key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    key: Mapped[str] = mapped_column(String(160))
    fingerprint: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict] = mapped_column(JSONB)
