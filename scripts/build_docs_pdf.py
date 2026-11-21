"""Render docs/<name>.html to docs/<name>.pdf with Playwright (A4, page numbers).

    python scripts/build_docs_pdf.py day-01 [--url http://127.0.0.1:8501]
Needs the Control Room running (it serves /docs) and `playwright install chromium`.
"""
import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--url", default="http://127.0.0.1:8501")
    a = ap.parse_args()
    out = ROOT / "docs" / f"{a.name}.pdf"
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.goto(f"{a.url}/docs/{a.name}.html")
        pg.wait_for_timeout(800)
        title = pg.title()
        pg.emulate_media(media="print")
        pg.pdf(path=str(out), format="A4", print_background=True,
               margin={"top": "16mm", "bottom": "18mm", "left": "15mm", "right": "15mm"},
               display_header_footer=True, header_template="<div></div>",
               footer_template=('<div style="font-size:8px;color:#7d7699;width:100%;padding:0 15mm;'
                                'display:flex;justify-content:space-between;font-family:Inter,sans-serif">'
                                f'<span>{title}</span><span><span class="pageNumber"></span> / '
                                '<span class="totalPages"></span></span></div>'))
        b.close()
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
