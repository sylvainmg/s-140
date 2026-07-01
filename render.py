"""
Module : render.py
Description : Charge les données hebdomadaires JSON, les injecte dans un template Jinja2,
              et génère un document HTML ou PDF (format A4, 2 semaines/page).
Dépendances : jinja2, playwright (recommandé), wkhtmltopdf (fallback), xhtml2pdf (fallback)
Usage CLI :
    python render.py data/sample_month.json output/programme.html
    python render.py data/sample_month.json output/programme.pdf "Fiangonana Ankadifotsy"
    python render.py data/sample_month.json output/prog.pdf "Fiangonana" assignments.json
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from jinja2 import Environment, FileSystemLoader


# ── Configuration ──
TEMPLATE_DIR = Path(__file__).parent
TEMPLATE_NAME = "template.html.j2"


# ── Chargement & Structuration des Données ──


def load_weeks(json_path: str) -> list[dict]:
    """
    Charge et normalise les données hebdomadaires depuis un fichier JSON.

    Args:
        json_path: Chemin vers le fichier JSON d'entrée.

    Returns:
        list[dict]: Liste de dictionnaires structurés (date_range, bible_reading, full_ordered_program).
    """
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
    """
    Découpe une séquence en sous-listes de taille fixe.

    Args:
        seq: Séquence à découper.
        size: Taille de chaque chunk.

    Returns:
        list[list]: Liste de chunks.
    """
    return [seq[i : i + size] for i in range(0, len(seq), size)]


def attach_assignments(weeks: list[dict], assignments: dict | None) -> None:
    """
    Injecte les noms d'orateurs/affectations dans les données hebdomadaires.

    Modifie `weeks` en place.

    Structure JSON attendue pour `assignments` :
    {
        "6-12 Jolay": {
            "mpitari_draharaha": "Rakoto",
            "mpanome_torohevitra": "Rabe",
            "vavaka_fampidirana": "Be",
            "vavaka_famaranana": "Soa",
            "parts": {
                "2. Vatosoa Ara-panahy": {"assignee": "Tojo"},
                "3. Famakiana Baiboly": {
                    "efitrano_faharoa": {"etudiant": "Niry"},
                    "efitrano_lehibe": {"etudiant": "Soa"}
                },
                "10. Fianarana Baiboly": {
                    "etudiant": "Hery",
                    "complement": "Soa"
                },
                "4. Miresadresaha Aloha": {
                    "efitrano_faharoa": {"etudiant": "Niry", "complement": "Be"},
                    "efitrano_lehibe": {"etudiant": "Tina", "complement": "Voa"}
                }
            }
        }
    }


    Args:
        weeks: Liste des semaines chargée via load_weeks().
        assignments: Dictionnaire des affectations ou None.
    """
    if not assignments:
        return

    for week in weeks:
        wa = assignments.get(week["date_range"], {})
        week["mpitari_draharaha"] = wa.get("mpitari_draharaha", "")
        week["mpanome_torohevitra"] = wa.get("mpanome_torohevitra", "")
        week["vavaka_fampidirana"] = wa.get("vavaka_fampidirana", "")
        week["vavaka_famaranana"] = wa.get("vavaka_famaranana", "")

        parts_assign = wa.get("parts", {})
        for item in week["full_ordered_program"]:
            if item.get("type") == "part" and item["title"] in parts_assign:
                item.update(parts_assign[item["title"]])


# ── Rendu HTML ──


def render_html(weeks: list[dict], church_name: str) -> str:
    """
    Rend le template Jinja2 avec les données structurées.

    Args:
        weeks: Liste des semaines à afficher.
        church_name: Nom de la congrégation/église.

    Returns:
        str: Contenu HTML complet.
    """
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))
    template = env.get_template(TEMPLATE_NAME)
    return template.render(church_name=church_name, weeks_by_page=chunk(weeks, 2))


# ── Conversion HTML -> PDF (Fallback Chain) ──


def _pdf_via_playwright(html: str, output_path: str) -> None:
    """
    Rendu PDF via Chromium headless (Playwright). Rendu CSS Grid/couleurs optimal.
    Installation : pip install playwright && python -m playwright install chromium

    Usage CLI uniquement : lance un navigateur temporaire pour ce seul rendu.
    Pour l'API (requêtes répétées), voir `render_pdf_async` qui réutilise un
    navigateur déjà démarré.
    """
    from playwright.sync_api import sync_playwright  # type: ignore

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(html, wait_until="load")
            page.pdf(
                path=output_path,
                format="A4",
                print_background=True,  # Indispensable pour les bandeaux colorés
                margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
            )
        finally:
            browser.close()


async def render_pdf_async(browser, html: str) -> bytes:
    """
    Génère un PDF à partir du HTML avec un navigateur Playwright déjà lancé (async).

    Pensé pour l'API : le navigateur est démarré une seule fois au démarrage du
    serveur (voir lifespan dans api.py) et réutilisé pour chaque requête, ce qui
    évite le coût de lancement de Chromium (~1-2s) à chaque appel. Le HTML est
    injecté directement en mémoire via `set_content` (pas de fichier .html
    temporaire) et le PDF est retourné en bytes (pas de fichier .pdf temporaire
    non plus).

    Args:
        browser: Instance Playwright Browser (async_api), déjà lancée.
        html: Contenu HTML à convertir.

    Returns:
        bytes: Contenu du PDF généré.
    """
    page = await browser.new_page()
    try:
        await page.set_content(html, wait_until="load")
        return await page.pdf(
            format="A4",
            print_background=True,
            margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
        )
    finally:
        await page.close()


def _pdf_via_wkhtmltopdf(html: str, output_path: str) -> None:
    """Rendu PDF via le binaire externe wkhtmltopdf."""
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
        raise FileNotFoundError("wkhtmltopdf introuvable. Veuillez l'installer ou utiliser Playwright.")

    with tempfile.NamedTemporaryFile(
        suffix=".html", delete=False, mode="w", encoding="utf-8"
    ) as tmp:
        tmp.write(html)
        tmp_path = tmp.name

    try:
        cmd = [
            exe,
            "--page-size", "A4",
            "--orientation", "Portrait",
            "--margin-top", "0mm", "--margin-bottom", "0mm",
            "--margin-left", "0mm", "--margin-right", "0mm",
            "--encoding", "UTF-8",
            "--enable-local-file-access",
            "--disable-smart-shrinking",
            "--dpi", "150",
            "--print-media-type",
            "--background",
            tmp_path, output_path,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"wkhtmltopdf échec (code {res.returncode}): {res.stderr.strip()}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _pdf_via_xhtml2pdf(html: str, output_path: str) -> None:
    """Fallback basique (CSS Grid non supporté). pip install xhtml2pdf"""
    from xhtml2pdf import pisa  # type: ignore

    with open(output_path, "wb") as f:
        result = pisa.CreatePDF(html.encode("utf-8"), dest=f, encoding="utf-8")
    if result.err:
        raise RuntimeError(f"xhtml2pdf a rencontré des erreurs: {result.err}")


def html_to_pdf(html: str, output_path: str) -> None:
    """
    Orchestre la conversion HTML->PDF en essayant les backends dans l'ordre de préférence.

    Args:
        html: Contenu HTML à convertir.
        output_path: Chemin de sortie du PDF.

    Raises:
        SystemExit: Si aucun moteur PDF disponible.
    """
    backends = [
        ("Playwright/Chromium", _pdf_via_playwright, (ImportError,)),
        ("wkhtmltopdf", _pdf_via_wkhtmltopdf, (FileNotFoundError,)),
        ("xhtml2pdf", _pdf_via_xhtml2pdf, (ImportError,)),
    ]

    for name, fn, skip_on in backends:
        try:
            fn(html, output_path)
            print(f"  [moteur PDF : {name}]")
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


# ── Point d'Entrée Principal ──


def render(
    json_path: str,
    output_path: str,
    church_name: str = "[ANARAN'NY FIANGONANA]",
    assignments_path: str | None = None,
) -> None:
    """
    Orchestre le pipeline complet : chargement -> injection -> rendu HTML -> export PDF/HTML.

    Args:
        json_path: Chemin vers le fichier JSON des données hebdomadaires.
        output_path: Chemin de sortie (.html ou .pdf).
        church_name: Nom de la congrégation à injecter.
        assignments_path: Chemin optionnel vers le fichier JSON des affectations.
    """
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
        print(f"  [HTML intermédiaire] -> {html_path}")
        html_to_pdf(html, str(out))
        print(f"  [OK] -> {out}  ({len(weeks)} semaine(s), {len(pages)} page(s))")
    else:
        out.write_text(html, encoding="utf-8")
        print(f"  [OK] -> {out}  ({len(weeks)} semaine(s), {len(pages)} page(s))")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(
            "Usage: python render.py <data.json> <output.html|output.pdf> "
            "[church_name] [assignments.json]"
        )
        sys.exit(1)

    render(
        json_path=sys.argv[1],
        output_path=sys.argv[2],
        church_name=sys.argv[3] if len(sys.argv) > 3 else "[ANARAN'NY FIANGONANA]",
        assignments_path=sys.argv[4] if len(sys.argv) > 4 else None,
    )