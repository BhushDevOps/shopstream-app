"""ShopStream worker — does the slow part of an order, in the background.

It waits on the Redis queue. When an order id arrives, it "processes" the
order (we just sleep to fake the work) and marks it done in PostgreSQL.
No HTTP server here: this is a background job, not a web service.
"""
import json
import os
import time

import psycopg
import redis

CONNINFO = (
    "host={h} port={p} dbname={d} user={u} password={pw}".format(
        h=os.environ["DB_HOST"], p=os.environ.get("DB_PORT", "5432"),
        d=os.environ.get("DB_NAME", "shopstream"),
        u=os.environ["DB_USER"], pw=os.environ["DB_PASSWORD"],
    )
)
REDIS_HOST = os.environ.get("REDIS_HOST", "redis-0.redis")
QUEUE = "orders"
WORK_SECONDS = float(os.environ.get("WORK_SECONDS", "2"))

rdb = redis.Redis(host=REDIS_HOST, port=6379, socket_connect_timeout=5)


def process(order_id: int):
    time.sleep(WORK_SECONDS)  # pretend this is payment + stock reservation
    with psycopg.connect(CONNINFO, connect_timeout=5) as conn, conn.cursor() as cur:
        cur.execute("UPDATE orders SET status = 'processed' WHERE id = %s", (order_id,))
        conn.commit()
    print(f"processed order {order_id}", flush=True)


def main():
    print(f"worker started, waiting on queue '{QUEUE}'", flush=True)
    while True:
        try:
            # BLPOP waits until an item arrives (up to 5s), then removes it.
            item = rdb.blpop(QUEUE, timeout=5)
            if item is None:
                continue  # nothing to do, loop again
            _, payload = item
            order_id = json.loads(payload)["order_id"]
            process(order_id)
        except Exception as exc:
            # Never crash the loop: log, wait, retry. Restarts lose queue position.
            print(f"error: {exc}", flush=True)
            time.sleep(3)


if __name__ == "__main__":
    main()
