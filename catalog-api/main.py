"""ShopStream catalog-api — Day 8 version (v2): products live in PostgreSQL.

Day 7's in-memory list is gone. Connection details arrive as environment
variables: host/port/name from a ConfigMap, username/password from a
Kubernetes Secret that External Secrets Operator syncs from AWS Secrets
Manager. This code never knows where the password came from.
"""
import os
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, HTTPException

DB = dict(
    host=os.environ["DB_HOST"],
    port=os.environ.get("DB_PORT", "5432"),
    dbname=os.environ.get("DB_NAME", "shopstream"),
    user=os.environ["DB_USER"],
    password=os.environ["DB_PASSWORD"],
)
CONNINFO = "host={host} port={port} dbname={dbname} user={user} password={password}".format(**DB)

SEED = [
    ("Mechanical keyboard", 89.00),
    ("USB-C dock", 129.50),
    ("27-inch monitor", 249.99),
    ("Webcam", 59.00),
]


def init_db():
    """Create the table and seed it if empty - our data is re-derivable."""
    with psycopg.connect(CONNINFO, connect_timeout=5) as conn, conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS products ("
            "id SERIAL PRIMARY KEY, name TEXT NOT NULL, price NUMERIC(10,2) NOT NULL)"
        )
        cur.execute("SELECT count(*) FROM products")
        if cur.fetchone()[0] == 0:
            cur.executemany("INSERT INTO products (name, price) VALUES (%s, %s)", SEED)
        conn.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="catalog-api", lifespan=lifespan)


@app.get("/healthz")
def healthz():
    """Liveness: process alive? NO database check here - Day 7's rule."""
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    """Readiness: can I serve? DB check belongs HERE. DB down -> this pod
    politely leaves the Service endpoints until the DB is back."""
    try:
        with psycopg.connect(CONNINFO, connect_timeout=2) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
        return {"status": "ready"}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"db not reachable: {exc}")


@app.get("/products")
def list_products():
    with psycopg.connect(CONNINFO, connect_timeout=3) as conn, conn.cursor() as cur:
        cur.execute("SELECT id, name, price FROM products ORDER BY id")
        rows = cur.fetchall()
    return {
        "products": [{"id": r[0], "name": r[1], "price": float(r[2])} for r in rows],
        "served_by": os.environ.get("HOSTNAME", "unknown"),
    }
