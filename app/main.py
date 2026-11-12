import logging
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import schemas
from app.db import Session
from app.inventory import DomainError, Inventory
from app.queries import state

BASE = Path(__file__).parent
app = FastAPI(
    title="CheckStock",
    version="1.0.0",
    description="Прозрачный складской учёт. Все количества в штуках.",
)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=160)]


def session():
    with Session.begin() as db:
        yield db


def reader():
    with Session.begin() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        yield db


@app.exception_handler(DomainError)
async def domain_error(request, error):
    return JSONResponse(
        status_code=error.status,
        content={
            "error": {"code": error.code, "message": error.message, **error.details}
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request, error):
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "Проверьте поля: количество должно быть целым числом от 1 до 1 000 000, названия не должны быть пустыми.",
                "fields": [".".join(map(str, e["loc"])) for e in error.errors()],
            }
        },
    )


@app.exception_handler(SQLAlchemyError)
async def database_error(request, error):
    logging.getLogger("checkstock").exception("Database error", exc_info=error)
    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "code": "database_error",
                "message": "База временно недоступна. Повторите действие: повторный запрос безопасен.",
            }
        },
    )


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/health", tags=["system"])
def health(db=Depends(reader)):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.get("/api/v1/state", tags=["read"])
def get_state(db=Depends(reader)):
    return state(db)


@app.get("/api/v1/stock", tags=["read"])
def get_stock(db=Depends(reader)):
    data = state(db)
    return {k: data[k] for k in ("stocks", "summary", "integrity")}


@app.get("/api/v1/warehouses", tags=["read"])
def get_warehouses(db=Depends(reader)):
    return state(db)["warehouses"]


@app.get("/api/v1/history", tags=["read"])
def get_history(db=Depends(reader)):
    return state(db)["history"]


@app.get("/api/v1/documents", tags=["read"])
def get_documents(db=Depends(reader)):
    return state(db)["documents"]


def run(db, key, operation_name, payload, method, **kwargs):
    inv = Inventory(db)
    return inv.execute(
        key, operation_name, payload, lambda: getattr(inv, method)(**kwargs)
    )


@app.post("/api/v1/warehouses", tags=["warehouses"])
def create_warehouse(body: schemas.WarehouseInput, key: Key, db=Depends(session)):
    payload = body.model_dump(mode="json")
    return run(db, key, "warehouse:create", payload, "create_warehouse", **payload)


@app.patch("/api/v1/warehouses/{warehouse_id}", tags=["warehouses"])
def rename_warehouse(
    warehouse_id: UUID, body: schemas.WarehouseInput, key: Key, db=Depends(session)
):
    payload = body.model_dump(mode="json") | {"warehouse_id": str(warehouse_id)}
    return run(db, key, "warehouse:rename", payload, "rename_warehouse", **payload)


@app.delete("/api/v1/warehouses/{warehouse_id}", tags=["warehouses"])
def archive_warehouse(warehouse_id: UUID, key: Key, db=Depends(session)):
    payload = {"warehouse_id": str(warehouse_id)}
    return run(db, key, "warehouse:archive", payload, "archive_warehouse", **payload)


@app.post("/api/v1/receipts", tags=["inventory"])
def receipt(body: schemas.ReceiptInput, key: Key, db=Depends(session)):
    payload = body.model_dump(mode="json")
    return run(db, key, "receipt", payload, "receipt", **payload)


@app.post("/api/v1/transfers", tags=["inventory"])
def transfer(body: schemas.TransferInput, key: Key, db=Depends(session)):
    payload = body.model_dump(mode="json")
    return run(db, key, "transfer", payload, "transfer", **payload)


@app.post("/api/v1/fbs-orders", tags=["inventory"])
def reserve(body: schemas.ShipmentInput, key: Key, db=Depends(session)):
    payload = body.model_dump(mode="json")
    return run(db, key, "reserve", payload, "reserve", **payload)


@app.post("/api/v1/fbo-shipments", tags=["inventory"])
def fbo(body: schemas.FBOInput, key: Key, db=Depends(session)):
    payload = body.model_dump(mode="json")
    return run(db, key, "fbo", payload, "fbo", **payload)


def change(db, key, document_id, kind, action):
    payload = {"document_id": str(document_id), "kind": kind, "action": action}
    return run(db, key, "transition", payload, "transition", **payload)


@app.post("/api/v1/transfers/{document_id}/receive", tags=["inventory"])
def receive_transfer(document_id: UUID, key: Key, db=Depends(session)):
    return change(db, key, document_id, "TRANSFER", "receive")


@app.post("/api/v1/fbo-shipments/{document_id}/receive", tags=["inventory"])
def receive_fbo(document_id: UUID, key: Key, db=Depends(session)):
    return change(db, key, document_id, "FBO", "receive")


@app.post("/api/v1/fbs-orders/{document_id}/ship", tags=["inventory"])
def ship_fbs(document_id: UUID, key: Key, db=Depends(session)):
    return change(db, key, document_id, "FBS", "ship")


@app.post("/api/v1/fbs-orders/{document_id}/cancel", tags=["inventory"])
def cancel_fbs(document_id: UUID, key: Key, db=Depends(session)):
    return change(db, key, document_id, "FBS", "cancel")
