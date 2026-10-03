import os
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .auth import SESSION_COOKIE, Auth
from .logs import LogStore, round_started

ROOT_PATH = os.environ.get("ROOT_PATH", "")
PAGE_SIZE = 200

app = FastAPI(root_path=ROOT_PATH, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
store = LogStore(os.environ.get("LOGS_DIR", "/logs"))
auth = Auth()


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path.removeprefix(ROOT_PATH)
    if path.startswith(("/auth/", "/static/")):
        return await call_next(request)
    request.state.user = auth.identity(request.cookies.get(SESSION_COOKIE))
    if not request.state.user:
        return templates.TemplateResponse(request, "login.html", {"login_url": f"{ROOT_PATH}/auth/login"}, status_code=401)
    return await call_next(request)


@app.get("/auth/login")
async def login():
    return RedirectResponse(auth.login_url())


@app.get("/auth/callback")
async def callback(request: Request, code: str = "", state: str = ""):
    identity, error = await auth.complete(code, state)
    if error:
        return templates.TemplateResponse(request, "login.html", {"login_url": f"{ROOT_PATH}/auth/login", "error": error}, status_code=403)
    response = RedirectResponse(f"{ROOT_PATH}/")
    response.set_cookie(SESSION_COOKIE, auth.cookie(identity), max_age=14 * 24 * 3600, httponly=True, samesite="lax")
    return response


@app.get("/auth/logout")
async def logout():
    response = RedirectResponse(f"{ROOT_PATH}/")
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/", response_class=HTMLResponse)
async def rounds(request: Request):
    items = [(r, round_started(r)) for r in store.rounds()[:60]]
    return templates.TemplateResponse(request, "rounds.html", {"rounds": items, "user": request.state.user})


@app.get("/round/{number}", response_class=HTMLResponse)
async def round_view(request: Request, number: int, q: str = "", ckey: str = "", char: str = "", cat: list[str] | None = None, start: str = "", end: str = "", page: int = 1):
    round_ = store.round(number)
    if not round_:
        return PlainTextResponse("Раунд не найден", status_code=404)
    cats = tuple(cat or ())
    hits = list(store.search(round_, q, ckey, char, cats, start, end)) if (q or ckey or char or cats or start or end) else []
    pages = max(1, (len(hits) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(max(page, 1), pages)
    return templates.TemplateResponse(request, "round.html", {
        "round": round_,
        "categories": store.categories(round_),
        "hits": hits[(page - 1) * PAGE_SIZE : page * PAGE_SIZE],
        "total": len(hits),
        "page": page,
        "pages": pages,
        "filters": {"q": q, "ckey": ckey, "char": char, "cat": cats, "start": start, "end": end},
        "query_base": urlencode([("q", q), ("ckey", ckey), ("char", char), ("start", start), ("end", end)] + [("cat", c) for c in cats]),
        "user": request.state.user,
    })


@app.get("/round/{number}/context/{index}", response_class=HTMLResponse)
async def context_view(request: Request, number: int, index: int):
    round_ = store.round(number)
    if not round_:
        return PlainTextResponse("Раунд не найден", status_code=404)
    return templates.TemplateResponse(request, "context.html", {
        "round": round_,
        "entries": store.context(round_, index),
        "focus": index,
        "user": request.state.user,
    })


@app.get("/round/{number}/export")
async def export(number: int, ckey: str = "", char: str = "", q: str = ""):
    round_ = store.round(number)
    if not round_:
        return PlainTextResponse("Раунд не найден", status_code=404)
    lines = [f"[{e.ts}] {e.cat}: {e.msg}" for e in store.search(round_, q, ckey, char)]
    name = f"round-{number}-{ckey or char or 'search'}.txt"
    return PlainTextResponse("\n".join(lines), headers={"Content-Disposition": f'attachment; filename="{name}"'})
