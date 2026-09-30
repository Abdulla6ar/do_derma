"""Printable assessment note: the format on screen, on the clinic's letterhead.

One standard Print Format on Patient Encounter. The body is the same block the
existing print injection uses (`derma_assessment_html`), so it always prints the
format the encounter is documented in - what the doctor sees is what prints.
"""

import re

import frappe
from frappe.utils import cint, cstr
from markupsafe import Markup, escape

from do_derma import assessment
from do_derma.assessment import HP, SOAP, STRUCTURED
from do_derma.printing import letterhead
from do_derma.printing.render import derma_print_text

ARABIC = re.compile(r"[\u0600-\u06FF]")
ADVICE_FIELDS = {"English": "custom_derma_patient_advice", "Arabic": "custom_derma_patient_advice_ar"}


def advice_languages(doc, mode: str | None = None) -> list[str]:
	"""Which advice versions print: the chosen language, Both, or Auto = the report's language."""
	choice = cstr(doc.get("custom_derma_patient_advice_language")) or "Auto"
	if choice == "Both":
		return ["English", "Arabic"]
	if choice in ADVICE_FIELDS:
		return [choice]
	mode = mode or assessment.get_assessment_mode(doc)
	fields = assessment.MODE_FIELDS.get(mode) or [row["fieldname"] for row in assessment.get_layout(mode) if row.get("is_value_field")]
	report = " ".join(cstr(doc.get(f)) for f in fields if isinstance(doc.get(f), str))
	return ["Arabic"] if ARABIC.search(report) else ["English"]


def derma_patient_advice_html(doc, mode: str | None = None) -> Markup:
	"""Jinja global. The Patient Advice block, or empty unless the doctor opted in."""
	try:
		encounter = doc if hasattr(doc, "get") else frappe.get_doc("Patient Encounter", doc)
		if not cint(encounter.get("custom_derma_print_patient_advice")):
			return Markup("")
		blocks = []
		for language in advice_languages(encounter, mode):
			text = cstr(encounter.get(ADVICE_FIELDS[language])).strip()
			if not text:  # the chosen version is blank - fall back to the other one rather than print nothing
				other = "Arabic" if language == "English" else "English"
				text = cstr(encounter.get(ADVICE_FIELDS[other])).strip()
				language = other
			if text and (language, text) not in blocks:
				blocks.append((language, text))
		if not blocks:
			return Markup("")
		html = ['<div class="derma-patient-advice" style="font-size:12px;margin-top:18px;padding-top:12px;border-top:1px solid #e5e7eb;">']
		html.append('<h2 style="font-size:14px;color:#1a3a5c;margin:0 0 6px;">Patient Advice</h2>')
		for language, text in blocks:
			rtl = ' dir="rtl"' if language == "Arabic" else ""
			html.append(f'<div{rtl} style="white-space:pre-wrap;margin-top:6px;">{escape(derma_print_text(text))}</div>')
		html.append("</div>")
		return Markup("".join(html))
	except Exception:
		frappe.log_error(title="Derma patient advice print failed", message=frappe.get_traceback())
		return Markup("")

def derma_diagnosis_html(doc) -> Markup:
	"""Jinja global. The diagnosis and ICD-10 code the voice scribe drafted, or empty."""
	encounter = doc if hasattr(doc, "get") else frappe.get_doc("Patient Encounter", doc)
	diagnosis = cstr(encounter.get("custom_derma_ai_diagnosis")).strip()
	icd10 = cstr(encounter.get("custom_derma_icd10")).strip()
	if not (diagnosis or icd10):
		return Markup("")
	parts = [f"<b>Diagnosis:</b> {escape(diagnosis)}" if diagnosis else "", f"<b>ICD-10:</b> {escape(icd10)}" if icd10 else ""]
	return Markup(
		'<div class="derma-diagnosis" style="font-size:12px;margin:0 0 14px;padding:8px 12px;border-left:3px solid #1a3a5c;background:#f5f7fa;">'
		+ " &nbsp;·&nbsp; ".join(p for p in parts if p)
		+ "</div>"
	)


PRINT_FORMAT = "Derma Assessment Note"  # prints whichever format the visit is documented in
# One print per report type - never mixed. The chart's Print button picks by the open tab.
PRINT_FORMATS = {
	HP: "Derma Assessment Note (H&P)",
	SOAP: "Derma Assessment Note (SOAP)",
	STRUCTURED: "Derma Assessment Note (Structured)",
}
TEMPLATE_MARKER = "<!-- derma-assessment-note v"
TEMPLATE_VERSION = 20

TEMPLATE = f"""{TEMPLATE_MARKER}{TEMPLATE_VERSION} -->
""" + """
{%- set patient = frappe.get_doc("Patient", doc.patient) if doc.patient else None -%}
{%- set practitioner = frappe.get_doc("Healthcare Practitioner", doc.practitioner) if doc.practitioner else None -%}
""" + letterhead.OPEN + """
<div style="font-family:Arial,Helvetica,sans-serif;max-width:720px;margin:0 auto;color:#1a1a1a;line-height:1.4;padding:0 24px;">
  <table style="width:100%;font-size:12px;margin-bottom:18px;border:1px solid #e5e7eb;"><tr>
    <td style="padding:6px 10px;white-space:nowrap;"><b>Patient:</b> {{ (patient and patient.patient_name) or '' }}</td>
    <td style="padding:6px 10px;white-space:nowrap;"><b>MRN:</b> {{ doc.patient }}</td>
    <td style="padding:6px 10px;white-space:nowrap;"><b>Visit:</b> {{ frappe.utils.formatdate(doc.encounter_date) }}</td>
    <td style="padding:6px 10px;white-space:nowrap;"><b>Clinician:</b> {{ (practitioner and practitioner.practitioner_name) or '' }}</td>
  </tr></table>
  {{ derma_diagnosis_html(doc) }}
  <div style="font-size:12px;">{{ derma_assessment_html(doc) }}</div>
  {{ derma_patient_advice_html(doc) }}
""" + letterhead.SIGNATURE + """
</div>
""" + letterhead.CLOSE


def template_for(mode: str | None) -> str:
	"""The letter template pinned to one report type, or the documented-format one."""
	if not mode:
		return TEMPLATE
	return TEMPLATE.replace("derma_assessment_html(doc)", f'derma_assessment_html(doc, "{mode}")').replace(
		"derma_patient_advice_html(doc)", f'derma_patient_advice_html(doc, "{mode}")'
	)


def ensure_assessment_print_format() -> str:
	"""Seed the print formats once. A clinic's own edit (marker removed) is kept."""
	done = []
	for mode, name in ((None, PRINT_FORMAT), *PRINT_FORMATS.items()):
		outcome = _ensure_format(name, template_for(mode))
		if outcome:
			done.append(outcome)
	return ", ".join(done)


def _ensure_format(name: str, html: str) -> str:
	existing = frappe.db.get_value("Print Format", name, ["name", "html"], as_dict=True)
	if existing:
		current = existing.html or ""
		if TEMPLATE_MARKER in current and f"{TEMPLATE_MARKER}{TEMPLATE_VERSION} -->" not in current:
			frappe.db.set_value("Print Format", existing.name, "html", html)
			return f"{name} (upgraded)"
		return ""
	frappe.get_doc(
		{
			"doctype": "Print Format",
			"name": name,
			"doc_type": "Patient Encounter",
			"module": "Do Derma",
			"print_format_type": "Jinja",
			"custom_format": 1,
			"standard": "No",
			"html": html,
		}
	).insert(ignore_permissions=True)
	return name
