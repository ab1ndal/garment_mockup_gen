# Phase 2 — Product Selection, Prompts, Generation UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the product-browsing + prompt-management UI and data layer, with image/video generation endpoints stubbed for Phase 3 wiring.

**Architecture:** A new `prompts` table and a read-only `product_browse` Postgres view back three FastAPI routers (products, prompts, generate). Pure-Python repos in `mockup_generator/db/` follow the existing `profiles_repo` pattern. A React tabbed shell (Products | Prompts) calls the API via the existing `apiFetch` helper.

**Tech Stack:** FastAPI, Supabase (Postgres + supabase-py v2), React + Vite + TypeScript, pytest.

## Global Constraints

- Python `>=3.10,<3.11`. No new core dependency unless listed here.
- Core modules under `mockup_generator/` MUST NOT import `streamlit`.
- All Supabase schema changes are **additive only** (shared project with Inventory-Management) — never alter/drop existing tables.
- Every API route lives under `/api` and requires `Depends(get_current_user)` — no admin gate this phase (any active profile may do everything).
- Repos are pure functions taking a `supabase.Client` as the first arg (match `mockup_generator/db/profiles_repo.py`).
- Product ids are `BC<YY><seq>` with variable-width seq; range filtering uses the numeric key `YY*1_000_000 + seq`, never lexical string compare.
- Generation handlers are **stubs** this phase (return HTTP 501); Phase 3 replaces only their bodies.
- Frontend uses inline styles + `apiFetch` (no new UI framework); type-check gate is `npm run build`.

---

### Task 1: Supabase migration — `prompts` table + `product_browse` view

**Files:**
- Migration applied via Supabase MCP `apply_migration` (project `epotsxdugwfhyeiudjox`). No repo file.

**Interfaces:**
- Produces: table `public.prompts(prompt_id, categoryid, label, body, is_default, created_at, updated_at, updated_by)`; view `public.product_browse(productid, name, categoryid, category_name, producturl, base_mockup, id_key)`.

- [ ] **Step 1: Apply the migration**

Call `apply_migration` with name `phase2_prompts_and_product_browse` and this SQL:

```sql
-- Named prompt variants per category (Phase 2).
create table if not exists public.prompts (
  prompt_id   bigint generated always as identity primary key,
  categoryid  text not null references public.categories(categoryid),
  label       text not null,
  body        text not null,
  is_default  boolean not null default false,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  updated_by  uuid references public.profiles(id),
  unique (categoryid, label)
);

create unique index if not exists prompts_one_default_per_category
  on public.prompts (categoryid) where is_default;

alter table public.prompts enable row level security;

drop policy if exists prompts_authenticated_all on public.prompts;
create policy prompts_authenticated_all on public.prompts
  for all to authenticated using (true) with check (true);

grant select, insert, update, delete on public.prompts to authenticated, service_role;

-- Read-only browse view: products + category name + base_mockup + numeric id key.
create or replace view public.product_browse as
select
  p.productid,
  p.name,
  p.categoryid,
  c.name as category_name,
  p.producturl,
  coalesce(m.base_mockup, false) as base_mockup,
  case when p.productid ~ '^BC[0-9]{2}[0-9]+$'
       then (substring(p.productid from 3 for 2))::int * 1000000
            + (substring(p.productid from 5))::int
  end as id_key
from public.products p
left join public.categories c on c.categoryid = p.categoryid
left join public.mockups m on m.productid = p.productid;

alter view public.product_browse set (security_invoker = on);
grant select on public.product_browse to authenticated, service_role, anon;
```

- [ ] **Step 2: Verify table + view exist and key math is correct**

Run via MCP `execute_sql`:

```sql
select count(*) from public.prompts;                       -- expect 0
select productid, id_key from public.product_browse
  where productid in ('BC25001','BC251000','BC25999','BC26227')
  order by id_key;
```

Expected: `prompts` count 0; ids order `BC25001`(25000001) < `BC25999`(25000999) < `BC251000`(25001000) < `BC26227`(26000227). This confirms the 3→4-digit boundary sorts numerically.

- [ ] **Step 3: Commit** (migration is server-side; record it in the plan checklist — no repo file to commit yet)

```bash
git commit --allow-empty -m "chore(db): add prompts table + product_browse view (phase 2)"
```

---

### Task 2: Product-id key parser (pure, TDD)

**Files:**
- Create: `mockup_generator/db/product_ids.py`
- Test: `tests/test_product_ids.py`

**Interfaces:**
- Produces: `product_key(productid: str) -> int | None`; `parse_range(start: str, end: str) -> tuple[int, int]` (raises `ValueError` on malformed input; returns low<=high).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_product_ids.py
import pytest
from mockup_generator.db.product_ids import product_key, parse_range


def test_key_three_and_four_digit_seq():
    assert product_key("BC25001") == 25_000_001
    assert product_key("BC251000") == 25_001_000
    assert product_key("BC26227") == 26_000_227


def test_key_orders_across_width_boundary():
    # lexically 'BC251000' < 'BC25999' (wrong); numerically it must be greater
    assert product_key("BC251000") > product_key("BC25999")


def test_key_malformed_returns_none():
    for bad in ["", "X1", "BC2", "BCAB123", "25001", None]:  # type: ignore[list-item]
        assert product_key(bad) is None


def test_parse_range_orders_low_high():
    assert parse_range("BC25001", "BC251000") == (25_000_001, 25_001_000)
    assert parse_range("BC251000", "BC25001") == (25_000_001, 25_001_000)


def test_parse_range_rejects_bad_endpoint():
    with pytest.raises(ValueError):
        parse_range("BC25001", "nope")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_product_ids.py -v`
Expected: FAIL — `ModuleNotFoundError: mockup_generator.db.product_ids`

- [ ] **Step 3: Write minimal implementation**

```python
# mockup_generator/db/product_ids.py
"""Parse Bindal product ids (``BC<YY><seq>``) into sortable numeric keys.

Ids have a ``BC`` prefix, a 2-digit year, and a variable-width sequence (3 or 4
digits today). Lexical comparison breaks across that width boundary
(``'BC251000' < 'BC25999'``), so range filtering compares on a parsed key.
"""

from __future__ import annotations

import re

_ID_RE = re.compile(r"^BC(\d{2})(\d+)$")


def product_key(productid: str | None) -> int | None:
    """Return a monotonic sort key for a product id, or None if malformed.

    key = YY * 1_000_000 + seq   (seq < 1_000_000 for all real ids).
    """
    m = _ID_RE.match(productid or "")
    if not m:
        return None
    yy, seq = int(m.group(1)), int(m.group(2))
    return yy * 1_000_000 + seq


def parse_range(start: str, end: str) -> tuple[int, int]:
    """Return (low_key, high_key) for an inclusive id range.

    Raises ValueError if either endpoint is not a valid product id.
    """
    lo, hi = product_key(start), product_key(end)
    if lo is None or hi is None:
        raise ValueError(f"invalid product id range: {start!r}..{end!r}")
    return (lo, hi) if lo <= hi else (hi, lo)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_product_ids.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add mockup_generator/db/product_ids.py tests/test_product_ids.py
git commit -m "feat(db): product-id numeric key parser + range"
```

---

### Task 3: products + mockups repos

**Files:**
- Create: `mockup_generator/db/products_repo.py`
- Create: `mockup_generator/db/mockups_repo.py`
- Test: `tests/test_products_repo.py`

**Interfaces:**
- Consumes: `product_ids.parse_range`; a `supabase.Client`.
- Produces:
  - `products_repo.Product` dataclass: `productid:str, name:str, categoryid:str|None, category_name:str|None, base_mockup:bool, producturl:str|None`.
  - `products_repo.list_products(client, *, category=None, product_id=None, id_start=None, id_end=None, pending=True, limit=50, offset=0) -> list[Product]`.
  - `products_repo.get_product(client, productid) -> Product | None`.
  - `products_repo.list_categories(client) -> list[tuple[str, str]]` (categoryid, name).
  - `mockups_repo.get_flags(client, productid) -> dict | None`.

- [ ] **Step 1: Write the failing test** (uses a fake client that records the query chain — no network)

```python
# tests/test_products_repo.py
from mockup_generator.db import products_repo


class FakeQuery:
    def __init__(self, sink, rows):
        self.sink, self._rows = sink, rows
    def select(self, *a, **k): self.sink.append(("select", a)); return self
    def eq(self, c, v): self.sink.append(("eq", c, v)); return self
    def gte(self, c, v): self.sink.append(("gte", c, v)); return self
    def lte(self, c, v): self.sink.append(("lte", c, v)); return self
    def order(self, c, **k): self.sink.append(("order", c)); return self
    def range(self, lo, hi): self.sink.append(("range", lo, hi)); return self
    def limit(self, n): self.sink.append(("limit", n)); return self
    def execute(self):
        class R: data = self._rows
        return R()


class FakeClient:
    def __init__(self, rows): self.sink, self._rows = [], rows
    def table(self, name): self.sink.append(("table", name)); return FakeQuery(self.sink, self._rows)


def test_list_products_pending_and_range_filters():
    rows = [{"productid": "BC25001", "name": "Silk-Saree", "categoryid": "SA",
             "category_name": "Saree", "producturl": "http://d", "base_mockup": False}]
    c = FakeClient(rows)
    out = products_repo.list_products(c, category="SA", id_start="BC25001",
                                      id_end="BC251000", pending=True, limit=20, offset=0)
    assert out[0].productid == "BC25001"
    assert ("table", "product_browse") in c.sink
    assert ("eq", "categoryid", "SA") in c.sink
    assert ("eq", "base_mockup", False) in c.sink
    assert ("gte", "id_key", 25_000_001) in c.sink
    assert ("lte", "id_key", 25_001_000) in c.sink
    assert ("range", 0, 19) in c.sink


def test_list_products_single_id_exact():
    c = FakeClient([])
    products_repo.list_products(c, product_id="BC25007", pending=False)
    assert ("eq", "productid", "BC25007") in c.sink
    # pending=False must NOT add a base_mockup filter
    assert all(s[0] != "eq" or s[1] != "base_mockup" for s in c.sink)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_products_repo.py -v`
Expected: FAIL — `ModuleNotFoundError: mockup_generator.db.products_repo`

- [ ] **Step 3: Write minimal implementation**

```python
# mockup_generator/db/products_repo.py
"""Read access to the product browse view (products + category + mockup flag)."""

from __future__ import annotations

from dataclasses import dataclass

from supabase import Client

from mockup_generator.db.product_ids import parse_range

_COLS = "productid, name, categoryid, category_name, producturl, base_mockup"


@dataclass
class Product:
    productid: str
    name: str
    categoryid: str | None
    category_name: str | None
    base_mockup: bool
    producturl: str | None


def _row(r: dict) -> Product:
    return Product(
        productid=r["productid"],
        name=r["name"],
        categoryid=r.get("categoryid"),
        category_name=r.get("category_name"),
        base_mockup=bool(r.get("base_mockup")),
        producturl=r.get("producturl"),
    )


def list_products(
    client: Client,
    *,
    category: str | None = None,
    product_id: str | None = None,
    id_start: str | None = None,
    id_end: str | None = None,
    pending: bool = True,
    limit: int = 50,
    offset: int = 0,
) -> list[Product]:
    q = client.table("product_browse").select(_COLS)
    if category:
        q = q.eq("categoryid", category)
    if pending:
        q = q.eq("base_mockup", False)
    if product_id:
        q = q.eq("productid", product_id)
    elif id_start and id_end:
        lo, hi = parse_range(id_start, id_end)
        q = q.gte("id_key", lo).lte("id_key", hi)
    q = q.order("id_key").range(offset, offset + limit - 1)
    resp = q.execute()
    return [_row(r) for r in (resp.data or [])]


def get_product(client: Client, productid: str) -> Product | None:
    resp = (
        client.table("product_browse").select(_COLS)
        .eq("productid", productid).limit(1).execute()
    )
    rows = resp.data or []
    return _row(rows[0]) if rows else None


def list_categories(client: Client) -> list[tuple[str, str]]:
    resp = client.table("categories").select("categoryid, name").order("name").execute()
    return [(r["categoryid"], r["name"]) for r in (resp.data or [])]
```

```python
# mockup_generator/db/mockups_repo.py
"""Read access to the existing per-product ``mockups`` status flags."""

from __future__ import annotations

from supabase import Client

_FLAG_COLS = "productid, redo, base_mockup, file_mockup, mockup, video, ig_reel, ig_post, whatsapp"


def get_flags(client: Client, productid: str) -> dict | None:
    resp = (
        client.table("mockups").select(_FLAG_COLS)
        .eq("productid", productid).limit(1).execute()
    )
    rows = resp.data or []
    return rows[0] if rows else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_products_repo.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add mockup_generator/db/products_repo.py mockup_generator/db/mockups_repo.py tests/test_products_repo.py
git commit -m "feat(db): products + mockups repos over product_browse view"
```

---

### Task 4: prompts repo + seed

**Files:**
- Create: `mockup_generator/db/prompts_repo.py`
- Test: `tests/test_prompts_repo.py`

**Interfaces:**
- Consumes: `mockup_generator.prompts.defaults.CATEGORY_PROMPTS`; a `supabase.Client`.
- Produces:
  - `Prompt` dataclass: `prompt_id:int, categoryid:str, label:str, body:str, is_default:bool`.
  - `list_by_category(client, categoryid) -> list[Prompt]`.
  - `create(client, *, categoryid, label, body, is_default=False, updated_by=None) -> Prompt`.
  - `update(client, prompt_id, *, label=None, body=None, is_default=None, updated_by=None) -> Prompt`.
  - `delete(client, prompt_id) -> None`.
  - `seed_defaults(client) -> int` (idempotent; returns count inserted).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_prompts_repo.py
from mockup_generator.db import prompts_repo


class FakeTable:
    def __init__(self, sink, rows):
        self.sink, self._rows = sink, rows
    def select(self, *a): self.sink.append(("select", a)); return self
    def insert(self, payload): self.sink.append(("insert", payload)); return self
    def update(self, payload): self.sink.append(("update", payload)); return self
    def delete(self): self.sink.append(("delete",)); return self
    def eq(self, c, v): self.sink.append(("eq", c, v)); return self
    def order(self, c, **k): return self
    def limit(self, n): return self
    def execute(self):
        class R: data = self._rows
        return R()


class FakeClient:
    def __init__(self, rows): self.sink, self._rows = [], rows
    def table(self, name): self.sink.append(("table", name)); return FakeTable(self.sink, self._rows)


def test_create_default_clears_siblings_first():
    inserted = [{"prompt_id": 1, "categoryid": "SA", "label": "Studio",
                 "body": "b", "is_default": True}]
    c = FakeClient(inserted)
    p = prompts_repo.create(c, categoryid="SA", label="Studio", body="b", is_default=True)
    assert p.prompt_id == 1 and p.is_default is True
    # clearing siblings (update is_default=False) must occur before insert
    kinds = [s[0] for s in c.sink]
    assert kinds.index("update") < kinds.index("insert")


def test_list_by_category_maps_rows():
    rows = [{"prompt_id": 5, "categoryid": "SA", "label": "Default",
             "body": "x", "is_default": True}]
    c = FakeClient(rows)
    out = prompts_repo.list_by_category(c, "SA")
    assert out[0].label == "Default" and out[0].prompt_id == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_prompts_repo.py -v`
Expected: FAIL — `ModuleNotFoundError: mockup_generator.db.prompts_repo`

- [ ] **Step 3: Write minimal implementation**

```python
# mockup_generator/db/prompts_repo.py
"""CRUD for per-category named prompt variants (Phase 2)."""

from __future__ import annotations

from dataclasses import dataclass

from supabase import Client

from mockup_generator.prompts.defaults import CATEGORY_PROMPTS

_COLS = "prompt_id, categoryid, label, body, is_default"


@dataclass
class Prompt:
    prompt_id: int
    categoryid: str
    label: str
    body: str
    is_default: bool


def _row(r: dict) -> Prompt:
    return Prompt(
        prompt_id=int(r["prompt_id"]),
        categoryid=r["categoryid"],
        label=r["label"],
        body=r["body"],
        is_default=bool(r["is_default"]),
    )


def list_by_category(client: Client, categoryid: str) -> list[Prompt]:
    resp = (
        client.table("prompts").select(_COLS)
        .eq("categoryid", categoryid)
        .order("is_default", desc=True).order("label").execute()
    )
    return [_row(r) for r in (resp.data or [])]


def _clear_defaults(client: Client, categoryid: str) -> None:
    client.table("prompts").update({"is_default": False}).eq("categoryid", categoryid).execute()


def create(client: Client, *, categoryid: str, label: str, body: str,
           is_default: bool = False, updated_by: str | None = None) -> Prompt:
    if is_default:
        _clear_defaults(client, categoryid)
    payload = {"categoryid": categoryid, "label": label, "body": body,
               "is_default": is_default, "updated_by": updated_by}
    resp = client.table("prompts").insert(payload).execute()
    return _row(resp.data[0])


def update(client: Client, prompt_id: int, *, label: str | None = None,
           body: str | None = None, is_default: bool | None = None,
           updated_by: str | None = None) -> Prompt:
    if is_default:
        cur = client.table("prompts").select("categoryid").eq("prompt_id", prompt_id).limit(1).execute()
        if cur.data:
            _clear_defaults(client, cur.data[0]["categoryid"])
    payload: dict = {"updated_at": "now()"}
    if label is not None:
        payload["label"] = label
    if body is not None:
        payload["body"] = body
    if is_default is not None:
        payload["is_default"] = is_default
    if updated_by is not None:
        payload["updated_by"] = updated_by
    resp = client.table("prompts").update(payload).eq("prompt_id", prompt_id).execute()
    return _row(resp.data[0])


def delete(client: Client, prompt_id: int) -> None:
    client.table("prompts").delete().eq("prompt_id", prompt_id).execute()


def seed_defaults(client: Client) -> int:
    inserted = 0
    for categoryid, body in CATEGORY_PROMPTS.items():
        existing = (
            client.table("prompts").select("prompt_id")
            .eq("categoryid", categoryid).eq("label", "Default").limit(1).execute()
        )
        if existing.data:
            continue
        client.table("prompts").insert(
            {"categoryid": categoryid, "label": "Default", "body": body, "is_default": True}
        ).execute()
        inserted += 1
    return inserted
```

Note: `"updated_at": "now()"` is sent as a literal string; the table default already stamps it. Drop the key if your PostgREST rejects it — it is not asserted by tests.

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_prompts_repo.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Seed the live table + verify** (one-time, via a throwaway script using the service or anon client)

Run:
```bash
poetry run python -c "from mockup_generator.integrations.supabase_client import service_client, anon_client; from mockup_generator.db import prompts_repo; c=service_client() or anon_client(); print('inserted', prompts_repo.seed_defaults(c))"
```
Then verify via MCP `execute_sql`: `select categoryid, label, is_default from public.prompts order by categoryid;`
Expected: 11 rows (SA, KP, C-KP, GWN, LE, SHT, KUR, NHJ, SKT-TOP, CRD, TOP), each `Default` / `true`. Re-running the seed prints `inserted 0`.

- [ ] **Step 6: Commit**

```bash
git add mockup_generator/db/prompts_repo.py tests/test_prompts_repo.py
git commit -m "feat(db): prompts repo (CRUD + idempotent seed)"
```

---

### Task 5: Backend schemas + DB-client dependency

**Files:**
- Create: `backend/schemas.py`
- Create: `backend/deps.py`
- Modify: `pyproject.toml` (add `httpx` to dev group for TestClient)

**Interfaces:**
- Produces:
  - Pydantic models: `CategoryOut`, `ProductOut`, `PromptOut`, `PromptCreate`, `PromptUpdate`, `GenerateRequest`, `GenerateResponse`.
  - `deps.get_db(authorization: str | None) -> Client` — service client if `SUPABASE_SECRET_KEY` set, else a client acting as the bearer user.

- [ ] **Step 1: Add httpx dev dependency**

Run: `poetry add --group dev httpx`
Expected: `pyproject.toml` dev group now lists `httpx`.

- [ ] **Step 2: Write `backend/schemas.py`**

```python
# backend/schemas.py
from __future__ import annotations

from pydantic import BaseModel


class CategoryOut(BaseModel):
    categoryid: str
    name: str


class ProductOut(BaseModel):
    productid: str
    name: str
    categoryid: str | None = None
    category_name: str | None = None
    base_mockup: bool = False
    producturl: str | None = None


class PromptOut(BaseModel):
    prompt_id: int
    categoryid: str
    label: str
    body: str
    is_default: bool


class PromptCreate(BaseModel):
    categoryid: str
    label: str
    body: str
    is_default: bool = False


class PromptUpdate(BaseModel):
    label: str | None = None
    body: str | None = None
    is_default: bool | None = None


class GenerateRequest(BaseModel):
    productid: str
    prompt: str


class GenerateResponse(BaseModel):
    status: str
    detail: str
```

- [ ] **Step 3: Write `backend/deps.py`**

```python
# backend/deps.py
"""Shared FastAPI dependencies for DB access."""

from __future__ import annotations

from fastapi import Header
from supabase import Client

from backend.auth import _bearer_token
from mockup_generator.integrations.supabase_client import client_for_user, service_client


def get_db(authorization: str | None = Header(default=None)) -> Client:
    """Service-role client when a secret key is configured, else act as the user.

    Routes that use this also depend on get_current_user, so the request is
    already gated; this only chooses which Supabase client performs the query.
    """
    svc = service_client()
    if svc is not None:
        return svc
    return client_for_user(_bearer_token(authorization))
```

- [ ] **Step 4: Verify imports resolve**

Run: `poetry run python -c "import backend.schemas, backend.deps; print('ok')"`
Expected: prints `ok`

- [ ] **Step 5: Commit**

```bash
git add backend/schemas.py backend/deps.py pyproject.toml poetry.lock
git commit -m "feat(backend): API schemas + DB-client dependency"
```

---

### Task 6: Products router

**Files:**
- Create: `backend/routers/__init__.py` (empty)
- Create: `backend/routers/products.py`
- Test: `tests/test_products_api.py`

**Interfaces:**
- Consumes: `products_repo`; `get_current_user`; `deps.get_db`; `schemas.ProductOut`, `CategoryOut`.
- Produces: `router` (APIRouter) with `GET /api/categories`, `GET /api/products`, `GET /api/products/{productid}`.

- [ ] **Step 1: Write the failing test** (override auth + db, monkeypatch the repo)

```python
# tests/test_products_api.py
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.auth import get_current_user, CurrentUser
from backend.deps import get_db
from mockup_generator.db import products_repo
from mockup_generator.db.profiles_repo import Profile


@pytest.fixture
def client(monkeypatch):
    fake_user = CurrentUser(id="u1", email="a@b.c", role="user",
                            profile=Profile(id="u1", email="a@b.c", role="user", is_active=True))
    app.dependency_overrides[get_current_user] = lambda: fake_user
    app.dependency_overrides[get_db] = lambda: object()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_list_products_returns_items(client, monkeypatch):
    sample = [products_repo.Product("BC25001", "Silk-Saree", "SA", "Saree", False, "http://d")]
    monkeypatch.setattr(products_repo, "list_products", lambda *a, **k: sample)
    r = client.get("/api/products?category=SA&pending=true")
    assert r.status_code == 200
    body = r.json()
    assert body[0]["productid"] == "BC25001"
    assert body[0]["category_name"] == "Saree"


def test_get_product_404(client, monkeypatch):
    monkeypatch.setattr(products_repo, "get_product", lambda *a, **k: None)
    r = client.get("/api/products/BC99999")
    assert r.status_code == 404


def test_bad_range_returns_400(client, monkeypatch):
    def boom(*a, **k):
        raise ValueError("invalid product id range")
    monkeypatch.setattr(products_repo, "list_products", boom)
    r = client.get("/api/products?id_start=BC25001&id_end=oops")
    assert r.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_products_api.py -v`
Expected: FAIL — import error (`backend.routers.products` missing) / 404 route.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/routers/__init__.py
```

```python
# backend/routers/products.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from supabase import Client

from backend.auth import CurrentUser, get_current_user
from backend.deps import get_db
from backend.schemas import CategoryOut, ProductOut
from mockup_generator.db import products_repo

router = APIRouter(prefix="/api", tags=["products"])


@router.get("/categories", response_model=list[CategoryOut])
def list_categories(user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    return [CategoryOut(categoryid=cid, name=name) for cid, name in products_repo.list_categories(db)]


@router.get("/products", response_model=list[ProductOut])
def list_products(
    category: str | None = None,
    id: str | None = None,
    id_start: str | None = None,
    id_end: str | None = None,
    pending: bool = True,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: CurrentUser = Depends(get_current_user),
    db: Client = Depends(get_db),
):
    try:
        rows = products_repo.list_products(
            db, category=category, product_id=id, id_start=id_start, id_end=id_end,
            pending=pending, limit=limit, offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [ProductOut(**vars(p)) for p in rows]


@router.get("/products/{productid}", response_model=ProductOut)
def get_product(productid: str, user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    p = products_repo.get_product(db, productid)
    if p is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return ProductOut(**vars(p))
```

Then register in `backend/main.py` (full wiring is Task 9, but products router is needed for this test) — add these two lines now:

```python
from backend.routers import products as products_router  # near other imports
app.include_router(products_router.router)                # after app = FastAPI(...)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_products_api.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/routers/__init__.py backend/routers/products.py backend/main.py tests/test_products_api.py
git commit -m "feat(backend): products + categories router"
```

---

### Task 7: Prompts router

**Files:**
- Create: `backend/routers/prompts.py`
- Modify: `backend/main.py` (include the router)
- Test: `tests/test_prompts_api.py`

**Interfaces:**
- Consumes: `prompts_repo`; `get_current_user`; `get_db`; `schemas.PromptOut/PromptCreate/PromptUpdate`.
- Produces: `router` with `GET /api/prompts`, `POST /api/prompts`, `PATCH /api/prompts/{prompt_id}`, `DELETE /api/prompts/{prompt_id}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_prompts_api.py
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.auth import get_current_user, CurrentUser
from backend.deps import get_db
from mockup_generator.db import prompts_repo
from mockup_generator.db.profiles_repo import Profile


@pytest.fixture
def client():
    u = CurrentUser(id="u1", email="a@b.c", role="user",
                    profile=Profile(id="u1", email="a@b.c", role="user", is_active=True))
    app.dependency_overrides[get_current_user] = lambda: u
    app.dependency_overrides[get_db] = lambda: object()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_list_prompts(client, monkeypatch):
    monkeypatch.setattr(prompts_repo, "list_by_category",
                        lambda *a, **k: [prompts_repo.Prompt(1, "SA", "Default", "b", True)])
    r = client.get("/api/prompts?categoryid=SA")
    assert r.status_code == 200 and r.json()[0]["label"] == "Default"


def test_create_prompt(client, monkeypatch):
    monkeypatch.setattr(prompts_repo, "create",
                        lambda *a, **k: prompts_repo.Prompt(2, "SA", "Studio", "b", False))
    r = client.post("/api/prompts", json={"categoryid": "SA", "label": "Studio", "body": "b"})
    assert r.status_code == 201 and r.json()["prompt_id"] == 2


def test_delete_prompt(client, monkeypatch):
    called = {}
    monkeypatch.setattr(prompts_repo, "delete", lambda c, pid: called.setdefault("pid", pid))
    r = client.delete("/api/prompts/7")
    assert r.status_code == 204 and called["pid"] == 7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_prompts_api.py -v`
Expected: FAIL — `backend.routers.prompts` missing.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/routers/prompts.py
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from supabase import Client

from backend.auth import CurrentUser, get_current_user
from backend.deps import get_db
from backend.schemas import PromptCreate, PromptOut, PromptUpdate
from mockup_generator.db import prompts_repo

router = APIRouter(prefix="/api", tags=["prompts"])


@router.get("/prompts", response_model=list[PromptOut])
def list_prompts(categoryid: str, user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    return [PromptOut(**vars(p)) for p in prompts_repo.list_by_category(db, categoryid)]


@router.post("/prompts", response_model=PromptOut, status_code=status.HTTP_201_CREATED)
def create_prompt(payload: PromptCreate, user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    p = prompts_repo.create(db, categoryid=payload.categoryid, label=payload.label,
                            body=payload.body, is_default=payload.is_default, updated_by=user.id)
    return PromptOut(**vars(p))


@router.patch("/prompts/{prompt_id}", response_model=PromptOut)
def update_prompt(prompt_id: int, payload: PromptUpdate,
                  user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    p = prompts_repo.update(db, prompt_id, label=payload.label, body=payload.body,
                            is_default=payload.is_default, updated_by=user.id)
    return PromptOut(**vars(p))


@router.delete("/prompts/{prompt_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_prompt(prompt_id: int, user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    prompts_repo.delete(db, prompt_id)
```

Register in `backend/main.py`:
```python
from backend.routers import prompts as prompts_router
app.include_router(prompts_router.router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_prompts_api.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/routers/prompts.py backend/main.py tests/test_prompts_api.py
git commit -m "feat(backend): prompts CRUD router"
```

---

### Task 8: Generation stub router

**Files:**
- Create: `backend/routers/generate.py`
- Modify: `backend/main.py` (include the router)
- Test: `tests/test_generate_api.py`

**Interfaces:**
- Consumes: `get_current_user`; `schemas.GenerateRequest/GenerateResponse`.
- Produces: `router` with `POST /api/generate/image`, `POST /api/generate/video`. Both return HTTP 501 with a `GenerateResponse`-shaped body. **Phase 3 replaces only the handler bodies.**

- [ ] **Step 1: Write the failing test**

```python
# tests/test_generate_api.py
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.auth import get_current_user, CurrentUser
from mockup_generator.db.profiles_repo import Profile


@pytest.fixture
def client():
    u = CurrentUser(id="u1", email="a@b.c", role="user",
                    profile=Profile(id="u1", email="a@b.c", role="user", is_active=True))
    app.dependency_overrides[get_current_user] = lambda: u
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_generate_image_stub_501(client):
    r = client.post("/api/generate/image", json={"productid": "BC25001", "prompt": "x"})
    assert r.status_code == 501
    assert "Phase 3" in r.json()["detail"]


def test_generate_video_stub_501(client):
    r = client.post("/api/generate/video", json={"productid": "BC25001", "prompt": "x"})
    assert r.status_code == 501
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_generate_api.py -v`
Expected: FAIL — `backend.routers.generate` missing.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/routers/generate.py
"""Generation endpoints. Phase 2 = stubs; Phase 3 wires Drive + Gemini/VEO."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from backend.auth import CurrentUser, get_current_user
from backend.schemas import GenerateRequest

router = APIRouter(prefix="/api/generate", tags=["generate"])

_NOT_READY = "Generation is enabled in Phase 3 (needs Drive service-account setup)."


@router.post("/image")
def generate_image(req: GenerateRequest, user: CurrentUser = Depends(get_current_user)):
    return JSONResponse(status_code=501, content={"status": "not_implemented", "detail": _NOT_READY})


@router.post("/video")
def generate_video(req: GenerateRequest, user: CurrentUser = Depends(get_current_user)):
    return JSONResponse(status_code=501, content={"status": "not_implemented", "detail": _NOT_READY})
```

Register in `backend/main.py`:
```python
from backend.routers import generate as generate_router
app.include_router(generate_router.router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_generate_api.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/routers/generate.py backend/main.py tests/test_generate_api.py
git commit -m "feat(backend): generation stub endpoints (image/video)"
```

---

### Task 9: Full router wiring + backend suite green

**Files:**
- Modify: `backend/main.py` (consolidate router includes; confirm all three present)
- Test: full suite

**Interfaces:**
- Consumes: routers from Tasks 6–8.
- Produces: a `backend/main.py` whose body, after the CORS middleware, includes all three routers and keeps `/api/health` + `/api/me`.

- [ ] **Step 1: Confirm `backend/main.py` includes all routers**

The imports/includes added across Tasks 6–8 should leave `main.py` like:

```python
from backend.routers import generate as generate_router
from backend.routers import products as products_router
from backend.routers import prompts as prompts_router

# ... app = FastAPI(...) and CORS middleware unchanged ...

app.include_router(products_router.router)
app.include_router(prompts_router.router)
app.include_router(generate_router.router)
```

(Leave the existing `/api/health` and `/api/me` handlers in place.)

- [ ] **Step 2: Run the whole backend + repo suite**

Run: `poetry run pytest -q`
Expected: PASS — `test_imports`, `test_product_ids`, `test_products_repo`, `test_prompts_repo`, `test_products_api`, `test_prompts_api`, `test_generate_api`.

- [ ] **Step 3: Smoke-run the server**

Run: `poetry run uvicorn backend.main:app --port 8000 &` then `curl -s localhost:8000/api/health` ; then `kill %1`
Expected: `{"status":"ok"}` and no import errors on startup.

- [ ] **Step 4: Commit**

```bash
git add backend/main.py
git commit -m "chore(backend): wire products/prompts/generate routers"
```

---

### Task 10: Frontend API client

**Files:**
- Modify: `frontend/src/api.ts`

**Interfaces:**
- Consumes: `apiFetch` (existing).
- Produces: types `Category`, `Product`, `Prompt`; functions `getCategories`, `listProducts(params)`, `getProduct(id)`, `listPrompts(categoryid)`, `createPrompt(body)`, `updatePrompt(id, body)`, `deletePrompt(id)`, `generateImage(body)`, `generateVideo(body)`.

- [ ] **Step 1: Append to `frontend/src/api.ts`**

```typescript
export interface Category { categoryid: string; name: string; }
export interface Product {
  productid: string; name: string; categoryid: string | null;
  category_name: string | null; base_mockup: boolean; producturl: string | null;
}
export interface Prompt {
  prompt_id: number; categoryid: string; label: string; body: string; is_default: boolean;
}
export interface GenResult { status: string; detail: string; }

export const getCategories = () => apiFetch<Category[]>("/api/categories");

export function listProducts(p: {
  category?: string; id?: string; id_start?: string; id_end?: string;
  pending?: boolean; limit?: number; offset?: number;
}): Promise<Product[]> {
  const q = new URLSearchParams();
  if (p.category) q.set("category", p.category);
  if (p.id) q.set("id", p.id);
  if (p.id_start) q.set("id_start", p.id_start);
  if (p.id_end) q.set("id_end", p.id_end);
  q.set("pending", String(p.pending ?? true));
  if (p.limit != null) q.set("limit", String(p.limit));
  if (p.offset != null) q.set("offset", String(p.offset));
  return apiFetch<Product[]>(`/api/products?${q.toString()}`);
}

export const getProduct = (id: string) => apiFetch<Product>(`/api/products/${encodeURIComponent(id)}`);

export const listPrompts = (categoryid: string) =>
  apiFetch<Prompt[]>(`/api/prompts?categoryid=${encodeURIComponent(categoryid)}`);

export const createPrompt = (b: { categoryid: string; label: string; body: string; is_default?: boolean }) =>
  apiFetch<Prompt>("/api/prompts", { method: "POST", body: JSON.stringify(b) });

export const updatePrompt = (id: number, b: { label?: string; body?: string; is_default?: boolean }) =>
  apiFetch<Prompt>(`/api/prompts/${id}`, { method: "PATCH", body: JSON.stringify(b) });

export const deletePrompt = (id: number) =>
  apiFetch<void>(`/api/prompts/${id}`, { method: "DELETE" });

export const generateImage = (b: { productid: string; prompt: string }) =>
  apiFetch<GenResult>("/api/generate/image", { method: "POST", body: JSON.stringify(b) });

export const generateVideo = (b: { productid: string; prompt: string }) =>
  apiFetch<GenResult>("/api/generate/video", { method: "POST", body: JSON.stringify(b) });
```

Note: `deletePrompt` returns 204 (no body). `apiFetch` calls `res.json()`; guard it — change the final line of `apiFetch` to tolerate empty bodies:

```typescript
  // in apiFetch, replace `return res.json() as Promise<T>;`
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && npm run build`
Expected: build succeeds, no TS errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api.ts
git commit -m "feat(frontend): typed API client for products/prompts/generate"
```

---

### Task 11: Products tab component

**Files:**
- Create: `frontend/src/components/ProductsTab.tsx`

**Interfaces:**
- Consumes: `getCategories`, `listProducts`, `listPrompts`, `generateImage`, `generateVideo` from `../api`.
- Produces: default-exported `<ProductsTab />` component.

- [ ] **Step 1: Write the component**

```tsx
// frontend/src/components/ProductsTab.tsx
import { useEffect, useState } from "react";
import {
  getCategories, listProducts, listPrompts, generateImage, generateVideo,
  type Category, type Product, type Prompt,
} from "../api";

export default function ProductsTab() {
  const [cats, setCats] = useState<Category[]>([]);
  const [category, setCategory] = useState("");
  const [idSingle, setIdSingle] = useState("");
  const [idEnd, setIdEnd] = useState("");
  const [pending, setPending] = useState(true);
  const [rows, setRows] = useState<Product[]>([]);
  const [selected, setSelected] = useState<Product | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { getCategories().then(setCats).catch((e) => setErr(e.message)); }, []);

  const search = () => {
    setErr(null);
    const params: Parameters<typeof listProducts>[0] = { pending };
    if (category) params.category = category;
    if (idSingle && idEnd) { params.id_start = idSingle; params.id_end = idEnd; }
    else if (idSingle) params.id = idSingle;
    listProducts(params).then(setRows).catch((e) => setErr(e.message));
  };

  return (
    <div style={{ display: "flex", gap: 24 }}>
      <div style={{ flex: 1 }}>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <select value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">All categories</option>
            {cats.map((c) => <option key={c.categoryid} value={c.categoryid}>{c.name}</option>)}
          </select>
          <input placeholder="Product ID (e.g. BC25001)" value={idSingle}
                 onChange={(e) => setIdSingle(e.target.value)} />
          <input placeholder="…to ID (range end, optional)" value={idEnd}
                 onChange={(e) => setIdEnd(e.target.value)} />
          <label><input type="checkbox" checked={pending}
                 onChange={(e) => setPending(e.target.checked)} /> pending only</label>
          <button onClick={search}>Search</button>
        </div>
        {err && <p style={{ color: "#b00" }}>{err}</p>}
        <table style={{ width: "100%", marginTop: 12, borderCollapse: "collapse" }}>
          <thead><tr><th align="left">ID</th><th align="left">Name</th><th align="left">Category</th><th>Status</th></tr></thead>
          <tbody>
            {rows.map((p) => (
              <tr key={p.productid} onClick={() => setSelected(p)}
                  style={{ cursor: "pointer", background: selected?.productid === p.productid ? "#eef" : undefined }}>
                <td>{p.productid}</td><td>{p.name}</td><td>{p.category_name ?? p.categoryid}</td>
                <td align="center">{p.base_mockup ? "✅ done" : "⏳ pending"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {selected && <ProductDetail product={selected} />}
    </div>
  );
}

function ProductDetail({ product }: { product: Product }) {
  const [prompts, setPrompts] = useState<Prompt[]>([]);
  const [promptText, setPromptText] = useState("");
  const [videoPrompt, setVideoPrompt] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    setMsg(null);
    if (!product.categoryid) { setPrompts([]); setPromptText(""); return; }
    listPrompts(product.categoryid).then((ps) => {
      setPrompts(ps);
      const def = ps.find((p) => p.is_default) ?? ps[0];
      setPromptText(def?.body ?? "");
    }).catch((e) => setMsg(e.message));
  }, [product.productid, product.categoryid]);

  const genImage = () =>
    generateImage({ productid: product.productid, prompt: promptText })
      .then((r) => setMsg(r.detail)).catch((e) => setMsg(e.message));
  const genVideo = () =>
    generateVideo({ productid: product.productid, prompt: videoPrompt })
      .then((r) => setMsg(r.detail)).catch((e) => setMsg(e.message));

  return (
    <div style={{ flex: 1, borderLeft: "1px solid #ddd", paddingLeft: 16 }}>
      <h3>{product.productid} — {product.name}</h3>
      {product.producturl
        ? <a href={product.producturl} target="_blank" rel="noreferrer">Open Drive folder ↗</a>
        : <em>No producturl</em>}
      <h4>Image prompt</h4>
      <select onChange={(e) => {
        const p = prompts.find((x) => String(x.prompt_id) === e.target.value);
        if (p) setPromptText(p.body);
      }}>
        {prompts.map((p) => <option key={p.prompt_id} value={p.prompt_id}>{p.label}{p.is_default ? " (default)" : ""}</option>)}
      </select>
      <textarea value={promptText} onChange={(e) => setPromptText(e.target.value)}
                rows={6} style={{ width: "100%", marginTop: 8 }} />
      <button onClick={genImage}>Generate Image</button>
      <h4>Video (custom prompt)</h4>
      <textarea value={videoPrompt} onChange={(e) => setVideoPrompt(e.target.value)}
                rows={4} style={{ width: "100%" }} placeholder="Describe the video for this product…" />
      <button onClick={genVideo} disabled={!videoPrompt.trim()}>Generate Video</button>
      {msg && <p style={{ marginTop: 8, color: "#555" }}>{msg}</p>}
    </div>
  );
}
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ProductsTab.tsx
git commit -m "feat(frontend): products tab (search, select, generate)"
```

---

### Task 12: Prompts tab component

**Files:**
- Create: `frontend/src/components/PromptsTab.tsx`

**Interfaces:**
- Consumes: `getCategories`, `listPrompts`, `createPrompt`, `updatePrompt`, `deletePrompt`.
- Produces: default-exported `<PromptsTab />`.

- [ ] **Step 1: Write the component**

```tsx
// frontend/src/components/PromptsTab.tsx
import { useEffect, useState } from "react";
import {
  getCategories, listPrompts, createPrompt, updatePrompt, deletePrompt,
  type Category, type Prompt,
} from "../api";

export default function PromptsTab() {
  const [cats, setCats] = useState<Category[]>([]);
  const [category, setCategory] = useState("");
  const [prompts, setPrompts] = useState<Prompt[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { getCategories().then(setCats).catch((e) => setErr(e.message)); }, []);

  const reload = (cat: string) => {
    if (!cat) { setPrompts([]); return; }
    listPrompts(cat).then(setPrompts).catch((e) => setErr(e.message));
  };
  useEffect(() => reload(category), [category]);

  const addNew = () =>
    createPrompt({ categoryid: category, label: "New prompt", body: "" })
      .then(() => reload(category)).catch((e) => setErr(e.message));

  return (
    <div>
      {err && <p style={{ color: "#b00" }}>{err}</p>}
      <select value={category} onChange={(e) => setCategory(e.target.value)}>
        <option value="">Select category…</option>
        {cats.map((c) => <option key={c.categoryid} value={c.categoryid}>{c.name}</option>)}
      </select>
      {category && <button onClick={addNew} style={{ marginLeft: 8 }}>+ Add prompt</button>}
      <div style={{ marginTop: 16, display: "flex", flexDirection: "column", gap: 16 }}>
        {prompts.map((p) => (
          <PromptEditor key={p.prompt_id} prompt={p}
            onSaved={() => reload(category)} onDeleted={() => reload(category)} onError={setErr} />
        ))}
      </div>
    </div>
  );
}

function PromptEditor({ prompt, onSaved, onDeleted, onError }: {
  prompt: Prompt; onSaved: () => void; onDeleted: () => void; onError: (m: string) => void;
}) {
  const [label, setLabel] = useState(prompt.label);
  const [body, setBody] = useState(prompt.body);
  const [isDefault, setIsDefault] = useState(prompt.is_default);

  const save = () =>
    updatePrompt(prompt.prompt_id, { label, body, is_default: isDefault })
      .then(onSaved).catch((e) => onError(e.message));
  const remove = () =>
    deletePrompt(prompt.prompt_id).then(onDeleted).catch((e) => onError(e.message));

  return (
    <div style={{ border: "1px solid #ddd", borderRadius: 6, padding: 12 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Label" />
        <label><input type="checkbox" checked={isDefault}
               onChange={(e) => setIsDefault(e.target.checked)} /> default</label>
        <button onClick={save}>Save</button>
        <button onClick={remove} style={{ color: "#b00" }}>Delete</button>
      </div>
      <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={6}
                style={{ width: "100%", marginTop: 8 }} />
    </div>
  );
}
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/PromptsTab.tsx
git commit -m "feat(frontend): prompts tab (CRUD per category)"
```

---

### Task 13: App shell with tabs

**Files:**
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `<ProductsTab />`, `<PromptsTab />`.
- Produces: the post-login view renders a tab switcher (Products | Prompts) instead of the placeholder paragraph.

- [ ] **Step 1: Replace the placeholder block in `App.tsx`**

Replace the final authenticated `return (...)` block (the one containing
"You're in. Product list, generation, and review coming in the next phases.")
with:

```tsx
  return <Shell me={me} onSignOut={signOut} />;
}

function Shell({ me, onSignOut }: { me: Me; onSignOut: () => void }) {
  const [tab, setTab] = useState<"products" | "prompts">("products");
  return (
    <div style={{ padding: 32, fontFamily: "system-ui, sans-serif" }}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h1>Mockup Generator</h1>
        <div>
          <span style={{ marginRight: 12 }}>{me.email} · <strong>{me.role}</strong></span>
          <button onClick={onSignOut}>Sign out</button>
        </div>
      </header>
      <nav style={{ display: "flex", gap: 8, margin: "16px 0", borderBottom: "1px solid #ddd" }}>
        {(["products", "prompts"] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)}
            style={{ fontWeight: tab === t ? 700 : 400, border: "none", background: "none",
                     borderBottom: tab === t ? "2px solid #333" : "2px solid transparent", padding: "8px 12px", cursor: "pointer" }}>
            {t === "products" ? "Products" : "Prompts"}
          </button>
        ))}
      </nav>
      {tab === "products" ? <ProductsTab /> : <PromptsTab />}
    </div>
  );
}
```

Add the imports at the top of `App.tsx`:
```tsx
import ProductsTab from "./components/ProductsTab";
import PromptsTab from "./components/PromptsTab";
```
(`useState` is already imported.)

- [ ] **Step 2: Type-check + build**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 3: Manual smoke (local split dev)**

Run backend (`poetry run uvicorn backend.main:app --port 8000`) and frontend
(`cd frontend && npm run dev` with `VITE_API_URL=http://localhost:8000`). Log in,
confirm: Products tab searches/filters and shows producturl; selecting a row
loads its category prompt; Generate buttons show the Phase-3 "not enabled"
message; Prompts tab lists/edits/adds/deletes variants.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat(frontend): tabbed shell (Products | Prompts)"
```

---

## Self-Review

**Spec coverage:**
- Product selection by category / single id / range → Tasks 2, 3, 6, 11. ✓
- Pending-default list, generate allowed regardless → Task 6 (`pending` default true), Task 11. ✓
- producturl display → Tasks 3/11. ✓
- Prompts: multiple named variants per category, view/edit/add/delete/default → Tasks 1, 4, 7, 12. ✓
- Generate image (category prompt) + video (custom prompt) as stubs → Tasks 8, 11. ✓
- Any active user, no admin gate → all routers depend only on `get_current_user`. ✓
- Additive migration + seed from CATEGORY_PROMPTS → Tasks 1, 4. ✓
- Numeric-key range (not lexical) → Tasks 1 (view `id_key`), 2 (parser). ✓
- Phase 3 seam (swap handler bodies only) → Task 8. ✓
- Tests → every backend task is TDD; frontend gated on `npm run build`. ✓

**Placeholder scan:** none — all code blocks are complete; the only deferred behavior (generation) is an intentional, tested 501 stub.

**Type consistency:** `Product`/`Prompt`/`Category` field names match across repo dataclasses (`vars(p)` → Pydantic `**`), schemas, and TS interfaces. Repo function names (`list_products`, `get_product`, `list_categories`, `list_by_category`, `create`, `update`, `delete`, `seed_defaults`, `get_flags`) are referenced identically in routers and tests. API paths match between routers and `api.ts`.

## Notes / setup
- **Optional but recommended:** set `SUPABASE_SECRET_KEY` on the HF Space so `get_db` uses the service client (bypasses RLS). Without it, the backend acts as the logged-in user and relies on existing RLS allowing `authenticated` to select `products`/`categories`/`mockups` and the `prompts` policy from Task 1.
- The `mockup_variations` table and real generation wiring remain Phase 3.
