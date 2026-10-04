import asyncio
import os
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .auth import SESSION_COOKIE, Auth
from .logs import CATEGORIES, LogStore, round_map, round_started
from .maps import MapStore

ROOT_PATH = os.environ.get("ROOT_PATH", "")
PAGE_SIZE = 200

app = FastAPI(root_path=ROOT_PATH, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.globals["static_version"] = str(int((Path(__file__).parent / "static" / "style.css").stat().st_mtime))
store = LogStore(os.environ.get("LOGS_DIR", "/logs"))
maps = MapStore(os.environ.get("GAME_DIR", "/game"), os.environ.get("MAPS_DIR", "/maps"), os.environ.get("DMM_TOOLS", "/usr/local/bin/dmm-tools"))
auth = Auth()
MAX_MAP_EVENTS = 4000


def render_in_background():
    if maps.available() and not maps.rendering():
        asyncio.get_event_loop().run_in_executor(None, maps.render_missing)


@app.on_event("startup")
async def render_maps():
    render_in_background()
    asyncio.get_event_loop().create_task(render_periodically())


async def render_periodically():
    while True:
        await asyncio.sleep(600)
        render_in_background()


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
async def round_view(request: Request, number: int, q: str = "", ckey: str = "", char: str = "", cat: list[str] = Query(default=[]), start: str = "", end: str = "", page: int = 1):
    round_ = store.round(number)
    if not round_:
        return PlainTextResponse("Раунд не найден", status_code=404)
    cats = tuple(cat or ())
    hits = list(store.search(round_, q, ckey, char, cats, start, end)) if (q or ckey or char or cats or start or end) else []
    pages = max(1, (len(hits) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(max(page, 1), pages)
    known, other = store.categories(round_)
    return templates.TemplateResponse(request, "round.html", {
        "round": round_,
        "known": known,
        "other": other,
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
    entries = store.context(round_, index)
    focused = next((e for e in entries if e.index == index), None)
    return templates.TemplateResponse(request, "context.html", {
        "round": round_,
        "entries": entries,
        "focus": index,
        "offset": store.offset(round_, focused) if focused else 0,
        "user": request.state.user,
    })


@app.get("/round/{number}/map", response_class=HTMLResponse)
async def map_view(request: Request, number: int):
    round_ = store.round(number)
    if not round_:
        return PlainTextResponse("Раунд не найден", status_code=404)
    map_name = round_map(round_)
    meta = maps.meta(map_name) if map_name else None
    if map_name and not meta:
        render_in_background()
    start, duration = store.span(round_)
    known, other = store.categories(round_)
    busiest = {}
    for e in store.entries(round_):
        if e.z:
            busiest[str(e.z)] = busiest.get(str(e.z), 0) + 1
    station_levels = {z: n for z, n in busiest.items() if z != "1"}
    station_z = max(station_levels, key=station_levels.get) if station_levels else "2"
    return templates.TemplateResponse(request, "map.html", {
        "round": round_,
        "map_name": map_name,
        "meta": meta,
        "known": known,
        "other": other,
        "colors": {k: c for k, _, c in CATEGORIES},
        "labels": {k: label for k, label, _ in CATEGORIES},
        "start": start.strftime("%H:%M") if start else "",
        "duration": duration,
        "station_z": station_z,
        "user": request.state.user,
    })


@app.get("/round/{number}/events.json")
async def events(number: int, q: str = "", ckey: str = "", char: str = "", cat: list[str] = Query(default=[]), z: int = 0, start: int = 0, end: int = 10**9):
    round_ = store.round(number)
    if not round_:
        return JSONResponse({"error": "no round"}, status_code=404)
    out = []
    for e in store.search(round_, q, ckey, char, tuple(cat)):
        if not e.x or (z and e.z != z):
            continue
        offset = store.offset(round_, e)
        if offset < start or offset > end:
            continue
        out.append({"i": e.index, "t": e.time, "o": offset, "c": e.cat, "k": e.ckey, "n": e.char, "a": e.area, "x": e.x, "y": e.y, "z": e.z, "m": e.msg[:300]})
        if len(out) >= MAX_MAP_EVENTS:
            break
    return JSONResponse({"events": out, "truncated": len(out) >= MAX_MAP_EVENTS})


@app.get("/round/{number}/counts.json")
async def counts(number: int, q: str = "", ckey: str = "", char: str = ""):
    round_ = store.round(number)
    if not round_:
        return JSONResponse({"error": "no round"}, status_code=404)
    result = {}
    for e in store.search(round_, q, ckey, char):
        result[e.cat] = result.get(e.cat, 0) + 1
    return JSONResponse(result)


@app.get("/maps/{map_name}/{z}.webp")
async def map_image(map_name: str, z: int):
    path = maps.image(map_name, z)
    if not path:
        return PlainTextResponse("Нет изображения", status_code=404)
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/round/{number}/export")
async def export(number: int, ckey: str = "", char: str = "", q: str = ""):
    round_ = store.round(number)
    if not round_:
        return PlainTextResponse("Раунд не найден", status_code=404)
    lines = [f"[{e.ts}] {e.cat}: {e.msg}" for e in store.search(round_, q, ckey, char)]
    name = f"round-{number}-{ckey or char or 'search'}.txt"
    return PlainTextResponse("\n".join(lines), headers={"Content-Disposition": f'attachment; filename="{name}"'})
