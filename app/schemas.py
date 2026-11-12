from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Name = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)
]
Quantity = Annotated[int, Field(strict=True, gt=0, le=1_000_000)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WarehouseInput(Input):
    name: Name


class ReceiptInput(Input):
    product_id: UUID
    warehouse_id: UUID
    quantity: Quantity


class TransferInput(Input):
    product_id: UUID
    source_id: UUID
    destination_id: UUID
    quantity: Quantity


class ShipmentInput(Input):
    product_id: UUID
    warehouse_id: UUID
    quantity: Quantity


class FBOInput(ShipmentInput):
    destination: Name = "Склад маркетплейса"
