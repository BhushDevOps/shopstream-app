"""ShopStream storefront — public web UI.

Login, product list, place an order, view your orders. Talks to two backends
over the cluster network (plain Service DNS names):
  catalog-api -> product list
  orders-api  -> create / list orders

Every request is logged with the pod name and the logged-in user, so
`kubectl logs` shows which pod served which click.

Auth here is a DEMO: users come from the USERS env var and the session is a
signed cookie. Real systems use an identity provider (OIDC/Cognito), not this.
"""
import logging
import os

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

POD = os.environ.get("HOSTNAME", "unknown")
CATALOG_URL = os.environ.get("CATALOG_URL", "http://catalog-api:8080")
ORDERS_URL = os.environ.get("ORDERS_URL", "http://orders-api:8080")
# "alice:alice123,bob:bob123" -> {"alice": "alice123", "bob": "bob123"}
USERS = dict(u.split(":", 1) for u in os.environ.get("USERS", "alice:alice123,bob:bob123").split(","))
# Signed-cookie key. Must be identical on every storefront replica, or a login
# on one pod is rejected by the other. Real deployments inject this from a Secret.
SECRET_KEY = os.environ.get("SESSION_SECRET", "dev-only-change-me")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("storefront")

app = FastAPI(title="storefront")
templates = Jinja2Templates(directory="templates")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    response = await call_next(request)
    if request.url.path not in ("/healthz", "/readyz"):
        user = request.session.get("user", "-")
        log.info("pod=%s user=%s %s %s -> %s", POD, user, request.method,
                 request.url.path, response.status_code)
    return response


# Added AFTER the logging middleware so it runs first and request.session exists.
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, max_age=3600)


def render(request, name, **ctx):
    ctx.update(pod=POD, user=request.session.get("user"))
    return templates.TemplateResponse(request=request, name=name, context=ctx)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    return {"status": "ready"}


@app.get("/login")
def login_form(request: Request):
    return render(request, "login.html", error=None)


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if USERS.get(username) == password:
        request.session["user"] = username
        log.info("pod=%s user=%s LOGIN ok", POD, username)
        return RedirectResponse("/", status_code=303)
    log.info("pod=%s user=%s LOGIN failed", POD, username)
    return render(request, "login.html", error="Wrong username or password")


@app.get("/logout")
def logout(request: Request):
    log.info("pod=%s user=%s LOGOUT", POD, request.session.get("user", "-"))
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


async def get_products():
    async with httpx.AsyncClient(timeout=3.0) as client:
        r = await client.get(f"{CATALOG_URL}/products")
        r.raise_for_status()
        return r.json()["products"]


@app.get("/")
async def home(request: Request):
    if not request.session.get("user"):
        return RedirectResponse("/login", status_code=303)
    error, products = None, []
    try:
        products = await get_products()
    except Exception as exc:  # show the failure instead of a stack trace
        error = f"catalog-api unreachable: {exc}"
    return render(request, "index.html", products=products, error=error,
                  message=request.query_params.get("message"))


@app.post("/order")
async def place_order(request: Request, product_id: int = Form(...), quantity: int = Form(1)):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=303)
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.post(f"{ORDERS_URL}/orders", json={
                "product_id": product_id, "quantity": quantity, "username": user})
            r.raise_for_status()
        log.info("pod=%s user=%s ORDER placed id=%s", POD, user, r.json()["order_id"])
        return RedirectResponse("/my-orders", status_code=303)
    except Exception as exc:
        log.info("pod=%s user=%s ORDER failed: %s", POD, user, exc)
        return RedirectResponse("/?message=Order failed: orders-api unreachable", status_code=303)


@app.get("/my-orders")
async def my_orders(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=303)
    error, orders = None, []
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{ORDERS_URL}/orders", params={"username": user})
            r.raise_for_status()
            orders = r.json()["orders"]
        names = {p["id"]: p["name"] for p in await get_products()}
        for o in orders:
            o["product"] = names.get(o["product_id"], f"product #{o['product_id']}")
    except Exception as exc:
        error = f"could not load orders: {exc}"
    return render(request, "orders.html", orders=orders, error=error)
