"""ShopStream orders-api — takes an order, saves it, queues the slow work.

Writes the order to PostgreSQL, then pushes the order id onto a Redis list.
The worker picks it up later. This is the async pattern: the customer does
not wait for the slow part.
"""
import os
import json

import psycopg
import redis
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

CONNINFO = (
    "host={h} port={p} dbname={d} user={u} password={pw}".format(
        h=os.environ["DB_HOST"], p=os.environ.get("DB_PORT", "5432"),
        d=os.environ.get("DB_NAME", "shopstream"),
        u=os.environ["DB_USER"], pw=os.environ["DB_PASSWORD"],
    )
)

# redis-0.redis.shopstream.svc.cluster.local - a StatefulSet pod's stable name
REDIS_HOST = os.environ.get("REDIS_HOST", "redis-0.redis")
QUEUE = "orders"

rdb = redis.Redis(host=REDIS_HOST, port=6379, socket_connect_timeout=2)


class Order(BaseModel):
    product_id: int
    quantity: int = 1


def init_db():
    with psycopg.connect(CONNINFO, connect_timeout=5) as conn, conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS orders ("
            "id SERIAL PRIMARY KEY, product_id INT NOT NULL, quantity INT NOT NULL,"
            "status TEXT NOT NULL DEFAULT 'new')"
        )
        conn.commit()


app = FastAPI(title="orders-api")


@app.on_event("startup")
def startup():
    init_db()


@app.get("/healthz")
def healthz():
    """Liveness: process alive only. No dependency checks here."""
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    """Readiness: both dependencies must work, or we should get no traffic."""
    try:
        with psycopg.connect(CONNINFO, connect_timeout=2) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
        rdb.ping()
        return {"status": "ready"}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.post("/orders")
def create_order(order: Order):
    with psycopg.connect(CONNINFO, connect_timeout=3) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO orders (product_id, quantity) VALUES (%s, %s) RETURNING id",
            (order.product_id, order.quantity),
        )
        order_id = cur.fetchone()[0]
        conn.commit()

    # Queue the slow work. RPUSH adds to the end of a Redis list.
    rdb.rpush(QUEUE, json.dumps({"order_id": order_id}))
    return {"order_id": order_id, "status": "new", "queued": True}


@app.get("/orders")
def list_orders():
    with psycopg.connect(CONNINFO, connect_timeout=3) as conn, conn.cursor() as cur:
        cur.execute("SELECT id, product_id, quantity, status FROM orders ORDER BY id DESC LIMIT 20")
        rows = cur.fetchall()
    return {"orders": [
        {"id": r[0], "product_id": r[1], "quantity": r[2], "status": r[3]} for r in rows
    ]}
