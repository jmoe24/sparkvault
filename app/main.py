"""SparkVault: authenticated, read-only catalogue + model-cache drift view.

Nothing here writes to the catalogue folder, the Hugging Face cache, or any GPU node. The one write is
POST /api/results, which parks a validated result record in the inbox (bearer token, off unless
SPARKVAULT_INGEST_TOKEN is set). Deploy and download are not part of v1.
"""
import hmac
import json
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import auth, cache, ingest
from .config import Settings
from .library import SCHEMA_PATH, Loader, flat, summarize
from .recipe import render as render_recipe

HERE = Path(__file__).parent
STATUSES = ("all", "active", "testing", "rejected")
# sort key -> (extractor, higher_is_better)
SORTS = {
    "status": (lambda s: {"active": 0, "testing": 1, "rejected": 2}[s.model["status"]], False),
    "name": (lambda s: s.model["id"], False),
    "single": (lambda s: s.single, True), "conc": (lambda s: s.conc, True),
    "ttft": (lambda s: s.ttft, False), "judge": (lambda s: s.judge, True),
    "size": (lambda s: s.model.get("size_gb"), False),
}
SORT_LABELS = {"status": "Status", "name": "Name", "single": "tok/s (single)", "conc": "tok/s (concurrent)",
               "ttft": "TTFT", "judge": "Judge score", "size": "Size"}
HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin", "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
}


class LoginRequired(Exception):
    def __init__(self, to: str):
        self.to = to


def _num(v, d=0):
    return "–" if v is None else f"{v:,.{d}f}"


def create_app(settings: Settings | None = None) -> FastAPI:
    st = settings or Settings.from_env()
    st.validate()
    app = FastAPI(title="SparkVault", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SessionMiddleware, secret_key=st.secret_key, session_cookie="sv_session",
                       max_age=12 * 3600, same_site="strict", https_only=st.cookie_secure)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    tpl = Jinja2Templates(directory=HERE / "templates")      # autoescape is on for .html
    tpl.env.filters["num"] = _num
    loader, throttle, ttl = Loader(st.catalogue_dir, st.inbox_dir), auth.Throttle(), cache.Cached()
    ingest_throttle = auth.Throttle()

    @app.middleware("http")
    async def headers(request: Request, call_next):
        resp = await call_next(request)
        for k, v in HEADERS.items():
            resp.headers.setdefault(k, v)
        return resp

    @app.exception_handler(LoginRequired)
    async def need_login(request: Request, exc: LoginRequired):
        return RedirectResponse("/login?" + urlencode({"next": exc.to}), status_code=303)

    def user(request: Request):
        if request.session.get("user") != "admin":
            raise LoginRequired(request.url.path + (f"?{request.url.query}" if request.url.query else ""))

    def csrf(request: Request) -> str:
        if "csrf" not in request.session:
            request.session["csrf"] = auth.new_csrf()
        return request.session["csrf"]

    def page(request, name, status_code=200, **ctx):
        lib = loader.get()
        ctx.update(user=request.session.get("user"), csrf=csrf(request), head=lib.head, problems=lib.problems, pending=lib.pending)
        return tpl.TemplateResponse(request, name, ctx, status_code=status_code)

    # ---- open endpoints ----
    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return PlainTextResponse("ok")

    @app.get("/login")
    def login_form(request: Request, next: str = "/"):
        return page(request, "login.html", next=auth.safe_next(next), error=None)

    @app.post("/login")
    def login(request: Request, password: str = Form(""), csrf_token: str = Form("", alias="csrf"), next: str = Form("/")):
        who = request.client.host if request.client else "?"
        if not auth.csrf_ok(request.session, csrf_token):
            return page(request, "login.html", 403, next=auth.safe_next(next), error="Session expired, try again.")
        if throttle.blocked(who):
            return page(request, "login.html", 429, next=auth.safe_next(next), error="Too many attempts. Wait a minute.")
        if not auth.verify_password(password, st.admin_hash):
            throttle.failure(who)
            return page(request, "login.html", 401, next=auth.safe_next(next), error="Wrong password.")
        throttle.success(who)
        request.session.clear()                     # new session on login
        request.session["user"] = "admin"
        request.session["csrf"] = auth.new_csrf()
        return RedirectResponse(auth.safe_next(next), status_code=303)

    @app.post("/logout")
    def logout(request: Request, csrf_token: str = Form("", alias="csrf")):
        if auth.csrf_ok(request.session, csrf_token):
            request.session.clear()
        return RedirectResponse("/login", status_code=303)

    # ---- result upload (machine clients: bearer token, no session, no cookies) ----
    @app.post("/api/results", include_in_schema=False)
    async def upload_result(request: Request):
        if not st.ingest_token:
            return JSONResponse({"error": "result upload is not enabled"}, status_code=404)
        who = request.client.host if request.client else "?"
        if ingest_throttle.blocked(who):
            return JSONResponse({"error": "too many failed attempts"}, status_code=429)
        given = request.headers.get("authorization", "")
        if not (given.startswith("Bearer ") and hmac.compare_digest(given[7:].encode(), st.ingest_token.encode())):
            ingest_throttle.failure(who)
            return JSONResponse({"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
        ingest_throttle.success(who)
        try:
            declared = int(request.headers.get("content-length") or 0)
        except ValueError:
            return JSONResponse({"error": "bad Content-Length"}, status_code=400)
        if declared > ingest.MAX_BYTES:                 # checked before the body is read
            return JSONResponse({"error": f"record larger than {ingest.MAX_BYTES} bytes"}, status_code=413)
        lib = loader.get()
        try:
            rec = ingest.parse(await request.body(), json.loads(SCHEMA_PATH.read_text()),
                               {m["id"] for m in lib.models}, {r["id"] for r in lib.results})
            ingest.store(st.inbox_dir, rec)
        except ingest.Rejected as e:
            return JSONResponse({"error": e.message}, status_code=e.status)
        except (OSError, ValueError):
            return JSONResponse({"error": "server could not read the schema"}, status_code=503)
        return JSONResponse({"id": rec["id"], "status": "pending"}, status_code=201)

    # ---- catalogue ----
    @app.get("/")
    def catalogue(request: Request, status: str = "all", node: str = "all", has_results: str = "",
                  sort: str = "status", dir: str = "", mode: str = "cards", q: str = ""):
        user(request)
        status = status if status in STATUSES else "all"
        nodes = ("all", *st.nodes)
        node = node if node in nodes else "all"
        sort = sort if sort in SORTS else "status"
        mode = mode if mode in ("cards", "table") else "cards"
        q = q.strip()[:100]
        lib = loader.get()
        key, higher = SORTS[sort]
        desc = (dir == "desc") if dir in ("asc", "desc") else higher
        rows = [summarize(m, lib.results) for m in lib.models]
        counts = {s: sum(1 for r in rows if s == "all" or r.model["status"] == s) for s in STATUSES}
        node_counts = {n: sum(1 for r in rows if n == "all" or n in r.model.get("on_nodes", [])) for n in nodes}
        n_with_results = sum(1 for r in rows if r.runs)

        def keep(r):
            m = r.model
            if status != "all" and m["status"] != status: return False
            if node != "all" and node not in m.get("on_nodes", []): return False
            if has_results and not r.runs: return False
            hay = " ".join(str(m.get(k) or "") for k in ("id", "hf_repo", "served_name", "litellm_alias", "quant", "notes")).lower()
            return not q or q.lower() in hay
        rows = [r for r in rows if keep(r)]
        have = sorted((r for r in rows if key(r) is not None), key=key, reverse=desc)
        rows = have + [r for r in rows if key(r) is None]            # missing values always last
        params = dict(status=status, node=node, has_results=has_results, sort=sort, dir=("desc" if desc else "asc"), mode=mode, q=q)

        def link(**over):
            p = {**params, **over}
            default = dict(status="all", node="all", has_results="", sort="status", mode="cards", q="")
            return "/?" + urlencode({k: v for k, v in p.items() if v and v != default.get(k) and not (k == "dir" and sort == "status" and v == "asc")})
        return page(request, "catalogue.html", nav="catalogue", rows=rows, params=params, link=link, counts=counts,
                    node_counts=node_counts, nodes=nodes, n_with_results=n_with_results, total=len(lib.models),
                    sort_labels=SORT_LABELS, higher=higher, desc=desc)

    @app.get("/m/{model_id}")
    def detail(request: Request, model_id: str):
        user(request)
        lib = loader.get()
        m = next((m for m in lib.models if m["id"] == model_id), None)
        if m is None:
            return page(request, "notfound.html", 404, nav="catalogue", what=model_id)
        info = ttl.get(("model", m["hf_repo"]), lambda: cache.scan_model(st.hf_dir, m["hf_repo"]))
        state, msg = cache.drift(m, info)
        r = m.get("recipe")
        return page(request, "detail.html", nav="catalogue", s=summarize(m, lib.results), m=m, info=info,
                    state=state, state_msg=msg, recipe=r, runs=[flat(x) for x in summarize(m, lib.results).runs],
                    evals=[x for x in summarize(m, lib.results).runs if x.get("eval", {}).get("prompts")],
                    command=render_recipe(r, m["hf_repo"]) if r else None)

    @app.get("/drift")
    def drift(request: Request):
        user(request)
        lib = loader.get()
        rows = []
        for m in lib.models:
            info = ttl.get(("model", m["hf_repo"]), lambda m=m: cache.scan_model(st.hf_dir, m["hf_repo"]))
            state, msg = cache.drift(m, info)
            rows.append(dict(m=m, info=info, state=state, msg=msg))
        known = {m["hf_repo"] for m in lib.models}
        extra = [r for r in ttl.get("repos", lambda: cache.list_repos(st.hf_dir)) if r not in known]
        stale = ttl.get("stale", lambda: cache.shared_incomplete(st.hf_dir))
        bad = sum(1 for r in rows if r["state"] in ("missing", "broken", "drift", "unreadable"))
        return page(request, "drift.html", nav="drift", rows=rows, extra=extra, stale=stale, bad=bad)

    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        if request.session.get("user") != "admin":
            return RedirectResponse("/login", status_code=303)
        return page(request, "notfound.html", 404, nav="", what=request.url.path)

    return app


def app_factory() -> FastAPI:       # uvicorn --factory app.main:app_factory
    return create_app()
