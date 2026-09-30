"""Derma One letterhead for every Derma Chart print: logo on top, company block at the foot.

wkhtmltopdf lifts `#footer-html` into its per-page footer and reads page margins from
`.print-format`; the browser print dialog uses the `@media print` table, whose empty tfoot
repeats on each sheet to reserve the fixed footer's room.
"""

import base64
import re
from pathlib import Path

from frappe.utils import cstr

IMAGES = Path(__file__).parent.parent / "public" / "images"
LOGO_FILE = "derma-one-logo.png"
# A doctor's own logo replaces the clinic's on their visits, matched on the practitioner's name.
# ponytail: name match; move to a Healthcare Practitioner field once more doctors bring a logo.
PRACTITIONER_LOGO_FILES = {"sadiq abdulla": "dr-sadiq-abdulla-logo.png"}


def _data_uri(filename: str) -> str:
	return "data:image/png;base64," + base64.b64encode((IMAGES / filename).read_bytes()).decode()


# Inlined so wkhtmltopdf never fetches it back from the site, which fails outside a web request.
# Served through a Jinja global: a data URI stored in a template is turned into a private File on save.
LOGO_SRC = _data_uri(LOGO_FILE)
PRACTITIONER_LOGO_SRCS = {filename: _data_uri(filename) for filename in PRACTITIONER_LOGO_FILES.values()}


def get_logo_file(practitioner_name: str | None) -> str:
	"""The practitioner's own logo file, else the clinic's."""
	name = " ".join(re.sub(r"[^a-z]+", " ", cstr(practitioner_name).lower()).split())
	return next((filename for key, filename in PRACTITIONER_LOGO_FILES.items() if key in name), LOGO_FILE)


def get_logo_url(practitioner_name: str | None) -> str:
	return f"/assets/do_derma/images/{get_logo_file(practitioner_name)}"


def derma_letterhead_logo(practitioner=None) -> str:
	"""Jinja global: the letterhead logo for this practitioner as a data URI."""
	filename = get_logo_file(practitioner and practitioner.get("practitioner_name"))
	return PRACTITIONER_LOGO_SRCS.get(filename, LOGO_SRC)


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
/* Frappe only reads the plain selector above (the PDF page margin); this keeps it off the page itself. */
div.print-format { margin-bottom: 0; }
table.derma-letterhead, .derma-letterhead > tbody, .derma-letterhead > tbody > tr, .derma-letterhead > tbody > tr > td { display: block; }
.derma-letterhead > tfoot { display: none; }
@media screen { table.derma-letterhead { margin-bottom: 36px; } }
/* Frappe keeps every div in a table cell on one page; the letter body must flow across pages. */
.derma-letterhead td div { page-break-inside: auto !important; }
.derma-letterhead td div.derma-signature { page-break-inside: avoid !important; }
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

# Every logo fits the Derma One logo's box (290 x 66 px, centred) so the header never moves.
# ponytail: move the logo into a #header-html block if multi-page letters need it on every sheet.
HEADER = '<div style="text-align:center;margin:0 0 20px;"><img src="{{ derma_letterhead_logo(practitioner) }}" alt="Logo" style="max-width:290px;max-height:66px;width:auto;height:auto;"></div>'

# The clinician's name and title from their record, with room to sign above.
SIGNATURE = """{% if practitioner %}
  <div class="derma-signature" style="margin-top:25mm;margin-bottom:-5mm;font-size:12px;">
    <div style="border-top:1px solid #333;width:240px;padding-top:6px;">{{ practitioner.practitioner_name }}
      {%- set title = practitioner.custom_specialty or practitioner.designation %}
      {%- if title %}<br><span style="color:#666;">{{ title }}</span>{% endif %}</div>
  </div>
{% endif %}"""

OPEN = STYLE + '<table class="derma-letterhead"><tfoot class="hidden-pdf"><tr><td><div style="height:42mm;"></div></td></tr></tfoot><tbody><tr><td>' + HEADER
CLOSE = (
	"</td></tr></tbody></table>"
	f'<div id="footer-html" class="visible-pdf derma-letterhead-foot">{FOOTER}</div>'
)

