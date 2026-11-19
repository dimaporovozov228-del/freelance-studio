import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from .db import make_engine, migrate

PositiveID = Annotated[int, Field(strict=True, gt=0)]
PathID = Annotated[int, Field(gt=0)]
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0)]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Customer(InputModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class Product(InputModel):
    sku: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=120)

    @field_validator("sku")
    @classmethod
    def normalize_sku(cls, value):
        return value.upper()


class OrderItem(InputModel):
    product_id: PositiveID
    quantity: int = Field(strict=True, ge=1, le=1000000)
    price_minor: int = Field(strict=True, ge=0, le=1000000000)


class NewOrder(InputModel):
    customer_id: PositiveID
    currency: Literal["RUB", "USD", "EUR"]
    items: list[OrderItem] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_products(self):
        if len({item.product_id for item in self.items}) != len(self.items):
            raise ValueError("Each product may appear only once")
        return self


class StatusChange(InputModel):
    status: Literal["confirmed", "completed", "cancelled"]
    version: PositiveID


class CustomerOut(Customer):
    id: int


class ProductOut(Product):
    id: int


class OrderOut(NewOrder):
    id: int
    status: Literal["draft", "confirmed", "completed", "cancelled"]
    created_at: str
    version: int
    total_minor: int


class EventOut(BaseModel):
    id: int
    order_id: int
    action: str
    timestamp: str


def now():
    return datetime.now(timezone.utc).isoformat()


def get_order(connection, order_id):
    row = connection.execute(text("SELECT * FROM orders WHERE id=:id"), {"id": order_id}).mappings().first()
    if row is None:
        raise HTTPException(404, "Order not found")
    order = dict(row)
    order["items"] = [dict(item) for item in connection.execute(
        text("SELECT product_id, quantity, price_minor FROM order_items WHERE order_id=:id ORDER BY id"),
        {"id": order_id}).mappings()]
    order["total_minor"] = sum(item["quantity"] * item["price_minor"] for item in order["items"])
    return order


def create_app(db_path="data/orderdesk.sqlite3"):
    engine = make_engine(db_path)

    @asynccontextmanager
    async def lifespan(app):
        migrate(engine)
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="Order Desk API", version="0.1.0",
                  description="Local synthetic portfolio demo. Prices are integer minor currency units. No authentication; loopback only.",
                  lifespan=lifespan)
    app.state.engine = engine

    @app.get("/", include_in_schema=False)
    def index():
        return RedirectResponse("/docs")

    @app.get("/health")
    def health():
        with engine.begin() as connection:
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        return {"status": "ok", "schema_revision": revision}

    @app.post("/customers", status_code=201, response_model=CustomerOut)
    def create_customer(customer: Customer):
        with engine.begin() as connection:
            result = connection.execute(text("INSERT INTO customers(name,email) VALUES (:name,:email)"), customer.model_dump())
            return {"id": result.lastrowid, **customer.model_dump()}

    @app.get("/customers", response_model=list[CustomerOut])
    def customers(limit: PageLimit = 20, offset: PageOffset = 0):
        with engine.begin() as connection:
            return [dict(row) for row in connection.execute(text("SELECT * FROM customers ORDER BY id LIMIT :limit OFFSET :offset"),
                    {"limit": limit, "offset": offset}).mappings()]

    @app.post("/products", status_code=201, response_model=ProductOut)
    def create_product(product: Product):
        try:
            with engine.begin() as connection:
                result = connection.execute(text("INSERT INTO products(sku,name) VALUES (:sku,:name)"), product.model_dump())
                return {"id": result.lastrowid, **product.model_dump()}
        except IntegrityError as error:
            raise HTTPException(409, "SKU already exists") from error

    @app.get("/products", response_model=list[ProductOut])
    def products(limit: PageLimit = 20, offset: PageOffset = 0):
        with engine.begin() as connection:
            return [dict(row) for row in connection.execute(text("SELECT * FROM products ORDER BY id LIMIT :limit OFFSET :offset"),
                    {"limit": limit, "offset": offset}).mappings()]

    @app.post("/orders", status_code=201, response_model=OrderOut)
    def create_order(order: NewOrder, idempotency_key: Annotated[str, Header(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]):
        request_json = json.dumps(order.model_dump(), sort_keys=True, separators=(",", ":"))
        with engine.begin() as connection:
            previous = connection.execute(text("SELECT * FROM idempotency WHERE key=:key"), {"key": idempotency_key}).mappings().first()
            if previous:
                if previous["request_json"] != request_json:
                    raise HTTPException(409, "Idempotency-Key already used with a different request")
                return json.loads(previous["response_json"])
            if not connection.execute(text("SELECT id FROM customers WHERE id=:id"), {"id": order.customer_id}).first():
                raise HTTPException(404, "Customer not found")
            timestamp = now()
            result = connection.execute(text("INSERT INTO orders(customer_id,status,currency,created_at,version) VALUES (:customer,'draft',:currency,:created,1)"),
                                        {"customer": order.customer_id, "currency": order.currency, "created": timestamp})
            order_id = result.lastrowid
            for item in order.items:
                if not connection.execute(text("SELECT id FROM products WHERE id=:id"), {"id": item.product_id}).first():
                    raise HTTPException(404, "Product not found")
                connection.execute(text("INSERT INTO order_items(order_id,product_id,quantity,price_minor) VALUES (:order_id,:product_id,:quantity,:price_minor)"),
                                   {"order_id": order_id, **item.model_dump()})
            connection.execute(text("INSERT INTO audit_events(order_id,action,timestamp) VALUES (:id,'created',:time)"),
                               {"id": order_id, "time": timestamp})
            response = get_order(connection, order_id)
            connection.execute(text("INSERT INTO idempotency(key,request_json,response_json) VALUES (:key,:request,:response)"),
                               {"key": idempotency_key, "request": request_json, "response": json.dumps(response)})
            return response

    @app.get("/orders", response_model=list[OrderOut])
    def orders(limit: PageLimit = 20, offset: PageOffset = 0):
        with engine.begin() as connection:
            identifiers = connection.execute(text("SELECT id FROM orders ORDER BY id LIMIT :limit OFFSET :offset"),
                                             {"limit": limit, "offset": offset}).scalars().all()
            return [get_order(connection, identifier) for identifier in identifiers]

    @app.get("/orders/{order_id}", response_model=OrderOut)
    def order_detail(order_id: PathID):
        with engine.begin() as connection:
            return get_order(connection, order_id)

    @app.patch("/orders/{order_id}/status", response_model=OrderOut)
    def change_status(order_id: PathID, change: StatusChange):
        transitions = {"draft": {"confirmed", "cancelled"}, "confirmed": {"completed", "cancelled"},
                       "completed": set(), "cancelled": set()}
        with engine.begin() as connection:
            order = get_order(connection, order_id)
            if change.version != order["version"]:
                raise HTTPException(409, "Stale version; reload the order")
            if change.status not in transitions[order["status"]]:
                raise HTTPException(409, "Invalid status transition")
            connection.execute(text("UPDATE orders SET status=:status,version=version+1 WHERE id=:id AND version=:version"),
                               {"id": order_id, **change.model_dump()})
            connection.execute(text("INSERT INTO audit_events(order_id,action,timestamp) VALUES (:id,:action,:time)"),
                               {"id": order_id, "action": order["status"] + "->" + change.status, "time": now()})
            return get_order(connection, order_id)

    @app.get("/orders/{order_id}/events", response_model=list[EventOut])
    def events(order_id: PathID):
        with engine.begin() as connection:
            get_order(connection, order_id)
            return [dict(row) for row in connection.execute(text("SELECT * FROM audit_events WHERE order_id=:id ORDER BY id"),
                                                          {"id": order_id}).mappings()]

    return app
