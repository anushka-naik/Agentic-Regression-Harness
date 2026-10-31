"""Fictional retailer world state (Northstar Demo Store)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

OrderStatus = Literal["delivered", "shipped", "cancelled", "refunded"]
SUPPORTED_REASONS: tuple[str, ...] = ("damaged", "wrong_item", "changed_mind")


class Order(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    order_id: str = Field(pattern=r"^O-\d{4}$")
    customer_id: str = Field(pattern=r"^C-\d{3}$")
    status: OrderStatus
    delivered_at: str | None
    paid_cents: int = Field(ge=0)
    currency: Literal["USD"] = "USD"
    final_sale: bool
    product_name: str
    public_note: str = ""
    private_marker: str = Field(pattern=r"^PRIVATE_[A-Z0-9_]+$")

    @field_validator("delivered_at")
    @classmethod
    def _timezone_aware(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError("delivered_at must include a UTC offset")
        return value


class World(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    world_id: str
    description: str = ""
    orders: list[Order]

    @field_validator("orders")
    @classmethod
    def _unique_ids(cls, orders: list[Order]) -> list[Order]:
        ids = [o.order_id for o in orders]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate order_id in world")
        markers = [o.private_marker for o in orders]
        if len(markers) != len(set(markers)):
            raise ValueError("duplicate private_marker in world")
        return orders
