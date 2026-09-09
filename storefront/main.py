"""ShopStream storefront — public web UI.

Calls catalog-api over the cluster network (plain Service DNS name)
and renders the product list as HTML.
"""
import os
import httpx
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates

app = FastAPI(title="storefront")
templates = Jinja2Templates(directory="templates")

# The Day 4 lesson in one line: services find each other by DNS name.
CATALOG_URL = os.environ.get("CATALOG_URL", "http://catalog-api:8080")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    return {"status": "ready"}


@app.get("/")
async def home(request: Request):
    error = None
    products = []
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{CATALOG_URL}/products")
            r.raise_for_status()
            products = r.json()["products"]
    except Exception as exc:  # show the failure instead of a stack trace
        error = f"catalog-api unreachable: {exc}"
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "products": products, "error": error,
         "pod": os.environ.get("HOSTNAME", "unknown")},
    )
