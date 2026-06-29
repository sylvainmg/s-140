# S-140 Backend

> FastAPI backend that retrieves the weekly S-140 congregation program from jw.org, parses and normalizes it, and renders it as a formatted A4 PDF with optional congregation assignment support.

## Overview

This project is the data-provider backend for a larger application whose mobile companion app manages congregation members and assignments. The backend's sole responsibility is to:

1. **Fetch** the official monthly S-140 program from jw.org (Malagasy edition).
2. **Parse** the HTML into a clean, structured format.
3. **Render** the program as a print-ready A4 PDF, optionally injecting congregation assignments.

It does **not** manage members, assignments, or any congregation-side data — those are handled by the mobile application.

## Features

- **Automatic scraping** of the official jw.org monthly program index and weekly program pages.
- **Data normalization** — durations, titles, and section headers are cleaned and standardized.
- **Two-step API** — parse the program first, then render it with or without assignments.
- **PDF generation** with three fallback backends: Playwright/Chromium (recommended), wkhtmltopdf, and xhtml2pdf.
- **Assignment injection** — optionally merge speaker/role assignments into the rendered output.
- **Docker support** — one-command deployment.
- **CLI** — standalone rendering via `render.py` for offline or batch use.

## Architecture

```
┌──────────────┐      POST /parse       ┌───────────────┐
│  Mobile App  │ ──────────────────────►│               │
│  (client)    │ ◄──────────────────────│  FastAPI      │
│              │      POST /render      │  Backend      │
└──────────────┘                        │               │
                                        │  ┌──────────┐ │
                                        │  │  parse   │ │
                                        │  │  module  │ │
                                        │  └──────────┘ │
                                        │  ┌──────────┐ │
                                        │  │  render  │ │
                                        │  │  module  │ │
                                        │  └──────────┘ │
                                        └───────────────┘
                                               │
                                               ▼
                                        ┌──────────────┐
                                        │   jw.org     │
                                        │   (source)   │
                                        └──────────────┘
```

### Request flow

1. **Parse** — The client sends a monthly program index URL from jw.org to `POST /parse`.
    - The backend resolves any redirect URLs.
    - It extracts the weekly sub-page URLs from the index.
    - Each weekly page is fetched and parsed into structured data (section headers, songs, parts, durations, Bible reading).
    - The structured program and a month label are returned.

2. **Render** — The client sends the parsed program (plus optional assignments) to `POST /render`.
    - Assignments are injected into the program data if provided.
    - A Jinja2 HTML template renders the two-weeks-per-page A4 layout.
    - The HTML is converted to PDF via the best-available backend (Playwright → wkhtmltopdf → xhtml2pdf).
    - The PDF is returned as a downloadable attachment.

## Project structure

```
.
├── api.py              # FastAPI application — /health, /parse, /render endpoints
├── parse.py            # Web scraping, HTML parsing, and data normalization
├── render.py           # Jinja2 rendering, assignment injection, PDF generation (CLI + library)
├── template.html.j2    # Jinja2 HTML template (A4, 2 weeks per page)
├── requirements.txt    # Python dependencies
├── Dockerfile          # Production container image
└── .gitignore
```

### Module responsibilities

| File               | Responsibility                                                                             |
| ------------------ | ------------------------------------------------------------------------------------------ |
| `api.py`           | FastAPI app, request/response schemas, endpoint logic                                      |
| `parse.py`         | URL resolution, HTML scraping, section detection, title/duration normalization             |
| `render.py`        | Data loading, assignment injection, Jinja2 rendering, HTML→PDF conversion, CLI entry point |
| `template.html.j2` | HTML layout with CSS for print-ready A4 output                                             |

## Technology stack

| Category      | Tools                                                                                                                            |
| ------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| Framework     | [FastAPI](https://fastapi.tiangolo.com/) 0.138, [Pydantic](https://docs.pydantic.dev/) 2.13                                      |
| Server        | [uvicorn](https://www.uvicorn.org/) 0.49                                                                                         |
| Scraping      | [curl_cffi](https://github.com/lexiforest/curl_cffi) 0.15, [BeautifulSoup4](https://www.crummy.com/software/BeautifulSoup/) 4.15 |
| Templating    | [Jinja2](https://jinja.palletsprojects.com/) 3.1                                                                                 |
| PDF rendering | [Playwright](https://playwright.dev/) (recommended), wkhtmltopdf, xhtml2pdf (fallbacks)                                          |
| Container     | Docker (Python 3.11 slim base)                                                                                                   |

## Installation

### Prerequisites

- Python 3.11+
- [Playwright Chromium browser](https://playwright.dev/python/docs/intro) (recommended PDF backend)

### From source

```bash
# Clone the repository
git clone https://github.com/<org>/<repo>.git
cd <repo>

# Create and activate a virtual environment
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# Install Python dependencies
pip install -r requirements.txt

# Install Playwright's Chromium browser
playwright install chromium
```

### With Docker

```bash
docker build -t s140-backend .
```

The Dockerfile installs system build dependencies, Python packages, and Playwright/Chromium automatically.

## Environment variables

This project does **not** require any environment variables. All configuration is hard-coded (e.g., the jw.org base URL) or passed through request payloads.

## Running the project

### Development server

```bash
uvicorn api:app --reload --host 0.0.0.0 --port 8000
```

The API will be available at `http://localhost:8000`. The interactive docs are at `http://localhost:8000/docs`.

### Docker

```bash
docker run --rm -p 8000:8000 s140-backend
```

### CLI (standalone rendering)

```bash
# Render from a JSON file to HTML
python render.py data/sample_month.json output/programme.html "Fiangonana Ankadifotsy"

# Render to PDF (with optional assignments file)
python render.py data/sample_month.json output/programme.pdf "Fiangonana Ankadifotsy" assignments.json
```

## API overview

### `HEAD /health`

Health check endpoint.

**Response**

```json
{ "status": "ok" }
```

---

### `POST /parse`

Scrapes a monthly program index from jw.org and returns structured weekly data.

**Request body** (`ParseRequest`)

| Field | Type  | Description                                      |
| ----- | ----- | ------------------------------------------------ |
| `url` | `str` | URL of the monthly S-140 program index on jw.org |

**Response** (200)

```json
{
  "program": {
    "6-12 Jolay": {
      "bible_reading": "Gen. 1:1-5",
      "full_ordered_program": [
        { "type": "song",    "title": "Hira 1",   "duration": null },
        { "type": "section", "title": "🟢 HARENA AVY AO AMIN'NY TENIN'ANDRIAMANITRA", "duration": null },
        { "type": "part",    "title": "Fianarana Baiboly", "duration": 30, "assignee": "" },
        ...
      ]
    }
  },
  "month_label": "Jolay-Aogositra 2026",
  "errors": []
}
```

**Error responses**

| Status | Meaning                                   |
| ------ | ----------------------------------------- |
| 404    | No weekly programs found at the given URL |
| 502    | Failed to fetch or resolve the index URL  |
| 500    | Parsing failed for all weeks              |

---

### `POST /render`

Renders a parsed program (with optional assignments) as a PDF.

**Request body** (`RenderRequest`)

| Field         | Type             | Description                                                      |
| ------------- | ---------------- | ---------------------------------------------------------------- |
| `program`     | `dict`           | The `program` object from `/parse` (week keys → structured data) |
| `assignments` | `dict`, optional | Congregation assignment data (see below)                         |
| `church_name` | `str`            | Name of the congregation (default: `"[ANARAN'NY FIANGONANA]"`)   |
| `month_label` | `str`            | Month/year label used in the PDF filename                        |

**Assignments structure (optional)**

```json
{
  "6-12 Jolay": {
    "mpitari_draharaha": "Rakoto",
    "mpanome_torohevitra": "Rabe",
    "vavaka_fampidirana": "Be",
    "vavaka_famaranana": "Soa",
    "parts": {
      "Fianarana Baiboly": {
        "etudiant": "Hery",
        "complement": "Soa"
      }
    }
}
```

**Response**

- **Status**: `200 OK`
- **Content-Type**: `application/pdf`
- **Content-Disposition**: `attachment; filename=S-140-<month_label>.pdf`

**Error responses**

| Status | Meaning                                  |
| ------ | ---------------------------------------- |
| 400    | Empty program                            |
| 500    | HTML rendering or PDF generation failure |

## Development workflow

1. **Make changes** to `api.py`, `parse.py`, `render.py`, or `template.html.j2`.
2. **Run the dev server**:
    ```bash
    uvicorn api:app --reload
    ```
3. **Test endpoints** using `http://localhost:8000/docs` (Swagger UI) or `http://localhost:8000/redoc` (ReDoc).
4. **Run the CLI** for offline rendering tests:
    ```bash
    python render.py data/sample_month.json output/test.pdf
    ```
5. **Build and test the Docker image**:
    ```bash
    docker build -t s140-backend .
    docker run --rm -p 8000:8000 s140-backend
    ```

## Contributing

Contributions are welcome! Please ensure:

- Code follows Python style conventions (PEP 8).
- New scraping logic accounts for potential jw.org HTML structure changes.
- PDF output is verified across all three backends (Playwright, wkhtmltopdf, xhtml2pdf) if template CSS is modified.
- No congregation-specific data is introduced — this backend must remain agnostic of member/assignment management.

## License

This project is open source. See the `LICENSE` file for details.

## Open-source notice

This project fetches data from jw.org, a third-party website. Please ensure your use of this backend complies with jw.org's [Terms of Use](https://www.jw.org/en/terms-of-use/) and applicable data protection regulations in your jurisdiction. The project does not store or distribute any copyrighted content — it retrieves and restructures publicly available program schedules for personal or organizational use.

## Repository

- **Source**: [github.com/<org>/<repo>](https://github.com/<org>/<repo>)
- **Issues**: [github.com/<org>/<repo>/issues](https://github.com/<org>/<repo>/issues)
- **API docs**: Available at `/docs` when the server is running (Swagger UI).
