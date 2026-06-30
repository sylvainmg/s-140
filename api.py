"""FastAPI backend for the S-140 weekly program generator.

Scrapes jw.org monthly program data and renders it as an A4 PDF
with congregation assignment support.
"""
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from concurrent.futures import ThreadPoolExecutor, as_completed

from parse import get_week_urls, scrape_week, extract_month_label, resolve_url
from render import attach_assignments, render_html, html_to_pdf

app = FastAPI(title="S-140 Backend", version="1.0.0")

BASE_DIR = Path(__file__).parent


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
    try:
        resolved = resolve_url(req.url)
        week_urls = get_week_urls(resolved)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erreur fetch index : {e}")

    if not week_urls:
        raise HTTPException(
            status_code=404, detail="Aucune semaine trouvée à cette URL."
        )

    program: dict = {}
    errors: list[dict] = []

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(scrape_week, url): url for url in week_urls}

        for future in as_completed(futures):
            url = futures[future]
            try:
                result = future.result()
                if result:
                    week_key, data = result
                    program[week_key] = data
            except Exception as e:
                errors.append({"url": url, "error": str(e)})

    if not program:
        raise HTTPException(status_code=500, detail=f"Parsing échoué : {errors}")

    return {
        "program": program,
        "month_label": extract_month_label(resolved),
        "errors": errors,
    }


@app.post("/render")
def render(req: RenderRequest):
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

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        html_to_pdf(html, tmp_path)
        pdf_bytes = Path(tmp_path).read_bytes()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur génération PDF : {e}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    filename = f"S-140-{req.month_label}.pdf" if req.month_label else "S-140.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )

