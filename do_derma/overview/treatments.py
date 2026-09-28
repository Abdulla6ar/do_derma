from __future__ import annotations

import re
from typing import Any

import frappe
from frappe import _
from frappe.utils import add_days, cint, cstr, flt, getdate, nowdate

from do_derma import api
from do_derma.overview.text import clean, clip, existing_fields, join_unique, listed, unique

ARCHIVED = "Archived"
# In order of urgency; each gets its own callout. Due dates come from STATUS_DUE_DAYS.
WATCH_STATUSES = ("Biopsied", "Worse", "Excised", "Monitoring")
SERIES_WINDOW_DAYS = 365
MAX_TAGS = 8
UNIT_LABELS = {"Units": "U", "J/cm2": "J/cm²", "Other": ""}
HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}){1,2}$")
# What makes a Clinical Procedure Template a derma one. Unlike api._is_derma_template, the marker
# behavior is not a sign: it is a Select, so every new template gets its first option.
DERMA_TEMPLATE_FLAGS = ("custom_derma_category", "custom_derma_required_fields")
MARK_FIELDS = [
	"name",
	"encounter",
	"encounter.encounter_date as encounter_date",
	"clinical_procedure",
	"clinical_procedure.docstatus as procedure_docstatus",
	"category",
	"procedure_template",
	"procedure_template.template as template_label",
	"body_template_part.part_name as part_name",
	"body_view",
	"body_region",
	"region_label",
	"side",
	"x_percent",
	"y_percent",
	"marker_behavior",
	"marker_color",
	"product_name",
	"dose",
	"dose_unit",
	"plane",
	"technique",
	"device",
	"settings",
	"passes",
	"lot_no",
	"expiry_date",
	"lesion_id",
	"diagnosis",
	"severity",
	"status",
	"annotation",
	"sequence",
	"creation",
]
# What makes a mark a treatment rather than a finding when it is not on a procedure.
TREATMENT_VALUE_FIELDS = ("product_name", "device", "lot_no", "settings")
# Variables a mark or procedure keeps in columns of its own. A variable row under the same
# fieldname would only repeat what the detail line already says.
SHOWN_VARIABLES = {
	"product_item",
	"product_name",
	"product",
	"dose",
	"dose_unit",
	"units",
	"lot_no",
	"lot",
	"expiry_date",
	"device",
	"settings",
	"passes",
	"plane",
	"technique",
	"diagnosis",
	"severity",
	"status",
	"lesion_id",
	"body_region",
}


def load_marks(patient: str, encounter: str, exclude_encounter: str | None) -> list[frappe._dict]:
	"""The visit's live marks plus every live mark of the last 12 months, in one read.

	The window feeds the series totals and the lesions still open from earlier visits. A mark
	whose procedure was cancelled never happened, so it is dropped here for every consumer.
	"""
	filters: list[list[Any]] = [
		["patient", "=", patient],
		["status", "!=", ARCHIVED],
		["encounter", "is", "set"],
		["encounter.docstatus", "<", 2],
	]
	if exclude_encounter:
		filters.append(["encounter", "!=", exclude_encounter])
	rows = frappe.get_all(
		"Derma Chart Mark",
		filters=filters,
		or_filters=[
			["encounter", "=", encounter],
			["encounter.encounter_date", ">=", add_days(nowdate(), -SERIES_WINDOW_DAYS)],
		],
		fields=MARK_FIELDS,
		# Newest first so a very long history loses its oldest marks, never the visit's own.
		order_by="creation desc",
		limit=1000,
	)
	return [row for row in reversed(rows) if cint(row.procedure_docstatus) != 2]


def load_procedures(patient: str, encounter: str, visit_marks: list[frappe._dict]) -> dict[str, frappe._dict]:
	"""The visit's derma procedures by name: a derma template, or a mark of this visit on it.

	A dental or GP procedure booked on the same encounter has neither, and stays off the card.
	"""
	encounter_field = api._get_clinical_procedure_encounter_field()
	mark_procedures = sorted({mark.clinical_procedure for mark in visit_marks if mark.clinical_procedure})
	or_filters: list[list[Any]] = []
	if encounter_field:
		or_filters.append([encounter_field, "=", encounter])
	if mark_procedures:
		or_filters.append(["name", "in", mark_procedures])
	if not or_filters:
		return {}

	flags = [flag for flag in DERMA_TEMPLATE_FLAGS if api._has_field("Clinical Procedure Template", flag)]
	fields = [
		"name",
		"procedure_template",
		"procedure_template.template as template_label",
		*[f"procedure_template.{flag} as {flag}" for flag in flags],
		*existing_fields("Clinical Procedure", ["title", "status", "start_date", "creation"]),
	]
	if api._has_field("Clinical Procedure Template", "custom_derma_marker_color"):
		fields.append("procedure_template.custom_derma_marker_color as template_color")
	# get_list: other apps' rules for Clinical Procedure (e.g. do_dental's sealed templates) apply.
	if not frappe.has_permission("Clinical Procedure", "read"):
		return {}
	filters = [["patient", "=", patient], ["docstatus", "<", 2]]
	if api._has_field("Clinical Procedure", "custom_entered_in_error"):
		filters.append(["custom_entered_in_error", "=", 0])
	rows = frappe.get_list(
		"Clinical Procedure",
		filters=filters,
		or_filters=or_filters,
		fields=fields,
		order_by="creation asc",
		limit=100,
	)
	return {
		row.name: row
		for row in rows
		if row.get("status") != "Cancelled" and (row.name in mark_procedures or any(row.get(flag) for flag in flags))
	}


def _load_mark_variables(mark_names: list[str]) -> dict[str, list[frappe._dict]]:
	if not mark_names or not api._has_field("Derma Chart Mark", "area_variables"):
		return {}
	rows = frappe.get_all(
		"Derma Mark Variable",
		filters={"parent": ["in", mark_names], "parenttype": "Derma Chart Mark"},
		fields=["parent", "fieldname", "label", "value", "source"],
		order_by="parent asc, idx asc",
		limit=0,
	)
	grouped: dict[str, list[frappe._dict]] = {}
	for row in rows:
		grouped.setdefault(row.parent, []).append(row)
	return grouped


def _load_procedure_variables(procedure_names: list[str]) -> dict[tuple[str, str], list[frappe._dict]]:
	if not procedure_names or not api._has_field("Clinical Procedure", api.PROCEDURE_VARIABLES_FIELD):
		return {}
	rows = frappe.get_all(
		"Derma Procedure Variable",
		filters={"parent": ["in", procedure_names], "parenttype": "Clinical Procedure"},
		fields=["parent", "procedure_template", "fieldname", "label", "value"],
		order_by="parent asc, idx asc",
		limit=0,
	)
	grouped: dict[tuple[str, str], list[frappe._dict]] = {}
	for row in rows:
		grouped.setdefault((row.parent, row.procedure_template), []).append(row)
	return grouped


def _load_treatment_entries(procedure_names: list[str]) -> dict[str, list[frappe._dict]]:
	"""Entries of procedures that have no mark - the only record of what such a session used."""
	if not procedure_names:
		return {}
	rows = frappe.get_all(
		"Derma Treatment Entry",
		filters={"clinical_procedure": ["in", procedure_names]},
		fields=[
			"clinical_procedure",
			"procedure_type",
			"body_view",
			"body_region",
			"region_label",
			"side",
			"product_name",
			"dose",
			"dose_unit",
			"device",
			"settings",
			"lot_no",
			"expiry_date",
			"variables_json",
		],
		order_by="creation asc",
		limit=0,
	)
	grouped: dict[str, list[frappe._dict]] = {}
	for row in rows:
		grouped.setdefault(row.clinical_procedure, []).append(row)
	return grouped


def get_treatment_groups(visit_marks: list[frappe._dict], procedures: dict[str, frappe._dict]) -> list[dict[str, Any]]:
	"""One group per procedure, and one per category for treated marks that never got one."""
	groups: dict[str, dict[str, Any]] = {}
	for mark in visit_marks:
		if not is_treatment(mark):
			continue
		key = mark.clinical_procedure or f"{mark.category}|{mark.procedure_template}"
		groups.setdefault(key, {"procedure": procedures.get(mark.clinical_procedure), "marks": []})["marks"].append(mark)
	for name, procedure in procedures.items():
		groups.setdefault(name, {"procedure": procedure, "marks": []})

	mark_variables = _load_mark_variables([mark.name for group in groups.values() for mark in group["marks"]])
	procedure_variables = _load_procedure_variables(
		[group["procedure"].name for group in groups.values() if group["procedure"]]
	)
	entries = _load_treatment_entries(
		[group["procedure"].name for group in groups.values() if group["procedure"] and not group["marks"]]
	)
	for group in groups.values():
		procedure = group["procedure"] or frappe._dict()
		marks = sorted(group["marks"], key=lambda mark: (cint(mark.sequence), cstr(mark.creation)))
		group["marks"] = marks
		group["category"] = next((mark.category for mark in marks if mark.category), None) or procedure.get(
			"custom_derma_category"
		)
		group["mark_variables"] = {mark.name: mark_variables.get(mark.name, []) for mark in marks}
		group["procedure_variables"] = procedure_variables.get((procedure.get("name"), procedure.get("procedure_template")), [])
		group["entries"] = entries.get(procedure.get("name"), [])
		group["session_date"] = procedure.get("start_date")
		# In the order they were done: the first mark placed, or the procedure raised without one.
		group["sort_key"] = min(
			[cstr(mark.creation) for mark in marks] + ([cstr(procedure.creation)] if procedure.get("creation") else [])
			or [""]
		)
	return sorted(groups.values(), key=lambda group: group["sort_key"])


def get_procedure_row(group: dict[str, Any]) -> dict[str, Any]:
	procedure = group["procedure"] or frappe._dict()
	marks = group["marks"]
	sources = marks or group["entries"]
	label = (
		procedure.get("template_label")
		or procedure.get("title")
		or next((mark.template_label for mark in marks if mark.template_label), None)
		or group["category"]
		or _("Treatment")
	)

	# Dose per area, in the order the areas were treated.
	areas: dict[str, dict[str, float]] = {}
	totals: dict[str, float] = {}
	for row in sources:
		dose, unit = _row_dose(row, group["mark_variables"].get(row.get("name"), []))
		area = get_area_label(row)
		if area:
			area_doses = areas.setdefault(area, {})
			if dose:
				area_doses[unit] = area_doses.get(unit, 0) + dose
		if dose:
			totals[unit] = totals.get(unit, 0) + dose
	tags = [
		" · ".join([area, " + ".join(get_dose_text(value, unit) for unit, value in doses.items())]) if doses else area
		for area, doses in areas.items()
	]
	if len(tags) > MAX_TAGS:
		tags = tags[: MAX_TAGS - 1] + [_("+{0} more").format(len(tags) - MAX_TAGS + 1)]

	# The total only adds something when the tags do not already say it.
	dosed_areas = sum(1 for doses in areas.values() if doses)
	passes = max([cint(row.get("passes")) for row in sources] or [0])
	detail = [
		listed(unique(row.get("product_name") for row in sources), 2),
		" + ".join(get_dose_text(value, unit) for unit, value in totals.items()) if dosed_areas != 1 else "",
		join_unique([*(row.get("plane") for row in sources), *(row.get("technique") for row in sources)], " · "),
		join_unique(row.get("device") for row in sources if row.get("device") != row.get("product_name")),
		join_unique(clean(row.get("settings"), 80) for row in sources),
		(_("1 pass") if passes == 1 else _("{0} passes").format(passes)) if passes else "",
		_variables_text(group),
		join_unique(_lot_text(row) for row in sources),
	]
	return {
		"label": label,
		"status": procedure.get("status") or None,
		"tags": tags,
		"detail": clip(" · ".join(part for part in detail if part), 300) or None,
		"color": _color(marks, procedure),
		"doctype": "Clinical Procedure" if procedure.get("name") else None,
		"name": procedure.get("name") or None,
	}


def _row_dose(row: dict[str, Any], variables: list[frappe._dict]) -> tuple[float, str]:
	"""A mark's own dose, else a "Units" variable the template asked for per area."""
	dose = flt(row.get("dose"))
	if dose > 0:
		return dose, cstr(row.get("dose_unit"))
	for variable in variables:
		if "units" in {cstr(variable.fieldname).lower(), cstr(variable.label).strip().lower()}:
			value = flt(cstr(variable.value).strip() or 0)
			if value > 0:
				return value, "Units"
	return 0, ""


def _variables_text(group: dict[str, Any]) -> str:
	"""Session parameters such as fluence and spot size: the procedure's shared values first,
	then what the marks recorded for themselves, then a mark-less procedure's treatment entry."""
	rows = [(row.fieldname, row.label, row.value) for row in group["procedure_variables"]]
	if not rows:
		rows = [
			(row.fieldname, row.label, row.value)
			for variables in group["mark_variables"].values()
			for row in variables
			if row.source == "Procedure"
		]
	if not rows:
		for entry in group["entries"]:
			values = api._parse_json(entry.get("variables_json"), {})
			if isinstance(values, dict):
				rows.extend((fieldname, None, value) for fieldname, value in values.items())
	texts = []
	for fieldname, label, value in rows:
		value = clean(value, 60)
		if not value or cstr(fieldname).lower() in SHOWN_VARIABLES:
			continue
		default = api._default_derma_variable(fieldname) or {}
		texts.append(f"{label or default.get('label') or frappe.unscrub(fieldname)}: {value}")
	return " · ".join(unique(texts))


def _lot_text(row: dict[str, Any]) -> str:
	lot = cstr(row.get("lot_no")).strip()
	if not lot:
		return ""
	if row.get("expiry_date"):
		return _("Lot {0} (exp {1})").format(lot, getdate(row.get("expiry_date")))
	return _("Lot {0}").format(lot)


def _color(marks: list[frappe._dict], procedure: frappe._dict) -> str | None:
	for value in [*(mark.marker_color for mark in marks), procedure.get("template_color")]:
		if value and HEX_COLOR.match(cstr(value).strip()):
			return cstr(value).strip()
	return None


def is_treatment(mark: frappe._dict) -> bool:
	"""On a procedure, or carrying what only a treatment records. A finding dot never is."""
	if mark.clinical_procedure:
		return True
	if mark.marker_behavior == "finding_dot":
		return False
	return flt(mark.dose) > 0 or any(cstr(mark.get(field)).strip() for field in TREATMENT_VALUE_FIELDS)


def get_area_label(row: dict[str, Any]) -> str:
	area = cstr(
		row.get("part_name") or row.get("region_label") or row.get("body_region") or row.get("body_view")
	).strip()
	side = row.get("side")
	if not area or not side or side.lower() in area.lower():
		return area
	if side in ("Left", "Right"):
		return f"{_(side)} {area}"
	if side == "Bilateral":
		return _("{0} (bilateral)").format(area)
	return area


def get_dose_text(value: float, unit: str | None) -> str:
	unit = UNIT_LABELS.get(unit, unit) if unit else ""
	number = f"{flt(value, 2):g}"
	if unit == "pass":
		return _("1 pass") if number == "1" else _("{0} passes").format(number)
	return f"{number} {unit}".strip()
