from __future__ import annotations

from typing import Any

import frappe
from frappe import _

from do_derma.assessment import HP, MODE_FIELD, SOAP, STRUCTURED
from do_derma.documents import current_diagnosis
from do_derma.overview.text import clean, clip, first_sentence, join_unique, listed, unique
from do_derma.overview.treatments import WATCH_STATUSES, get_area_label, is_treatment
from do_derma.schema import PATIENT_ADVICE_AR_FIELD, PATIENT_ADVICE_FIELD

ADVICE_FIELDS = (PATIENT_ADVICE_FIELD, PATIENT_ADVICE_AR_FIELD)


def get_summary(visit: frappe._dict, visit_marks: list[frappe._dict]) -> list[dict[str, Any]]:
	coded = frappe.get_all(
		"Patient Encounter Diagnosis",
		filters={
			"parent": visit.name,
			"parenttype": "Patient Encounter",
			"parentfield": ["in", ["diagnosis", "custom_differential_diagnosis"]],
		},
		fields=["parentfield", "diagnosis"],
		order_by="idx asc",
		limit=0,
	)
	mode = visit.get(MODE_FIELD)
	items = [
		_summary_item(_("Complaint"), _complaint(visit, mode)),
		_diagnosis_item(visit, coded, visit_marks),
		_summary_item(_("Findings"), _findings_text(visit_marks)),
		_summary_item(
			_("Examination"),
			_first_by_mode(
				mode,
				{
					HP: lambda: clean(visit.get("custom_derma_hp_examination")),
					SOAP: lambda: clean(visit.get("custom_derma_soap_objective")),
					STRUCTURED: lambda: clean(visit.get("custom_physical_examination")),
				},
				(HP, SOAP, STRUCTURED),
			),
		),
		_plan_item(visit, mode),
	]
	return [item for item in items if item]


def _complaint(visit: frappe._dict, mode: str | None) -> str:
	return _first_by_mode(
		mode,
		{
			HP: lambda: clean(visit.get("custom_derma_hp_chief_complaint")),
			STRUCTURED: lambda: _structured_complaint(visit),
			SOAP: lambda: first_sentence(visit.get("custom_derma_soap_subjective")),
		},
		(HP, STRUCTURED, SOAP),
	)


def _structured_complaint(visit: frappe._dict) -> str:
	complaints = frappe.get_all(
		"Patient Encounter Symptom",
		filters={"parent": visit.name, "parenttype": "Patient Encounter", "parentfield": "symptoms"},
		pluck="complaint",
		order_by="idx asc",
		limit=0,
	)
	notes = clean(visit.get("custom_symptoms_notes"))
	text = " — ".join(value for value in [join_unique(complaints), notes] if value)
	return clip(text) if text else clean(visit.get("custom_chief_complaints"))


def _diagnosis_item(
	visit: frappe._dict, coded: list[frappe._dict], visit_marks: list[frappe._dict]
) -> dict[str, Any] | None:
	visit.diagnosis = [row for row in coded if row.parentfield == "diagnosis"]
	value = clean(current_diagnosis(visit), 200)
	if not value:
		value = clean(join_unique(mark.diagnosis for mark in visit_marks), 200)
	note = clean(visit.get("custom_diagnosis_note"))
	if not value:
		value, note = note, ""
	if not value:
		return None
	differentials = join_unique(row.diagnosis for row in coded if row.parentfield == "custom_differential_diagnosis")
	secondary = " · ".join(
		part
		for part in [_("Ddx: {0}").format(differentials) if differentials else "", note if note != value else ""]
		if part
	)
	return {"label": _("Diagnosis"), "value": value, "secondary": clip(secondary) or None}


def _plan_item(visit: frappe._dict, mode: str | None) -> dict[str, Any] | None:
	plan = _first_by_mode(
		mode,
		{
			SOAP: lambda: clean(visit.get("custom_derma_soap_plan")),
			HP: lambda: clean(visit.get("custom_derma_hp_plan")),
		},
		(SOAP, HP),
	)
	return _summary_item(_("Plan"), plan or get_advice(visit))


def _findings_text(visit_marks: list[frappe._dict]) -> str:
	"""What was charted and is not treated or under watch - those have sections of their own."""
	groups: dict[str, list[frappe._dict]] = {}
	for mark in visit_marks:
		if is_treatment(mark) or mark.status in WATCH_STATUSES:
			continue
		groups.setdefault(mark.diagnosis or mark.category or _("Finding"), []).append(mark)
	parts = []
	for label, marks in groups.items():
		qualifiers = join_unique(
			[*(mark.severity for mark in marks), *(mark.status for mark in marks if mark.status != "Active")]
		)
		text = f"{label} ({qualifiers})" if qualifiers else label
		areas = unique(get_area_label(mark) for mark in marks)
		if areas:
			text += " — " + listed(areas, 4)
		elif len(marks) > 1:
			text += f" ×{len(marks)}"
		parts.append(text)
	return clip("; ".join(parts))


def _summary_item(label: str, value: str) -> dict[str, Any] | None:
	return {"label": label, "value": value} if value else None


def _first_by_mode(mode: str | None, sources: dict[str, Any], default_order: tuple[str, ...]) -> str:
	"""The documented mode speaks first; the others only fill in when it is empty."""
	order = [mode, *[candidate for candidate in default_order if candidate != mode]] if mode in sources else list(default_order)
	for candidate in order:
		value = sources[candidate]()
		if value:
			return value
	return ""


def get_advice(visit: frappe._dict) -> str:
	return next((clean(visit.get(fieldname)) for fieldname in ADVICE_FIELDS if clean(visit.get(fieldname))), "")
