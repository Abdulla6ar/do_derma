"""Derma One letterhead for every Derma Chart print: logo on top, company block at the foot.

wkhtmltopdf lifts `#footer-html` into its per-page footer and reads page margins from
`.print-format`; the browser print dialog uses the `@media print` table, whose empty tfoot
repeats on each sheet to reserve the fixed footer's room.
"""

import base64
from pathlib import Path

# Inlined so wkhtmltopdf never fetches it back from the site, which fails outside a web request.
LOGO_SRC = "data:image/png;base64," + base64.b64encode(
	(Path(__file__).parent.parent / "public" / "images" / "derma-one-logo.png").read_bytes()
).decode()

FOOTER_LINES = (
	"P.O. Box 31008, Floors 6 &amp; 7, Bldg 71, Road 3201, Block 332, Kingdom of Bahrain",
	"Tel: +973 1724 0042 &nbsp; Email: info@dermaonecentre.com &nbsp; CR No. 100506-1",
	"www.dermaonecentre.com",
)

FOOTER = (
	'<div style="text-align:center;color:#222;font-family:Arial,Helvetica,sans-serif;font-size:10px;line-height:1.5;padding-bottom:12mm;">'
	'<div style="font-family:Georgia,\'Times New Roman\',serif;font-size:11px;letter-spacing:4px;margin-bottom:5px;">'
	"DERMA ONE MEDICAL CENTRE W.L.L.</div>" + "".join(f"<div>{line}</div>" for line in FOOTER_LINES) + "</div>"
)

STYLE = """<style>
.print-format { margin-bottom: 42mm; }
table.derma-letterhead, .derma-letterhead > tbody, .derma-letterhead > tbody > tr, .derma-letterhead > tbody > tr > td { display: block; }
.derma-letterhead > tfoot { display: none; }
table.derma-letterhead { margin-bottom: 36px; }
@media print {
  @page { size: A4; margin: 10mm 12mm 0; }
  /* wkhtmltopdf also prints with print media, but its WebKit predates @supports: browsers only. */
  @supports (display: grid) {
    table.derma-letterhead { display: table; width: 100%; border-collapse: collapse; }
    .derma-letterhead > tbody { display: table-row-group; }
    .derma-letterhead > tbody > tr { display: table-row; }
    .derma-letterhead > tbody > tr > td { display: table-cell; padding: 0; }
    .derma-letterhead > tfoot { display: table-footer-group; }
    .derma-letterhead-foot { position: fixed; left: 0; right: 0; bottom: 0; margin: 0; }
  }
}
</style>"""

# ponytail: move the logo into a #header-html block if multi-page letters need it on every sheet.
HEADER = f'<div style="text-align:center;margin:0 0 40px;"><img src="{LOGO_SRC}" alt="Derma One" style="width:290px;height:auto;"></div>'

OPEN = STYLE + '<table class="derma-letterhead"><tfoot class="hidden-pdf"><tr><td><div style="height:42mm;"></div></td></tr></tfoot><tbody><tr><td>' + HEADER
CLOSE = (
	"</td></tr></tbody></table>"
	f'<div id="footer-html" class="visible-pdf derma-letterhead-foot">{FOOTER}</div>'
)

