"""
render.py — remplit template.html.j2 avec les données JSON du mois,
            puis génère un PDF A4 (2 semaines / page).

Usage:
    python render.py data/sample_month.json output/programme.html
    python render.py data/sample_month.json output/programme.pdf
    python render.py data/sample.json output/prog.pdf "Fiangonana Ankadifotsy"
    python render.py data/sample.json output/prog.pdf "Fiangonana" assignments.json

Moteurs PDF (essayés dans l'ordre) :
  1. Playwright/Chromium  pip install playwright && python -m playwright install chromium
  2. wkhtmltopdf          binaire externe (https://wkhtmltopdf.org)
  3. xhtml2pdf            pip install xhtml2pdf  (fallback, rendu basique)
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

TEMPLATE_DIR = Path(__file__).parent
TEMPLATE_NAME = "template.html.j2"


# ---------------------------------------------------------------------------
# Chargement des données
# ---------------------------------------------------------------------------

def load_weeks(json_path: str) -> list[dict]:
    with open(json_path, encoding="utf-8") as f:
        raw = json.load(f)
    weeks = []
    for date_range, content in raw.items():
        weeks.append({
            "date_range": date_range,
            "bible_reading": content["bible_reading"],
            "full_ordered_program": content["full_ordered_program"],
        })
    return weeks


def chunk(seq: list, size: int = 2) -> list[list]:
    return [seq[i:i + size] for i in range(0, len(seq), size)]


def attach_assignments(weeks: list[dict], assignments: dict | None) -> None:
    """
    Injecte les noms depuis un fichier d'affectations séparé.

    Structure attendue :
    {
        "6-12 Jolay": {
            "mpitari_draharaha": "Rakoto",
            "mpanome_torohevitra": "Rabe",
            "vavaka_fampidirana": "Be",
            "vavaka_famaranana": "Soa",
            "parts": {
                "2. Vatosoa Ara-panahy":  {"assignee": "Tojo"},
                "3. Famakiana Baiboly":   {"assignee": "Niry"},
                "10. Fianarana Baiboly":  {"assignee": "Hery/Soa"},
                "4. Miresadresaha Aloha": {"assignee_faharoa": "Niry/Hery", "assignee_lehibe": "Tina/Voa"},
            }
        }
    }
    """
    if not assignments:
        return
    for week in weeks:
        wa = assignments.get(week["date_range"], {})
        week["mpitari_draharaha"]   = wa.get("mpitari_draharaha", "")
        week["mpanome_torohevitra"] = wa.get("mpanome_torohevitra", "")
        week["vavaka_fampidirana"]  = wa.get("vavaka_fampidirana", "")
        week["vavaka_famaranana"]   = wa.get("vavaka_famaranana", "")
        parts_assign = wa.get("parts", {})
        for item in week["full_ordered_program"]:
            if item.get("type") == "part" and item["title"] in parts_assign:
                item.update(parts_assign[item["title"]])


# ---------------------------------------------------------------------------
# Rendu HTML
# ---------------------------------------------------------------------------

def render_html(weeks: list[dict], church_name: str) -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))
    template = env.get_template(TEMPLATE_NAME)
    return template.render(
        church_name=church_name,
        weeks_by_page=chunk(weeks, 2),
    )


# ---------------------------------------------------------------------------
# Conversion HTML -> PDF
# Ordre : Playwright (Chromium) -> wkhtmltopdf -> xhtml2pdf
# ---------------------------------------------------------------------------

def _pdf_via_playwright(html: str, output_path: str) -> None:
    """
    Rendu via Chromium headless — CSS Grid, couleurs, polices : rendu parfait.
    Installation : pip install playwright && python -m playwright install chromium
    """
    from playwright.sync_api import sync_playwright  # type: ignore

    with tempfile.NamedTemporaryFile(suffix=".html", delete=False,
                                     mode="w", encoding="utf-8") as tmp:
        tmp.write(html)
        tmp_path = Path(tmp.name)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(tmp_path.as_uri(), wait_until="networkidle")
            page.pdf(
                path=output_path,
                format="A4",
                print_background=True,   # indispensable pour les bandeaux colorés
                margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
            )
            browser.close()
    finally:
        tmp_path.unlink(missing_ok=True)


def _pdf_via_wkhtmltopdf(html: str, output_path: str) -> None:
    """Binaire externe wkhtmltopdf."""
    import shutil
    exe = shutil.which("wkhtmltopdf")
    if exe is None:
        for p in [
            r"C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe",
            r"C:\Program Files (x86)\wkhtmltopdf\bin\wkhtmltopdf.exe",
        ]:
            if Path(p).exists():
                exe = p
                break
    if exe is None:
        raise FileNotFoundError("wkhtmltopdf introuvable.")

    with tempfile.NamedTemporaryFile(suffix=".html", delete=False,
                                     mode="w", encoding="utf-8") as tmp:
        tmp.write(html)
        tmp_path = tmp.name
    try:
        cmd = [
            exe,
            "--page-size", "A4", "--orientation", "Portrait",
            "--margin-top", "0mm", "--margin-bottom", "0mm",
            "--margin-left", "0mm", "--margin-right", "0mm",
            "--encoding", "UTF-8", "--enable-local-file-access",
            "--disable-smart-shrinking", "--dpi", "150",
            "--print-media-type", "--background",
            tmp_path, output_path,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"wkhtmltopdf code {res.returncode}: {res.stderr.strip()}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _pdf_via_xhtml2pdf(html: str, output_path: str) -> None:
    """Fallback basique — CSS Grid non supporté. pip install xhtml2pdf"""
    from xhtml2pdf import pisa  # type: ignore
    with open(output_path, "wb") as f:
        result = pisa.CreatePDF(html.encode("utf-8"), dest=f, encoding="utf-8")
    if result.err:
        raise RuntimeError(f"xhtml2pdf: {result.err} erreur(s).")


def html_to_pdf(html: str, output_path: str) -> None:
    backends = [
        ("Playwright/Chromium", _pdf_via_playwright,   (ImportError,)),
        ("wkhtmltopdf",         _pdf_via_wkhtmltopdf,  (FileNotFoundError,)),
        ("xhtml2pdf",           _pdf_via_xhtml2pdf,    (ImportError,)),
    ]
    for name, fn, skip_on in backends:
        try:
            fn(html, output_path)
            print(f"  (moteur PDF : {name})")
            return
        except tuple(skip_on):
            continue

    print(
        "\n  Aucun moteur PDF disponible.\n"
        "  Installez Playwright (recommandé) :\n\n"
        "      pip install playwright\n"
        "      python -m playwright install chromium\n",
        file=sys.stderr,
    )
    raise SystemExit(1)


# ---------------------------------------------------------------------------
# Point d'entree
# ---------------------------------------------------------------------------

def render(json_path: str, output_path: str,
           church_name: str = "[ANARAN'NY FIANGONANA]",
           assignments_path: str | None = None) -> None:

    weeks = load_weeks(json_path)

    assignments = None
    if assignments_path:
        with open(assignments_path, encoding="utf-8") as f:
            assignments = json.load(f)
    attach_assignments(weeks, assignments)

    html = render_html(weeks, church_name)
    pages = chunk(weeks, 2)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    if out.suffix.lower() == ".pdf":
        html_path = out.with_suffix(".html")
        html_path.write_text(html, encoding="utf-8")
        print(f"HTML intermediaire -> {html_path}")
        html_to_pdf(html, str(out))
        print(f"OK -> {out}  ({len(weeks)} semaine(s), {len(pages)} page(s))")
    else:
        out.write_text(html, encoding="utf-8")
        print(f"OK -> {out}  ({len(weeks)} semaine(s), {len(pages)} page(s))")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python render.py <data.json> <output.html|output.pdf> "
              "[church_name] [assignments.json]")
        sys.exit(1)

    render(
        json_path        = sys.argv[1],
        output_path      = sys.argv[2],
        church_name      = sys.argv[3] if len(sys.argv) > 3 else "[ANARAN'NY FIANGONANA]",
        assignments_path = sys.argv[4] if len(sys.argv) > 4 else None,
    )