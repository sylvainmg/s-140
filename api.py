"""FastAPI backend for the S-140 weekly program generator.

Scrapes jw.org monthly program data and renders it as an A4 PDF
with congregation assignment support.
"""
import sys
import asyncio

# Fix Windows : uvicorn peut forcer SelectorEventLoop, qui ne supporte pas
# create_subprocess_exec (nécessaire à Playwright pour lancer Chromium).
# Sans ça, le navigateur persistant échoue au démarrage en local sous Windows
# (pas un problème en prod sur Render/Linux).
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

from cachetools import TTLCache
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from concurrent.futures import ThreadPoolExecutor, as_completed

from parse import get_week_urls, scrape_week, extract_month_label, resolve_url
from render import attach_assignments, render_html, html_to_pdf, render_pdf_async

BASE_DIR = Path(__file__).parent

# Cache en mémoire : le programme d'un mois donné ne change pas une fois publié
# sur jw.org. Clé = URL soumise par le client, TTL de 6h.
_parse_cache: TTLCache = TTLCache(maxsize=50, ttl=6 * 3600)

# Navigateur Playwright persistant, partagé entre toutes les requêtes /render.
# Lancé une seule fois au démarrage pour éviter le coût de démarrage de
# Chromium (~1-2s) à chaque appel. Si l'initialisation échoue (Playwright non
# installé, etc.), on retombe sur l'ancienne méthode (navigateur relancé à
# chaque requête) plutôt que de planter le serveur entier.
_playwright = None
_browser = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _playwright, _browser
    try:
        from playwright.async_api import async_playwright

        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch()
        print("[startup] Navigateur Playwright persistant démarré.")
    except Exception as e:
        print(f"[startup] ⚠️ Playwright persistant indisponible ({e}) — fallback par requête.")
        _playwright = None
        _browser = None

    yield

    if _browser is not None:
        await _browser.close()
    if _playwright is not None:
        await _playwright.stop()


app = FastAPI(title="S-140 Backend", version="1.0.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Schémas
# ---------------------------------------------------------------------------


class ParseRequest(BaseModel):
    url: str  # URL de l'index mensuel S-140 de jw.org


class RenderRequest(BaseModel):
    program: dict  # { "6-12 Jolay": { "bible_reading": ..., "full_ordered_program": [...] } }
    assignments: dict = {}  # optionnel
    church_name: str = "[ANARAN'NY FIANGONANA]"
    month_label: str = ""


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
@app.head("/health")
def health():
    return {"status": "ok"}


@app.post("/parse")
def parse(req: ParseRequest):
    cached = _parse_cache.get(req.url)
    if cached is not None:
        print(f"[/parse] cache hit -> {req.url}")
        return cached

    t0 = time.perf_counter()
    try:
        resolved = resolve_url(req.url)
        week_urls = get_week_urls(resolved)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erreur fetch index : {e}")
    t1 = time.perf_counter()

    if not week_urls:
        raise HTTPException(
            status_code=404, detail="Aucune semaine trouvée à cette URL."
        )

    results_by_url: dict = {}
    errors: list[dict] = []

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(scrape_week, url): url for url in week_urls}

        for future in as_completed(futures):
            url = futures[future]
            try:
                result = future.result()
                if result:
                    results_by_url[url] = result
            except Exception as e:
                errors.append({"url": url, "error": str(e)})

    # Reconstruction dans l'ordre chronologique de week_urls : le scraping
    # tourne en parallèle, donc les résultats arrivent dans un ordre variable
    # (le plus rapide en premier), pas forcément l'ordre des semaines.
    program: dict = {}
    for url in week_urls:
        if url in results_by_url:
            week_key, data = results_by_url[url]
            program[week_key] = data
    t2 = time.perf_counter()

    print(
        f"[/parse] resolve+index={t1 - t0:.2f}s "
        f"scrape({len(week_urls)} semaines)={t2 - t1:.2f}s "
        f"total={t2 - t0:.2f}s"
    )

    if not program:
        raise HTTPException(status_code=500, detail=f"Parsing échoué : {errors}")

    result = {
        "program": program,
        "month_label": extract_month_label(resolved),
        "errors": errors,
    }
    _parse_cache[req.url] = result
    return result


@app.post("/render")
async def render(req: RenderRequest):
    weeks: list[dict] = []
    for date_range, content in req.program.items():
        weeks.append(
            {
                "date_range": date_range,
                "bible_reading": content.get("bible_reading", ""),
                "full_ordered_program": content.get("full_ordered_program", []),
            }
        )

    if not weeks:
        raise HTTPException(status_code=400, detail="Le programme est vide.")

    attach_assignments(weeks, req.assignments or None)
    try:
        html = render_html(weeks, req.church_name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur rendu HTML : {e}")

    t0 = time.perf_counter()
    try:
        if _browser is not None:
            # Chemin rapide : navigateur persistant, tout en mémoire.
            pdf_bytes = await render_pdf_async(_browser, html)
        else:
            # Fallback : ancienne méthode (relance un navigateur, passe par le
            # disque). Ne devrait arriver que si le lifespan a échoué au démarrage.
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp_path = tmp.name
            try:
                html_to_pdf(html, tmp_path)
                pdf_bytes = Path(tmp_path).read_bytes()
            finally:
                Path(tmp_path).unlink(missing_ok=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur génération PDF : {e}")
    t1 = time.perf_counter()
    print(f"[/render] pdf={t1 - t0:.2f}s (browser persistant={_browser is not None})")

    filename = f"S-140-{req.month_label}.pdf" if req.month_label else "S-140.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
