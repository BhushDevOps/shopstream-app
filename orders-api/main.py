"""ShopStream orders-api — takes an order, saves it, queues the slow work.

Writes the order to PostgreSQL, then pushes the order id onto a Redis list.
The worker picks it up later. This is the async pattern: the customer does
not wait for the slow part.
"""
import json
import logging
import os

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
POD = os.environ.get("HOSTNAME", "unknown")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("orders-api")

rdb = redis.Redis(host=REDIS_HOST, port=6379, socket_connect_timeout=2)


class Order(BaseModel):
    product_id: int
    quantity: int = 1
    username: str = "anonymous"


def init_db():
    with psycopg.connect(CONNINFO, connect_timeout=5) as conn, conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS orders ("
            "id SERIAL PRIMARY KEY, product_id INT NOT NULL, quantity INT NOT NULL,"
            "status TEXT NOT NULL DEFAULT 'new')"
        )
        # added later: who placed the order (an existing table gets the column too)
        cur.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS username TEXT NOT NULL DEFAULT 'anonymous'")
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
            "INSERT INTO orders (product_id, quantity, username) VALUES (%s, %s, %s) RETURNING id",
            (order.product_id, order.quantity, order.username),
        )
        order_id = cur.fetchone()[0]
        conn.commit()

    # Queue the slow work. RPUSH adds to the end of a Redis list.
    rdb.rpush(QUEUE, json.dumps({"order_id": order_id}))
    log.info("pod=%s created order id=%s user=%s product=%s qty=%s",
             POD, order_id, order.username, order.product_id, order.quantity)
    return {"order_id": order_id, "status": "new", "queued": True}


@app.get("/orders")
def list_orders(username: str | None = None):
    """Recent orders, or only one user's when ?username= is given."""
    query = "SELECT id, product_id, quantity, status, username FROM orders"
    args = ()
    if username:
        query += " WHERE username = %s"
        args = (username,)
    query += " ORDER BY id DESC LIMIT 20"
    with psycopg.connect(CONNINFO, connect_timeout=3) as conn, conn.cursor() as cur:
        cur.execute(query, args)
        rows = cur.fetchall()
    log.info("pod=%s listed %d orders user=%s", POD, len(rows), username or "*")
    return {"orders": [
        {"id": r[0], "product_id": r[1], "quantity": r[2], "status": r[3], "username": r[4]}
        for r in rows
    ]}
