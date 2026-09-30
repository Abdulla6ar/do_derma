from __future__ import annotations

import base64
import json
import re
from typing import Any

import frappe
from do_health.api.appointment_methods import create_encounter_for_appointment
from frappe import _
from frappe.utils import cint, cstr, flt, now_datetime, nowdate
from frappe.utils.file_manager import save_file

from do_derma import assessment, previous_visits, reopen, voice
from do_derma.assessment import CHILD_INTERNAL_FIELDS
from do_derma.config.marker_size import (
	MARK_SIZE_FIELD,
	MARKER_SIZE_FIELD,
	MARKER_SIZE_MAX,
	MARKER_SIZE_MIN,
	MARKER_SIZE_STEP,
	validated_marker_size,
)
from do_derma.consumables import marks as consumable_marks
from do_derma.consumables import procedures as consumable_procedures
from do_derma.printing import letterhead
from do_derma.schema import COMPLETION_OVERRIDE_FIELD
from do_derma.settings import (
	ENFORCEMENT_WARN,
	FEATURE_TOGGLES,
	get_feature_toggles,
	get_readiness_settings,
)

MAX_PRESCRIPTION_REPEATS = 12
UNINSTALLED_APP_MESSAGE = re.compile(r"App ([a-z0-9_]+) is not installed", re.IGNORECASE)

DERMA_FINDING_FIELDS = [
	"name",
	"patient",
	"patient_name",
	"appointment",
	"encounter",
	"finding_type",
	"lesion_id",
	"body_view",
	"body_region",
	"region_label",
	"side",
	"x_percent",
	"y_percent",
	"diagnosis",
	"morphology",
	"distribution",
	"severity",
	"status",
	"size_mm",
	"color",
	"follow_up_required",
	"reviewed_this_visit",
	"photo_set",
	"annotation",
	"notes",
	"modified",
]

DERMA_TREATMENT_FIELDS = [
	"name",
	"patient",
	"patient_name",
	"appointment",
	"encounter",
	"clinical_procedure",
	"workflow",
	"procedure_type",
	"body_view",
	"body_region",
	"region_label",
	"side",
	"x_percent",
	"y_percent",
	"product_item",
	"product_name",
	"dose",
	"dose_unit",
	"device",
	"settings",
	"lot_no",
	"expiry_date",
	"billing_item",
	"consent_reference",
	"variables_json",
	"photo_set",
	"annotation",
	"notes",
	"modified",
]

DERMA_MARK_FIELDS = [
	"name",
	"patient",
	"patient_name",
	"appointment",
	"encounter",
	"clinical_procedure",
	"finding",
	"treatment_entry",
	"category",
	"procedure_template",
	"body_template",
	"body_view",
	"body_region",
	"region_label",
	"body_template_part",
	"side",
	"x_percent",
	"y_percent",
	"marker_behavior",
	"marker_color",
	MARK_SIZE_FIELD,
	"marker_label",
	"sequence",
	"product_item",
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
	"photo_set",
	"annotation",
	"annotation_json",
	"note",
	"modified",
]

DERMA_TEMPLATE_FIELDS = [
	"name",
	"template",
	"description",
	"item",
	"item_group",
	"medical_department",
	"custom_derma_category",
	"custom_derma_allowed_body_templates",
	"custom_derma_variables_json",
	"custom_derma_marker_behavior",
	"custom_derma_marker_color",
	MARKER_SIZE_FIELD,
	"custom_derma_marker_preset_json",
	"custom_derma_required_fields",
	"custom_derma_consent_required",
	"custom_derma_before_after_photo_required",
	"custom_derma_product_tracking_required",
	"custom_derma_device_settings_required",
	"custom_derma_variables_per_procedure",
	"custom_derma_print_procedure_variables",
	"custom_derma_note_template",
]

# What the config panel's detail view edits, keyed by the name it uses in the payload.
# Everything else on Clinical Procedure Template - consumables, sample collection, medical
# coding, the marker preset - stays in the desk form.
EDITOR_TEXT_FIELDS = {
	"description": "description",
	"item_group": "item_group",
	"medical_department": "medical_department",
	"category": "custom_derma_category",
	"marker_behavior": "custom_derma_marker_behavior",
	"marker_color": "custom_derma_marker_color",
	"note_template": "custom_derma_note_template",
}
EDITOR_CHECK_FIELDS = {
	"is_billable": "is_billable",
	"disabled": "disabled",
	"consent_required": "custom_derma_consent_required",
	"before_after_photo_required": "custom_derma_before_after_photo_required",
	"product_tracking_required": "custom_derma_product_tracking_required",
	"device_settings_required": "custom_derma_device_settings_required",
	"variables_per_procedure": "custom_derma_variables_per_procedure",
	"print_procedure_variables": "custom_derma_print_procedure_variables",
}
EDITOR_ROW_FIELDS = [
	*EDITOR_TEXT_FIELDS.values(),
	*EDITOR_CHECK_FIELDS.values(),
	MARKER_SIZE_FIELD,
	"rate",
	"modified",
]

# Required fields the two safety flags append to whatever a template declares. A
# variables row cannot call one of these optional - the procedure-creation gate
# re-checks them, so the template would promise what the server refuses.
PRODUCT_TRACKING_SOURCE = "product_tracking"
DEVICE_SETTINGS_SOURCE = "device_settings"
PRODUCT_TRACKING_REQUIRED_FIELDS = ["product_name", "lot_no", "expiry_date"]
DEVICE_SETTINGS_REQUIRED_FIELDS = ["device", "settings"]
SAFETY_FLAG_REQUIRED_SOURCES = {PRODUCT_TRACKING_SOURCE, DEVICE_SETTINGS_SOURCE}

VARIABLE_FIELDTYPES = ["Data", "Select", "Float", "Int", "Small Text", "Date", "Check"]

PHOTO_STAGE_BEFORE = "Before"
PHOTO_STAGE_AFTER = "After"
PHOTO_STAGE_VISIT = "Visit"
CHART_PHOTO_STAGES = (PHOTO_STAGE_BEFORE, PHOTO_STAGE_AFTER, PHOTO_STAGE_VISIT)
BEFORE_AFTER_SET_TYPE = "Before/After"
STARTED_PROCEDURE_STATUSES = {"In Progress", "Completed"}

CONFIG_CATEGORY_FIELDS = [
	"name",
	"title",
	"workflow",
	"sequence",
	"disabled",
	"marker_behavior",
	"marker_color",
	"default_body_template",
]


# Roles trusted to view and chart clinical data through this API. Doctype-level
# DocPerms across healthcare/do_health/do_derma are inconsistent (e.g. Clinical
# Procedure grants Nursing User/Physician, Health Annotation grants only System
# Manager, do_derma's own doctypes grant Healthcare Practitioner/Administrator),
# and several writes below use ignore_permissions=True to bridge that gap. This
# check is the actual authorization boundary for every endpoint in this module -
# it must run before any of them read or write patient data.
CLINICAL_ACCESS_ROLES = {
	"System Manager",
	"Healthcare Administrator",
	"Healthcare Practitioner",
	"Nursing User",
	"Physician",
}

STANDARD_DB_FIELDS = {
	"name",
	"owner",
	"creation",
	"modified",
	"modified_by",
	"docstatus",
	"parent",
	"parentfield",
	"parenttype",
	"idx",
}


def _ensure_clinical_access() -> None:
	if not set(frappe.get_roles()) & CLINICAL_ACCESS_ROLES:
		frappe.throw(_("You are not permitted to access clinical chart data."), frappe.PermissionError)


def _has_doctype(doctype: str) -> bool:
	return frappe.db.exists("DocType", doctype)


def _has_field(doctype: str, fieldname: str) -> bool:
	try:
		return frappe.get_meta(doctype).has_field(fieldname)
	except Exception:
		return False


def _select_existing_fields(doctype: str, fields: list[str]) -> list[str]:
	meta = frappe.get_meta(doctype)
	selected = []
	for field in fields:
		if field == "name":
			selected.append(field)
			continue
		if not (field in STANDARD_DB_FIELDS or meta.has_field(field)):
			continue
		try:
			if not frappe.db.has_column(doctype, field):
				continue
		except Exception:
			continue
		selected.append(field)
	return selected


def _safe_derma_context(label: str, fallback: Any, getter, errors: list[str] | None = None):
	"""Degrade one chart section to its fallback. `errors` collects the section label
	so the payload can say what broke; the traceback never leaves the server."""
	try:
		return getter()
	except Exception:
		frappe.log_error(
			title=_("Derma Chart Context: {0}").format(label),
			message=frappe.get_traceback(),
		)
		if errors is not None and label not in errors:
			errors.append(label)
		return fallback


def _patient_fields() -> list[str]:
	fields = ["name", "patient_name", "sex", "dob", "mobile", "image"]
	for optional in ["custom_cpr", "custom_file_number", "custom_allergies", "allergies"]:
		if _has_field("Patient", optional):
			fields.append(optional)
	return fields


def _appointment_fields() -> list[str]:
	fields = [
		"name",
		"patient",
		"patient_name",
		"status",
		"appointment_type",
		"appointment_date",
		"appointment_time",
		"practitioner",
		"practitioner_name",
		"department",
		"notes",
	]
	for optional in ["custom_visit_reason", "custom_appointment_category", "custom_past_appointment"]:
		if _has_field("Patient Appointment", optional):
			fields.append(optional)
	return fields


def _ensure_encounter(appointment: str | None = None, patient: str | None = None) -> str | None:
	if appointment:
		existing = frappe.db.get_value(
			"Patient Encounter",
			{"appointment": appointment, "docstatus": ["<", 2]},
			"name",
			order_by="creation desc",
		)
		if existing:
			return existing

	if not appointment and patient:
		existing = frappe.db.get_value(
			"Patient Encounter",
			{"patient": patient, "docstatus": 0},
			"name",
			order_by="creation desc",
		)
		if existing:
			return existing
		return None

	response = create_encounter_for_appointment(appointment=appointment, patient=patient)
	return response.get("name") if isinstance(response, dict) else None


def _get_visit_context(
	patient: str | None = None, appointment: str | None = None, encounter: str | None = None
) -> dict[str, Any]:
	if encounter:
		encounter_doc = frappe.get_doc("Patient Encounter", encounter)
		if patient and patient != encounter_doc.patient:
			frappe.throw(
				_("Encounter {0} belongs to a different patient.").format(encounter), frappe.ValidationError
			)
		patient = patient or encounter_doc.patient
		appointment = appointment or encounter_doc.appointment
	elif appointment:
		appointment_doc = frappe.get_doc("Patient Appointment", appointment)
		if patient and patient != appointment_doc.patient:
			frappe.throw(
				_("Appointment {0} belongs to a different patient.").format(appointment), frappe.ValidationError
			)
		patient = patient or appointment_doc.patient
		encounter = _ensure_encounter(appointment=appointment, patient=patient)
	elif patient:
		encounter = _ensure_encounter(patient=patient)
	else:
		frappe.throw(_("Patient, appointment, or encounter is required."))

	if encounter:
		encounter_doc = frappe.get_doc("Patient Encounter", encounter)
		patient = patient or encounter_doc.patient
		appointment = appointment or encounter_doc.appointment

	if not patient:
		frappe.throw(_("Unable to resolve patient context."))

	patient_doc = frappe.db.get_value("Patient", patient, _patient_fields(), as_dict=True) or {}
	appointment_doc = {}
	if appointment:
		appointment_doc = (
			frappe.db.get_value("Patient Appointment", appointment, _appointment_fields(), as_dict=True) or {}
		)

	encounter_summary = {}
	if encounter:
		encounter_summary = (
			frappe.db.get_value(
				"Patient Encounter",
				encounter,
				[
					"name",
					"status",
					"docstatus",
					"encounter_date",
					"encounter_time",
					"practitioner",
					"practitioner_name",
					"appointment_type",
				],
				as_dict=True,
			)
			or {}
		)

	return {
		"patient": patient_doc,
		"appointment": appointment_doc,
		"encounter": encounter_summary,
		"patient_name": patient_doc.get("patient_name") or appointment_doc.get("patient_name"),
		"patient_id": patient,
		"appointment_id": appointment,
		"encounter_id": encounter,
	}


def _base_filters(
	patient: str, appointment: str | None = None, encounter: str | None = None
) -> dict[str, Any]:
	filters = {"patient": patient}
	if encounter:
		filters["encounter"] = encounter
	elif appointment:
		filters["appointment"] = appointment
	return filters


def _parse_json(value: str | None, fallback: Any) -> Any:
	if not value:
		return fallback
	try:
		return json.loads(value)
	except Exception:
		return fallback


def _normalize_position(payload: dict[str, Any]) -> None:
	"""Clamp a placement to the template. Absent keys are left absent - a partial save
	(variables only) must not drag the mark back to the top-left corner."""
	for field in ("x_percent", "y_percent"):
		if field in payload:
			payload[field] = max(0, min(100, flt(payload.get(field))))


def _set_patient_name(doc) -> None:
	if doc.patient and not doc.patient_name:
		doc.patient_name = frappe.db.get_value("Patient", doc.patient, "patient_name")


def _get_categories() -> list[dict[str, Any]]:
	if not _has_doctype("Derma Procedure Category"):
		return []

	return frappe.get_all(
		"Derma Procedure Category",
		filters={"disabled": 0},
		fields=[
			"name",
			"title",
			"workflow",
			"sequence",
			"marker_behavior",
			"marker_color",
			"marker_label",
			"default_body_template",
			"note_sentence_template",
		],
		order_by="sequence asc, title asc",
		limit=100,
	)


def _get_body_templates() -> list[dict[str, Any]]:
	if not _has_doctype("Derma Body Template"):
		return []

	rows = frappe.get_all(
		"Derma Body Template",
		filters={"disabled": 0},
		fields=[
			"name",
			"title",
			"template_type",
			"gender",
			"is_standard",
			"view_key",
			"sequence",
			"image",
			"annotation_template",
			"regions_json",
		],
		order_by="sequence asc, title asc",
		limit=100,
	)
	for row in rows:
		row["regions"] = _parse_json(row.pop("regions_json", None), [])
		row["parts"] = []
		row["default_for_categories"] = []
	if rows:
		_attach_body_template_parts(rows)
	if rows and _has_doctype("Derma Procedure Category"):
		default_rows = frappe.get_all(
			"Derma Procedure Category",
			filters={"disabled": 0, "default_body_template": ["is", "set"]},
			fields=["title", "default_body_template"],
			limit=200,
		)
		row_map = {row.get("name"): row for row in rows}
		for default in default_rows:
			template = row_map.get(default.get("default_body_template"))
			if template is not None:
				template.setdefault("default_for_categories", []).append(default.get("title"))
	return rows


def _attach_body_template_parts(rows: list[dict[str, Any]], include_disabled: bool = False) -> None:
	template_names = [row.get("name") for row in rows if row.get("name")]
	parts_by_template: dict[str, list[dict[str, Any]]] = {}

	if template_names and _has_doctype("Derma Body Template Part"):
		filters: dict[str, Any] = {"body_template": ["in", template_names]}
		if not include_disabled:
			filters["disabled"] = 0
		part_rows = frappe.get_all(
			"Derma Body Template Part",
			filters=filters,
			fields=["name", "body_template", "part_name", "shape_json", "color", "opacity", "disabled"],
			order_by="creation asc",
			limit=1000,
		)
		for part in _hydrate_template_parts(
			part_rows, "Derma Template Part Variable", "Derma Body Template Part"
		):
			part["source"] = "Derma Body Template Part"
			parts_by_template.setdefault(part.get("body_template"), []).append(part)

	for row in rows:
		row["parts"] = parts_by_template.get(row.get("name"), [])


def _hydrate_template_parts(
	part_rows: list[dict[str, Any]], child_doctype: str, parenttype: str
) -> list[dict[str, Any]]:
	if not part_rows:
		return []
	parent_names = [part.get("name") for part in part_rows if part.get("name")]
	variables_by_parent: dict[str, list[dict[str, Any]]] = {}
	if parent_names and _has_doctype(child_doctype):
		variable_rows = frappe.get_all(
			child_doctype,
			filters={"parent": ["in", parent_names], "parenttype": parenttype},
			fields=["parent", "variable_name", "type", "options", "idx"],
			order_by="parent asc, idx asc",
			limit=2000,
		)
		for variable in variable_rows:
			variables_by_parent.setdefault(variable.parent, []).append(
				{
					"variable_name": variable.variable_name,
					"fieldname": _variable_fieldname(variable.variable_name),
					"type": variable.type,
					"fieldtype": _normalize_variable_type(variable.type),
					"options": variable.options or "",
				}
			)
	for part in part_rows:
		part["variables"] = variables_by_parent.get(part.get("name"), [])
	return part_rows


def get_config_body_templates() -> list[dict[str, Any]]:
	"""Every Body Template with its Area counts, retired templates and Areas included -
	the config list exists to show what the chart filters out. Counts are aggregated in
	the database because a truncated read here would report the wrong number."""
	if not _has_doctype("Derma Body Template"):
		return []

	rows = frappe.get_all(
		"Derma Body Template",
		fields=["name", "title", "template_type", "gender", "sequence", "disabled"],
		order_by="sequence asc, title asc",
		limit_page_length=0,
	)
	counts = _count_template_areas([row.get("name") for row in rows])
	for row in rows:
		row["area_count"], row["retired_area_count"] = counts.get(row.get("name"), (0, 0))
		row["warnings"] = _body_template_warnings(row)
	return rows


def _body_template_warnings(row: dict[str, Any]) -> list[str]:
	"""A live map with no live Area cannot be marked on at all. Retired Areas do not
	count, and a retired map is not a problem waiting to be fixed."""
	if cint(row.get("disabled")) or row.get("area_count"):
		return []
	return ["no_areas"]


def _count_template_areas(template_names: list[str]) -> dict[str, tuple[int, int]]:
	"""Body Template -> (live areas, retired areas). Only the disabled flag is read, so
	the whole unlimited list stays small."""
	if not template_names or not _has_doctype("Derma Body Template Part"):
		return {}

	rows = frappe.get_all(
		"Derma Body Template Part",
		filters={"body_template": ["in", template_names]},
		fields=["body_template", "disabled"],
		limit_page_length=0,
	)
	counts: dict[str, tuple[int, int]] = {}
	for row in rows:
		live, retired = counts.get(row.body_template, (0, 0))
		counts[row.body_template] = (live, retired + 1) if cint(row.disabled) else (live + 1, retired)
	return counts


def get_config_procedure_templates() -> list[dict[str, Any]]:
	"""Every derma procedure template with the owner of each required field. Retired
	templates are listed too - the config workspace exists to show what the chart hides."""
	if not _has_doctype("Clinical Procedure Template"):
		return []

	fields = _select_existing_fields("Clinical Procedure Template", [*DERMA_TEMPLATE_FIELDS, "disabled"])
	rows = frappe.get_all(
		"Clinical Procedure Template",
		fields=fields,
		order_by="template asc",
		limit_page_length=0,
	)
	return [_config_procedure_template(row) for row in rows if _is_derma_template(row)]


def _config_procedure_template(row: dict[str, Any]) -> dict[str, Any]:
	variables = _get_template_variables(row)
	required = _required_fields_with_owners(row, variables)
	declared, _seen = _parse_template_variable_schema(row.get("custom_derma_variables_json"), [])
	warnings: list[str] = []
	if not required:
		warnings.append("no_required_fields")
	if any(not field["enforced"] for field in required):
		warnings.append("unenforced_required_fields")
	if _is_unreadable_json(row.get("custom_derma_variables_json"), declared):
		warnings.append("unreadable_variables")

	return {
		"name": row.get("name"),
		"template": row.get("template") or row.get("name"),
		"category": row.get("custom_derma_category") or "",
		"marker_behavior": row.get("custom_derma_marker_behavior") or "",
		"effective_marker": _effective_marker(row),
		"has_marker_preset": bool(str(row.get("custom_derma_marker_preset_json") or "").strip()),
		"disabled": cint(row.get("disabled")),
		"variable_count": len(variables),
		"required_fields": required,
		"warnings": warnings,
	}


def _effective_marker(row: dict[str, Any]) -> dict[str, Any]:
	"""The mark the chart will actually stamp. `save_chart_mark` falls back to the category,
	so a card drawing the template's own empty field would draw the wrong thing."""
	defaults = _category_defaults(row.get("custom_derma_category"))
	behavior = row.get("custom_derma_marker_behavior") or ""
	color = row.get("custom_derma_marker_color") or ""
	return {
		"behavior": behavior or defaults.get("marker_behavior") or "",
		"color": color or defaults.get("marker_color") or "",
		"inherited": bool(not behavior and defaults.get("marker_behavior")),
	}


def _required_fields_with_owners(
	template_row: dict[str, Any], variables: list[dict[str, Any]]
) -> list[dict[str, Any]]:
	"""Required fields with their owner and whether the chart actually enforces them.

	A fieldname no `_default_derma_variable` knows never reaches the variable list, so
	nothing enforces it; a variables row marked `required` is enforced while no owner
	claims it. Both are silent today, so both are reported here.
	"""
	enforced = [variable.get("fieldname") for variable in variables if variable.get("required")]
	fields = [
		{**owner, "enforced": owner["fieldname"] in enforced}
		for owner in _required_field_owners(template_row)
	]
	claimed = {field["fieldname"] for field in fields}
	fields.extend(
		{"fieldname": fieldname, "source": "variables_json", "enforced": True}
		for fieldname in enforced
		if fieldname and fieldname not in claimed
	)
	return fields


def _required_field_owners(template_row: dict[str, Any]) -> list[dict[str, str]]:
	"""The owners of "required", in the order they win: whatever the template declares,
	then each safety flag."""
	groups = (
		("template", _parse_required_fields(template_row.get("custom_derma_required_fields"))),
		(
			PRODUCT_TRACKING_SOURCE,
			PRODUCT_TRACKING_REQUIRED_FIELDS
			if template_row.get("custom_derma_product_tracking_required")
			else [],
		),
		(
			DEVICE_SETTINGS_SOURCE,
			DEVICE_SETTINGS_REQUIRED_FIELDS
			if template_row.get("custom_derma_device_settings_required")
			else [],
		),
	)
	owners: list[dict[str, str]] = []
	seen: set[str] = set()
	for source, fieldnames in groups:
		for fieldname in fieldnames:
			if not fieldname or fieldname in seen:
				continue
			seen.add(fieldname)
			owners.append({"fieldname": fieldname, "source": source})
	return owners


def _locked_required_sources(template_row: dict[str, Any]) -> dict[str, str]:
	"""Fieldname -> the safety flag that owns it. A row cannot call one of these optional,
	and the builder renders them locked under the flag's name."""
	return {
		owner["fieldname"]: owner["source"]
		for owner in _required_field_owners(template_row)
		if owner["source"] in SAFETY_FLAG_REQUIRED_SOURCES
	}


def _is_unreadable_json(raw: Any, parsed: Any) -> bool:
	"""Configured JSON that parses to nothing, `[]`, `{}` and `null` being honest
	empties. The chart renders nothing and says nothing when this happens, so whoever
	asks is the only place it can surface."""
	if parsed:
		return False
	text = str(raw or "").strip()
	return bool(text) and text not in {"[]", "{}", "null"}


def get_config_categories() -> list[dict[str, Any]]:
	"""Every category with how many templates point at it."""
	if not _has_doctype("Derma Procedure Category"):
		return []

	fields = _select_existing_fields("Derma Procedure Category", CONFIG_CATEGORY_FIELDS)
	rows = frappe.get_all(
		"Derma Procedure Category",
		fields=fields,
		order_by="sequence asc, title asc",
		limit_page_length=0,
	)
	counts = _count_templates_per_category()
	for row in rows:
		row["template_count"] = counts.get(row.get("name"), 0)
	return rows


def _count_templates_per_category() -> dict[str, int]:
	"""Retired templates are counted too - they still point at the category."""
	if not _has_field("Clinical Procedure Template", "custom_derma_category"):
		return {}

	rows = frappe.get_all(
		"Clinical Procedure Template",
		filters={"custom_derma_category": ["is", "set"]},
		fields=["custom_derma_category"],
		limit_page_length=0,
	)
	counts: dict[str, int] = {}
	for row in rows:
		category = row.get("custom_derma_category")
		counts[category] = counts.get(category, 0) + 1
	return counts


def get_config_readiness() -> dict[str, Any]:
	"""How completion is gated today, plus the unfinished features. Read-only: the mode is
	edited on the Derma Settings singleton. A site missing the fields cannot choose a mode,
	so it stays on Warn - which is what the warning says."""
	readiness = get_readiness_settings()
	toggles = get_feature_toggles()
	return {
		"enforcement": readiness["enforcement"],
		"todo_downgrades_blockers": readiness["todo_downgrades_blockers"],
		"warnings": [] if readiness["is_configurable"] else ["completion_gate_is_client_side"],
		"feature_toggles": [
			{"fieldname": fieldname, "enabled": bool(toggles.get(fieldname))} for fieldname in FEATURE_TOGGLES
		],
	}


def get_config_health(sections: dict[str, Any]) -> dict[str, int]:
	"""How many rows each tool has to fix, keyed by the config rail's tool keys. Counted
	from the sections the panels render, so a badge and its panel cannot disagree.

	Categories carry no rule of their own since their requirement fields were deleted, so
	the rail shows no badge for them rather than a count that is always zero."""
	return {
		"body-templates": len([row for row in sections["body_templates"] if row.get("warnings")]),
		"procedure-templates": len([row for row in sections["procedure_templates"] if row.get("warnings")]),
		"readiness": len(sections["readiness"].get("warnings", [])),
	}


@frappe.whitelist()
def get_derma_config_overview():
	"""Everything the config workspace lists, in one round trip."""
	_ensure_clinical_access()
	errors: list[str] = []
	sections = {
		"body_templates": _safe_derma_context("body templates", [], get_config_body_templates, errors),
		"procedure_templates": _safe_derma_context(
			"procedure templates", [], get_config_procedure_templates, errors
		),
		"categories": _safe_derma_context("categories", [], get_config_categories, errors),
		"readiness": _safe_derma_context("readiness", {}, get_config_readiness, errors),
	}
	return {
		**sections,
		"health": get_config_health(sections),
		"errors": errors,
		"can_write": _can_write_procedure_templates(),
	}


def _can_write_procedure_templates() -> bool:
	"""The doctype's own permission, not the role gate: the panel renders read-only rather
	than offering edits a save would refuse."""
	return bool(frappe.has_permission("Clinical Procedure Template", "write"))


@frappe.whitelist()
def get_derma_body_template_parts(body_template: str, include_disabled: int | str = 0):
	_ensure_clinical_access()
	if not body_template:
		frappe.throw(_("Derma Body Template is required."))
	if not frappe.db.exists("Derma Body Template", body_template):
		frappe.throw(_("Derma Body Template {0} does not exist.").format(body_template))
	rows = [
		{
			"name": body_template,
			"annotation_template": frappe.db.get_value(
				"Derma Body Template", body_template, "annotation_template"
			),
		}
	]
	_attach_body_template_parts(rows, include_disabled=bool(cint(include_disabled)))
	return rows[0].get("parts", [])


@frappe.whitelist()
def save_derma_body_template_parts(body_template: str, parts: str | list[dict[str, Any]]):
	_ensure_clinical_access()
	if not body_template:
		frappe.throw(_("Derma Body Template is required."))
	if not frappe.db.exists("Derma Body Template", body_template):
		frappe.throw(_("Derma Body Template {0} does not exist.").format(body_template))
	payload = json.loads(parts) if isinstance(parts, str) else list(parts or [])
	if not isinstance(payload, list):
		frappe.throw(_("Parts must be a list."))

	incoming_names = {part.get("name") for part in payload if part.get("name")}
	existing = frappe.get_all(
		"Derma Body Template Part",
		filters={"body_template": body_template, "disabled": 0},
		fields=["name"],
	)
	for row in existing:
		if row.name not in incoming_names:
			frappe.db.set_value("Derma Body Template Part", row.name, "disabled", 1, update_modified=False)

	for index, part_data in enumerate(payload):
		doc = _part_doc_for_save(body_template, part_data.get("name"))
		doc.body_template = body_template
		doc.part_name = part_data.get("part_name") or _("Region {0}").format(index + 1)
		shape_json = part_data.get("shape_json")
		doc.shape_json = shape_json if isinstance(shape_json, str) else json.dumps(shape_json or [])
		doc.color = part_data.get("color") or "#4dabf7"
		doc.opacity = flt(part_data.get("opacity") if part_data.get("opacity") is not None else 0.2)
		doc.disabled = cint(part_data.get("disabled") or 0)
		_apply_part_variables(doc, part_data.get("variables"))
		doc.save(ignore_permissions=True)

	return get_derma_body_template_parts(body_template, include_disabled=1)


def _part_doc_for_save(body_template: str, name: str | None):
	"""An area is only editable through the body template that owns it. A name from
	another map starts a new area here rather than moving that one."""
	owner = frappe.db.get_value("Derma Body Template Part", name, "body_template") if name else None
	if owner == body_template:
		return frappe.get_doc("Derma Body Template Part", name)
	return frappe.new_doc("Derma Body Template Part")


def _apply_part_variables(doc, rows: Any) -> None:
	"""Rewrite the variable rows only when they differ, so untouched child rows keep
	their name and idx."""
	incoming = [
		{
			"variable_name": row.get("variable_name"),
			"type": row.get("type") or row.get("fieldtype") or "Data",
			"options": row.get("options") or "",
		}
		for row in rows or []
		if isinstance(row, dict) and row.get("variable_name")
	]
	stored = [
		{"variable_name": row.variable_name, "type": row.type, "options": row.options or ""}
		for row in doc.get("variables") or []
	]
	if stored == incoming:
		return
	doc.set("variables", [])
	for row in incoming:
		doc.append("variables", row)


@frappe.whitelist()
def get_derma_procedure_template(template: str = ""):
	"""One procedure template as the config panel's detail view edits it. No template means
	the panel is creating one, so it gets the same payload with nothing filled in."""
	_ensure_clinical_access()
	if not template:
		return _procedure_template_payload({})
	return _procedure_template_payload(_derma_template_row(template))


@frappe.whitelist()
def save_derma_procedure_template(template: str, values: str | dict[str, Any]):
	"""Write one procedure template from the config panel - basics, derma behaviour and
	variables in a single document save. An empty `template` creates one.

	No ignore_permissions: unlike the Health Annotation writes elsewhere in this module,
	Clinical Procedure Template's own DocPerms are consistent, so they run on top of the
	role gate.
	"""
	_ensure_clinical_access()
	payload = values if isinstance(values, dict) else _parse_json(values, None)
	if not isinstance(payload, dict):
		frappe.throw(_("Values must be an object."))
	if not _has_field("Clinical Procedure Template", "custom_derma_variables_json"):
		frappe.throw(_("This site has no derma template fields yet. Run bench migrate first."))
	_ensure_template_write_access(template)

	doc = _procedure_template_doc(template, payload)
	_apply_template_values(doc, payload)
	if "variables" in payload:
		_apply_template_variables(doc, payload["variables"])
	doc.save()
	return _procedure_template_payload(_derma_template_row(doc.name))


def _ensure_template_write_access(template: str) -> None:
	if not frappe.has_permission("Clinical Procedure Template", "write", doc=template or None):
		frappe.throw(_("You are not permitted to change procedure templates."), frappe.PermissionError)


def _procedure_template_doc(template: str, payload: dict[str, Any]):
	if template:
		doc = frappe.get_doc("Clinical Procedure Template", _derma_template_row(template)["name"])
		if payload.get("modified"):
			# Frappe compares this against the stored timestamp and refuses a stale write
			# rather than overwriting whoever saved in the meantime.
			doc.modified = payload["modified"]
		return doc

	name = str(payload.get("template") or "").strip()
	if not name:
		frappe.throw(_("A new procedure template needs a name."))
	doc = frappe.new_doc("Clinical Procedure Template")
	doc.template = name
	doc.item_code = str(payload.get("item_code") or name).strip()
	return doc


def _apply_template_values(doc, payload: dict[str, Any]) -> None:
	"""Only what the payload carries. The panel sends every field, but a caller that sends
	one is editing one."""
	for key, fieldname in EDITOR_TEXT_FIELDS.items():
		if key in payload and _has_field("Clinical Procedure Template", fieldname):
			doc.set(fieldname, str(payload[key] or "").strip())
	for key, fieldname in EDITOR_CHECK_FIELDS.items():
		if key in payload and _has_field("Clinical Procedure Template", fieldname):
			doc.set(fieldname, cint(payload[key]))
	if "rate" in payload:
		doc.rate = flt(payload["rate"])
	if "marker_size" in payload and _has_field("Clinical Procedure Template", MARKER_SIZE_FIELD):
		doc.set(MARKER_SIZE_FIELD, validated_marker_size(payload["marker_size"]))
	if "allowed_body_templates" in payload and _has_field(
		"Clinical Procedure Template", "custom_derma_allowed_body_templates"
	):
		doc.custom_derma_allowed_body_templates = _joined_body_templates(payload["allowed_body_templates"])


def _apply_template_variables(doc, variables: Any) -> None:
	"""Validated the way the chart reads them, and the required list derived from the
	result: the variables and the safety flags own it, never the client."""
	locked = _locked_required_sources(doc.as_dict())
	rows = _validated_variable_rows(variables, set(locked))
	doc.custom_derma_variables_json = json.dumps(rows, indent=2)
	if _has_field("Clinical Procedure Template", "custom_derma_required_fields"):
		doc.custom_derma_required_fields = json.dumps(
			[field["fieldname"] for field in rows if field.get("required")]
		)


def _joined_body_templates(value: Any) -> str:
	"""Back to the comma-separated string `_ensure_body_template_allowed` reads. Empty
	still means every map."""
	entries = value if isinstance(value, list) else _split_csv(str(value or ""))
	return ",".join(str(entry).strip() for entry in entries if str(entry).strip())


def _derma_template_row(template: str) -> dict[str, Any]:
	if not template or not frappe.db.exists("Clinical Procedure Template", template):
		frappe.throw(_("Clinical Procedure Template {0} does not exist.").format(template))
	fields = _select_existing_fields(
		"Clinical Procedure Template", [*DERMA_TEMPLATE_FIELDS, *EDITOR_ROW_FIELDS]
	)
	return frappe.db.get_value("Clinical Procedure Template", template, fields, as_dict=True) or {}


def _procedure_template_payload(template_row: dict[str, Any]) -> dict[str, Any]:
	variables = _editor_variable_rows(template_row)
	payload = {
		"name": template_row.get("name"),
		"template": template_row.get("template") or template_row.get("name"),
		"rate": flt(template_row.get("rate")),
		"allowed_body_templates": _split_csv(template_row.get("custom_derma_allowed_body_templates")),
		"effective_marker": _effective_marker(template_row),
		"has_marker_preset": bool(str(template_row.get("custom_derma_marker_preset_json") or "").strip()),
		"marker_size": flt(template_row.get(MARKER_SIZE_FIELD)),
		"marker_size_range": {
			"min": MARKER_SIZE_MIN,
			"max": MARKER_SIZE_MAX,
			"step": MARKER_SIZE_STEP,
		},
		"variables": variables,
		"required_fields": _required_fields_with_owners(template_row, _get_template_variables(template_row)),
		"marker_behaviors": _marker_behavior_options(),
		"fieldtypes": VARIABLE_FIELDTYPES,
		"modified": str(template_row.get("modified") or ""),
		"can_write": _can_write_procedure_templates(),
	}
	payload.update({key: template_row.get(fieldname) or "" for key, fieldname in EDITOR_TEXT_FIELDS.items()})
	payload.update({key: cint(template_row.get(fieldname)) for key, fieldname in EDITOR_CHECK_FIELDS.items()})
	return payload


def _editor_variable_rows(template_row: dict[str, Any]) -> list[dict[str, Any]]:
	locked = _locked_required_sources(template_row)
	return [
		{
			"fieldname": variable["fieldname"],
			"label": variable["label"],
			"fieldtype": variable["fieldtype"],
			"options": variable["options"],
			"required": bool(variable["required"]),
			"locked_by": locked.get(variable["fieldname"], ""),
		}
		for variable in _get_template_variables(template_row)
	]


def _marker_behavior_options() -> list[str]:
	"""From meta, because `add_derma_freehand_marker_behavior` appends options through a
	property setter - a literal list here would go stale at the next patch."""
	field = frappe.get_meta("Clinical Procedure Template").get_field("custom_derma_marker_behavior")
	options = str(getattr(field, "options", "") or "") if field else ""
	return [option.strip() for option in options.split("\n") if option.strip()]


def _validated_variable_rows(value: Any, locked: set[str]) -> list[dict[str, Any]]:
	"""The rows as they will be stored. Every fieldname is resolved by the runtime's own
	`_variable_fieldname`, so a set the builder accepts is a set the chart can render.

	A locked row stores no `required` key: the safety flag owns it, and freezing its
	answer here would keep the field required after the flag is switched off.
	"""
	# No silent fallback: an unreadable payload here would wipe the template's variables
	# and its required list, which is the one write in this module that cannot be undone
	# from the builder.
	rows = value if isinstance(value, list) else _parse_json(value, None)
	if not isinstance(rows, list):
		frappe.throw(_("Variables must be a list."))

	validated: list[dict[str, Any]] = []
	labels: dict[str, str] = {}
	for row in rows:
		if not isinstance(row, dict):
			frappe.throw(_("Every variable must be a row with a label."))
		label = str(row.get("label") or row.get("variable_name") or row.get("fieldname") or "").strip()
		fieldname = _variable_fieldname(row.get("fieldname") or label)
		if not fieldname:
			frappe.throw(_("Every variable needs a label."))
		if fieldname in labels:
			frappe.throw(
				_("{0} and {1} both resolve to the fieldname {2}.").format(
					labels[fieldname], label, fieldname
				)
			)
		fieldtype = _normalize_variable_type(row.get("fieldtype") or row.get("type"))
		options = str(row.get("options") or "").strip()
		if fieldtype == "Select" and not options:
			frappe.throw(_("{0} is a Select variable and needs at least one option.").format(label))

		labels[fieldname] = label
		variable = {
			"fieldname": fieldname,
			"label": label or fieldname.replace("_", " ").title(),
			"fieldtype": fieldtype,
			"options": options,
		}
		if fieldname not in locked:
			variable["required"] = bool(row.get("required"))
		validated.append(variable)
	return validated


def _get_template_sets() -> list[dict[str, Any]]:
	if not _has_doctype("Derma Template Set"):
		return []
	rows = frappe.get_all(
		"Derma Template Set",
		filters={"disabled": 0},
		fields=[
			"name",
			"title",
			"gender",
			"workflow",
			"sequence",
			"procedure_categories",
			"body_templates",
			"notes",
		],
		order_by="sequence asc, title asc",
		limit=100,
	)
	for row in rows:
		row["procedure_category_list"] = _split_csv(row.get("procedure_categories"))
		row["body_template_list"] = _split_csv(row.get("body_templates"))
	return rows


def _split_csv(value: str | None) -> list[str]:
	return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _get_derma_procedure_templates() -> list[dict[str, Any]]:
	fields = _select_existing_fields("Clinical Procedure Template", DERMA_TEMPLATE_FIELDS)
	filters = {"disabled": 0} if _has_field("Clinical Procedure Template", "disabled") else {}
	rows = frappe.get_all(
		"Clinical Procedure Template",
		filters=filters,
		fields=fields,
		order_by="template asc",
		limit=300,
	)
	derma_rows = [row for row in rows if _is_derma_template(row)]
	for row in derma_rows:
		row["derma_variables"] = _get_template_variables(row)
		category_defaults = _category_defaults(row.get("custom_derma_category"))
		row["derma_category_defaults"] = category_defaults
		# A template with no marker_behavior/color of its own inherits the category's -
		# resolve it here so every consumer (frontend included) sees the same effective
		# value save_chart_mark() would apply, instead of each place re-implementing the fallback.
		row["custom_derma_marker_behavior"] = row.get(
			"custom_derma_marker_behavior"
		) or category_defaults.get("marker_behavior")
		row["custom_derma_marker_color"] = row.get("custom_derma_marker_color") or category_defaults.get(
			"marker_color"
		)
	return derma_rows


def _is_derma_template(row: dict[str, Any]) -> bool:
	return bool(
		row.get("custom_derma_category")
		or row.get("custom_derma_marker_behavior")
		or row.get("custom_derma_required_fields")
	)


def _get_template_variables(template_row: dict[str, Any]) -> list[dict[str, Any]]:
	"""Return derma detail fields configured by Clinical Procedure Template."""

	required = [owner["fieldname"] for owner in _required_field_owners(template_row)]
	variables, seen = _parse_template_variable_schema(
		template_row.get("custom_derma_variables_json"),
		required,
		set(_locked_required_sources(template_row)),
	)

	for fieldname in required:
		if fieldname in seen:
			continue
		variable = _default_derma_variable(fieldname)
		if variable:
			variable["required"] = True
			variables.append(variable)
			seen.add(fieldname)

	return variables


def _parse_template_variable_schema(
	value: str | list | dict | None, required: list[str], locked: set[str] | None = None
) -> tuple[list[dict[str, Any]], set[str]]:
	"""Variables as the chart renders them. A row saying `"required": false` is believed
	unless a safety flag owns the field, in which case the flag wins."""
	rows = _parse_json(value, [])
	if isinstance(rows, dict):
		rows = rows.get("variables") or rows.get("fields") or []
	if isinstance(rows, str):
		rows = [part.strip() for part in rows.split(",") if part.strip()]
	if not isinstance(rows, list):
		return [], set()

	variables: list[dict[str, Any]] = []
	seen: set[str] = set()
	locked = set(locked or [])
	for row in rows:
		declared = row.get("required") if isinstance(row, dict) else None
		if isinstance(row, str):
			fieldname = _variable_fieldname(row)
			variable = _default_derma_variable(fieldname) or {
				"fieldname": fieldname,
				"label": row,
				"fieldtype": "Data",
				"options": "",
				"source": "Clinical Procedure Template",
			}
		elif isinstance(row, dict):
			label = row.get("label") or row.get("variable_name") or row.get("fieldname")
			fieldname = _variable_fieldname(row.get("fieldname") or label)
			if not fieldname:
				continue
			default = _default_derma_variable(fieldname) or {}
			variable = {
				"fieldname": fieldname,
				"label": label or default.get("label") or fieldname.replace("_", " ").title(),
				"fieldtype": _normalize_variable_type(
					row.get("fieldtype") or row.get("type") or default.get("fieldtype")
				),
				"options": row.get("options") or default.get("options") or "",
				"source": "Clinical Procedure Template",
			}
		else:
			continue
		variable["required"] = _variable_is_required(variable["fieldname"], declared, required, locked)
		variables.append(variable)
		seen.add(variable["fieldname"])
	return variables, seen


def _variable_is_required(fieldname: str, declared: Any, required: list[str], locked: set[str]) -> bool:
	"""A safety flag outranks the row, the row outranks the resolved set, and a row that
	says nothing inherits it."""
	if fieldname in locked:
		return True
	if declared is None:
		return fieldname in required
	return bool(declared)


def _parse_required_fields(value: str | list | None) -> list[str]:
	fields = _parse_json(value, value if isinstance(value, list) else [])
	if isinstance(fields, str):
		fields = [part.strip() for part in fields.split(",")]
	return [_variable_fieldname(field) for field in fields if field]


def _variable_fieldname(label: str | None) -> str:
	value = "".join(char.lower() if char.isalnum() else "_" for char in str(label or "").strip())
	while "__" in value:
		value = value.replace("__", "_")
	return value.strip("_")


def _normalize_variable_type(fieldtype: str | None) -> str:
	fieldtype = (fieldtype or "Data").strip()
	return fieldtype if fieldtype in VARIABLE_FIELDTYPES else "Data"


def _stringify_variable_value(value: Any) -> str:
	if value is None:
		return ""
	if isinstance(value, bool):
		return "1" if value else "0"
	return str(value)


def _apply_mark_area_variables(doc, raw: Any) -> None:
	"""Replace the mark's area variable rows. An absent key leaves the rows alone.

	save_chart_mark is called from several places that know nothing about areas, so
	only an explicit list - including an empty one, which clears - may touch them.
	"""
	if raw is None or not (
		_has_doctype("Derma Mark Variable") and _has_field("Derma Chart Mark", "area_variables")
	):
		return
	rows = raw if isinstance(raw, list) else _parse_json(raw, None)
	if not isinstance(rows, list):
		return
	doc.set(
		"area_variables",
		[row for row in doc.get("area_variables") or [] if row.get("source") == "Procedure"],
	)
	for row in rows:
		if not isinstance(row, dict):
			continue
		fieldname = _variable_fieldname(row.get("fieldname") or row.get("label"))
		if not fieldname:
			continue
		doc.append(
			"area_variables",
			{
				"fieldname": fieldname,
				"label": row.get("label") or row.get("variable_name") or fieldname,
				"value": _stringify_variable_value(row.get("value")),
				"source": "Area",
			},
		)


def _apply_mark_procedure_variables(doc, payload: dict[str, Any]) -> None:
	"""Store procedure variables that map to no mark field as their own rows.

	The studio sends the armed procedure's values twice: splatted, for the fields the
	mark owns, and whole under `procedure_variables`. A value that maps to no field
	would otherwise vanish while the badge legend keeps printing it."""
	if not (_has_doctype("Derma Mark Variable") and _has_field("Derma Chart Mark", "area_variables")):
		return
	declared = payload.get("procedure_variables")
	if isinstance(declared, str):
		declared = _parse_json(declared, None)
	values = declared if isinstance(declared, dict) else _unmapped_variable_values(payload)
	if values is None:
		return
	doc.set(
		"area_variables",
		[row for row in doc.get("area_variables") or [] if row.get("source") != "Procedure"],
	)
	for key, value in values.items():
		fieldname = _variable_fieldname(key)
		if not fieldname or fieldname in DERMA_MARK_FIELDS:
			continue
		doc.append(
			"area_variables",
			{
				"fieldname": fieldname,
				"label": frappe.unscrub(fieldname),
				"value": _stringify_variable_value(value),
				"source": "Procedure",
			},
		)


def _unmapped_variable_values(payload: dict[str, Any]) -> dict[str, Any] | None:
	"""None when the save carries no variable keys - absent means leave the rows alone."""
	unmapped = {
		key: value
		for key, value in payload.items()
		if key not in DERMA_MARK_FIELDS and key not in ("area_variables", "procedure_variables")
	}
	return unmapped or None


def _resolve_mark_template_part(payload: dict[str, Any]) -> None:
	"""Keep the area link only when it names an area of the payload's own body template.

	The annotation fan-out copies client-authored customData straight into this payload, so
	an unrelated part name reaching the field would label the mark with someone else's area.
	"""
	part = payload.get("body_template_part")
	if not part:
		return
	owner = (
		frappe.db.get_value("Derma Body Template Part", part, "body_template")
		if _has_doctype("Derma Body Template Part")
		else None
	)
	if not owner or owner != payload.get("body_template"):
		payload.pop("body_template_part")


def _hydrate_mark_area_variables(mark_rows: list[dict[str, Any]]) -> None:
	if not mark_rows or not _has_doctype("Derma Mark Variable"):
		return
	names = [row.get("name") for row in mark_rows if row.get("name")]
	rows = frappe.get_all(
		"Derma Mark Variable",
		filters={"parent": ["in", names], "parenttype": "Derma Chart Mark"},
		fields=["parent", "fieldname", "label", "value", "source"],
		order_by="parent asc, idx asc",
		# Unpaged on purpose: the caller already caps the parents, and a truncated read would
		# show a mark as missing values it actually has.
		limit=0,
	)
	by_parent: dict[str, list[dict[str, Any]]] = {}
	for row in rows:
		by_parent.setdefault(row.parent, []).append(
			{
				"fieldname": row.fieldname,
				"label": row.label,
				"value": row.value,
				"source": row.source,
			}
		)
	for mark in mark_rows:
		rows = by_parent.get(mark.get("name"), [])
		mark["area_variables"] = [row for row in rows if row.get("source") != "Procedure"]
		mark["procedure_variables"] = {
			row["fieldname"]: row["value"] for row in rows if row.get("source") == "Procedure"
		}


PROCEDURE_VARIABLES_FIELD = "custom_derma_procedure_variables"


def _has_procedure_variables() -> bool:
	return _has_doctype("Derma Procedure Variable") and _has_field(
		"Clinical Procedure", PROCEDURE_VARIABLES_FIELD
	)


def _procedure_level_variables(clinical_procedure: str, procedure_template: str) -> dict[str, str]:
	"""What a template asked to capture once for the whole procedure, keyed by fieldname."""
	if not clinical_procedure or not procedure_template or not _has_procedure_variables():
		return {}
	rows = frappe.get_all(
		"Derma Procedure Variable",
		filters={
			"parent": clinical_procedure,
			"parenttype": "Clinical Procedure",
			"procedure_template": procedure_template,
		},
		fields=["fieldname", "value"],
		order_by="idx asc",
		limit=0,
	)
	return {row.fieldname: row.value for row in rows}


@frappe.whitelist()
def get_procedure_variables(clinical_procedure: str) -> dict[str, dict[str, str]]:
	"""Every template's shared values on one procedure, keyed by template then fieldname.

	One read for the studio to seed from: a procedure that captures once has values to show
	before any mark exists, so they cannot be reached through the marks.
	"""
	_ensure_clinical_access()
	if not clinical_procedure or not _has_procedure_variables():
		return {}
	rows = frappe.get_all(
		"Derma Procedure Variable",
		filters={"parent": clinical_procedure, "parenttype": "Clinical Procedure"},
		fields=["procedure_template", "fieldname", "value"],
		order_by="idx asc",
		limit=0,
	)
	grouped: dict[str, dict[str, str]] = {}
	for row in rows:
		grouped.setdefault(row.procedure_template, {})[row.fieldname] = row.value
	return grouped


def _captures_variables_per_procedure(procedure_template: str) -> bool:
	if not procedure_template or not _has_field(
		"Clinical Procedure Template", "custom_derma_variables_per_procedure"
	):
		return False
	return bool(
		frappe.db.get_value(
			"Clinical Procedure Template", procedure_template, "custom_derma_variables_per_procedure"
		)
	)


def _mark_answers_itself(mark: dict[str, Any], own: dict[str, Any], fieldname: str) -> bool:
	"""Has this mark given its own answer for a variable, wherever that answer is kept?

	A fieldname Derma Chart Mark owns as a field of its own is stored there rather than as a
	child row - `_apply_mark_procedure_variables` skips those on purpose. Reading only the rows
	would let a shared value mask an override the practitioner typed. A numeric field reads 0
	when nothing was entered, so that counts as unanswered.
	"""
	if own.get(fieldname) not in (None, ""):
		return True
	value = mark.get(fieldname)
	if isinstance(value, (int, float)) and not isinstance(value, bool):
		return bool(value)
	return value not in (None, "")


def _merge_procedure_level_variables(mark_rows: list[dict[str, Any]]) -> None:
	"""Fill each mark's gaps from its procedure's shared values, for flagged templates only.

	The mark's own value always wins; a blank is a gap, not an override. What came from the
	procedure is listed separately in `inherited_variables` and must stay that way: the studio
	renders those as placeholders rather than values, and the copy-forward path strips them so
	last visit's shared dose is never pinned onto this visit's mark.
	"""
	if not mark_rows or not _has_procedure_variables():
		return
	flagged: dict[str, bool] = {}
	shared: dict[tuple[str, str], dict[str, str]] = {}
	for mark in mark_rows:
		mark.setdefault("inherited_variables", {})
		procedure = mark.get("clinical_procedure")
		template = mark.get("procedure_template")
		if not procedure or not template:
			continue
		if template not in flagged:
			flagged[template] = _captures_variables_per_procedure(template)
		if not flagged[template]:
			continue
		key = (procedure, template)
		if key not in shared:
			shared[key] = _procedure_level_variables(procedure, template)
		own = mark.get("procedure_variables") or {}
		inherited = {
			fieldname: value
			for fieldname, value in shared[key].items()
			if not _mark_answers_itself(mark, own, fieldname)
		}
		if not inherited:
			continue
		mark["inherited_variables"] = inherited
		mark["procedure_variables"] = {**own, **inherited}


def _carry_procedure_variables(source: str, target: str, procedure_template: str) -> None:
	"""Move a template's shared values onto the procedure a mark has just been re-anchored to.

	Without this the mark ends up on a procedure that holds none of the answers the
	practitioner already typed, and the studio reopens showing blanks.
	"""
	if not source or not target or source == target:
		return
	values = _procedure_level_variables(source, procedure_template)
	if not values:
		return
	save_procedure_variables(target, procedure_template, values)


@frappe.whitelist()
def save_procedure_variables(
	clinical_procedure: str, procedure_template: str, values: str | dict[str, Any] | None = None
):
	"""Store one set of variable values for a template against a Clinical Procedure.

	Rows for other templates on the same procedure are left alone, so a drawing that tags
	several templates keeps each one's answers.
	"""
	_ensure_clinical_access()
	if not clinical_procedure or not frappe.db.exists("Clinical Procedure", clinical_procedure):
		frappe.throw(_("Clinical Procedure not found."))
	_ensure_owner_open("Clinical Procedure", clinical_procedure)
	if not procedure_template or not frappe.db.exists("Clinical Procedure Template", procedure_template):
		frappe.throw(_("Clinical Procedure Template not found."))
	if not _has_procedure_variables():
		frappe.throw(_("This site cannot store per-procedure variables yet. Run bench migrate."))

	# No silent fallback: an unreadable payload here would wipe the procedure's answers.
	parsed = values if isinstance(values, dict) else _parse_json(values, None)
	if not isinstance(parsed, dict):
		frappe.throw(_("Procedure variables must be a set of fieldname and value pairs."))

	doc = frappe.get_doc("Clinical Procedure", clinical_procedure)
	kept = [
		row
		for row in (doc.get(PROCEDURE_VARIABLES_FIELD) or [])
		if row.procedure_template != procedure_template
	]
	doc.set(PROCEDURE_VARIABLES_FIELD, [])
	for row in kept:
		doc.append(PROCEDURE_VARIABLES_FIELD, row)
	for fieldname, value in parsed.items():
		resolved = _variable_fieldname(fieldname)
		if not resolved:
			continue
		doc.append(
			PROCEDURE_VARIABLES_FIELD,
			{
				"procedure_template": procedure_template,
				"fieldname": resolved,
				"label": frappe.unscrub(resolved),
				"value": _stringify_variable_value(value),
			},
		)
	doc.flags.ignore_mandatory = True
	doc.flags.ignore_validate_update_after_submit = True
	doc.save(ignore_permissions=True)
	return _procedure_level_variables(clinical_procedure, procedure_template)


def _default_derma_variable(fieldname: str) -> dict[str, Any] | None:
	labels = {
		"product_item": ("Product Item", "Data", ""),
		"product_name": ("Product / Device", "Data", ""),
		"dose": ("Dose / Quantity", "Float", ""),
		"dose_unit": ("Dose Unit", "Select", "Units\nml\nmg\npass\nJ/cm2\nHz\nOther"),
		"lot_no": ("Lot No", "Data", ""),
		"expiry_date": ("Expiry Date", "Date", ""),
		"device": ("Device", "Data", ""),
		"settings": ("Settings", "Small Text", ""),
		"passes": ("Passes", "Int", ""),
		"fluence": ("Fluence", "Data", ""),
		"spot_size": ("Spot Size", "Data", ""),
		"pulse_duration": ("Pulse Duration", "Data", ""),
		"repetition_rate": ("Repetition Rate", "Data", ""),
		"no_of_pulses": ("No Of Pulses", "Int", ""),
		"diagnosis": ("Diagnosis", "Data", ""),
		"severity": ("Severity", "Select", "Mild\nModerate\nSevere"),
		"status": ("Status", "Select", "Active\nImproving\nStable\nResolved\nFollow-up"),
		"lesion_id": ("Lesion ID", "Data", ""),
		"body_region": ("Body Region / Site", "Data", ""),
		"plane": ("Plane", "Select", "Intradermal\nSubdermal\nSubcutaneous\nSupraperiosteal"),
		"technique": (
			"Technique",
			"Select",
			"Needle\nCannula\nBolus\nLinear threading\nFanning\nSerial puncture",
		),
	}
	if fieldname not in labels:
		return None
	label, fieldtype, options = labels[fieldname]
	return {
		"fieldname": fieldname,
		"label": label,
		"fieldtype": fieldtype,
		"options": options,
		"required": False,
		"source": "Derma Template",
	}


def _get_clinical_procedure_encounter_field() -> str | None:
	for fieldname in ["patient_encounter", "custom_patient_encounter", "encounter"]:
		if _has_field("Clinical Procedure", fieldname):
			return fieldname
	return None


def _get_derma_procedures(
	patient: str, appointment: str | None = None, encounter: str | None = None
) -> list[dict[str, Any]]:
	meta = frappe.get_meta("Clinical Procedure")
	fields = _select_existing_fields(
		"Clinical Procedure",
		[
			"name",
			"docstatus",
			"patient",
			"appointment",
			"procedure_template",
			"title",
			"status",
			"start_date",
			"practitioner",
			"practitioner_name",
			"medical_department",
			"notes",
			"creation",
			"modified",
			"patient_encounter",
			"custom_patient_encounter",
			"encounter",
			"custom_derma_price_list",
			"custom_derma_price_override",
			"custom_derma_no_charge",
			"custom_derma_price_override_reason",
		],
	)
	filters: dict[str, Any] = {}
	if meta.has_field("patient"):
		filters["patient"] = patient
	if appointment and meta.has_field("appointment"):
		filters["appointment"] = appointment
	encounter_field = _get_clinical_procedure_encounter_field()
	if encounter and encounter_field:
		filters[encounter_field] = encounter
	if not filters:
		return []

	rows = frappe.get_all(
		"Clinical Procedure",
		filters=filters,
		fields=fields,
		order_by="modified desc",
		limit=200,
	)
	procedure_names = [row.get("name") for row in rows if row.get("name")]
	template_names = [row.get("procedure_template") for row in rows if row.get("procedure_template")]
	if template_names:
		template_labels = frappe.get_all(
			"Clinical Procedure Template",
			filters={"name": ["in", template_names]},
			fields=_select_existing_fields(
				"Clinical Procedure Template", ["name", "template", "custom_derma_category"]
			),
		)
		template_map = {row.name: row for row in template_labels}
		for row in rows:
			template = template_map.get(row.get("procedure_template"))
			if template:
				row["template_label"] = template.get("template") or template.get("name")
				row["derma_category"] = template.get("custom_derma_category")
	if procedure_names:
		_enrich_derma_procedure_rows(rows, procedure_names)
	return rows


def _enrich_derma_procedure_rows(rows: list[dict[str, Any]], procedure_names: list[str]) -> None:
	marks_by_procedure: dict[str, list[dict[str, Any]]] = {}
	if _has_doctype("Derma Chart Mark"):
		mark_rows = frappe.get_all(
			"Derma Chart Mark",
			filters={"clinical_procedure": ["in", procedure_names]},
			fields=_select_existing_fields(
				"Derma Chart Mark",
				[
					"name",
					"clinical_procedure",
					# What the chart heads a mark's materials with - the autoname names nothing
					# a practitioner can find on the drawing.
					"sequence",
					"procedure_template",
					"category",
					"body_view",
					"body_region",
					"region_label",
					"product_name",
					"dose",
					"dose_unit",
					"device",
					"settings",
					"passes",
					"lot_no",
					"expiry_date",
					"diagnosis",
					"severity",
					"status",
					"photo_set",
					"annotation",
				],
			),
			order_by="sequence asc, modified asc",
			limit=1000,
		)
		consumable_marks.hydrate(mark_rows)
		for mark in mark_rows:
			marks_by_procedure.setdefault(mark.get("clinical_procedure"), []).append(mark)

	treatments_by_procedure: dict[str, list[dict[str, Any]]] = {}
	if _has_doctype("Derma Treatment Entry"):
		treatment_rows = frappe.get_all(
			"Derma Treatment Entry",
			filters={"clinical_procedure": ["in", procedure_names]},
			fields=_select_existing_fields(
				"Derma Treatment Entry",
				[
					"name",
					"clinical_procedure",
					"procedure_type",
					"body_view",
					"body_region",
					"region_label",
					"product_name",
					"dose",
					"dose_unit",
					"device",
					"settings",
					"lot_no",
					"expiry_date",
					"photo_set",
					"annotation",
				],
			),
			order_by="modified asc",
			limit=500,
		)
		for treatment in treatment_rows:
			treatments_by_procedure.setdefault(treatment.get("clinical_procedure"), []).append(treatment)

	for row in rows:
		marks = marks_by_procedure.get(row.get("name"), [])
		treatments = treatments_by_procedure.get(row.get("name"), [])
		row["derma_marks"] = marks
		row["derma_treatments"] = treatments
		row["mark_count"] = len(marks)
		row["photo_count"] = len(
			{
				value
				for value in [
					*(mark.get("photo_set") for mark in marks),
					*(treatment.get("photo_set") for treatment in treatments),
				]
				if value
			}
		)
		row["annotation_count"] = len(
			{
				value
				for value in [
					*(mark.get("annotation") for mark in marks),
					*(treatment.get("annotation") for treatment in treatments),
				]
				if value
			}
		)
		row["derma_detail_text"] = _procedure_history_detail(row, marks, treatments)
		row["derma_artifact_text"] = _procedure_artifact_text(row)

	annotation_map = _get_annotation_counts_for_procedures(procedure_names)
	for row in rows:
		annotation_count = annotation_map.get(row.get("name"), 0)
		if annotation_count:
			row["annotation_count"] = max(cint(row.get("annotation_count") or 0), annotation_count)
			row["derma_artifact_text"] = _procedure_artifact_text(row)

	_hydrate_procedure_variables(rows, procedure_names)
	consumable_procedures.hydrate(rows)


def _hydrate_procedure_variables(rows: list[dict[str, Any]], procedure_names: list[str]) -> None:
	"""The shared values each procedure holds for its own template, ready for the row to show.

	Read in one query rather than per row: a visit's procedure list is capped at 200, and asking
	`_procedure_level_variables` for each would be that many round trips for a chart the
	clinician is waiting on.
	"""
	for row in rows:
		row.setdefault("derma_procedure_variables", [])
		row.setdefault("derma_procedure_variables_text", "")
		row.setdefault("derma_captures_variables_per_procedure", 0)
	if not procedure_names or not _has_procedure_variables():
		return
	# The flag decides whether the row offers the editor at all, and it is what makes the
	# offer appear on a procedure that has recorded nothing yet.
	templates = {row.get("procedure_template") for row in rows if row.get("procedure_template")}
	flagged = set()
	if templates and _has_field("Clinical Procedure Template", "custom_derma_variables_per_procedure"):
		flagged = {
			entry.name
			for entry in frappe.get_all(
				"Clinical Procedure Template",
				filters={"name": ["in", list(templates)], "custom_derma_variables_per_procedure": 1},
				fields=["name"],
				limit=0,
			)
		}
	for row in rows:
		row["derma_captures_variables_per_procedure"] = int(row.get("procedure_template") in flagged)
	stored = frappe.get_all(
		"Derma Procedure Variable",
		filters={"parent": ["in", procedure_names], "parenttype": "Clinical Procedure"},
		fields=["parent", "procedure_template", "fieldname", "label", "value"],
		order_by="parent asc, idx asc",
		limit=0,
	)
	by_procedure: dict[tuple[str, str], list[dict[str, Any]]] = {}
	for entry in stored:
		by_procedure.setdefault((entry.parent, entry.procedure_template), []).append(
			{"fieldname": entry.fieldname, "label": entry.label, "value": entry.value}
		)
	for row in rows:
		values = by_procedure.get((row.get("name"), row.get("procedure_template")), [])
		if not values:
			continue
		row["derma_procedure_variables"] = values
		row["derma_procedure_variables_text"] = " · ".join(
			f"{value['label'] or value['fieldname']}: {value['value']}" for value in values if value["value"]
		)


def _procedure_history_detail(
	row: dict[str, Any], marks: list[dict[str, Any]], treatments: list[dict[str, Any]]
) -> str:
	source = marks or treatments
	if not source:
		return row.get("notes") or ""
	category = row.get("derma_category") or next(
		(
			item.get("category") or item.get("procedure_type")
			for item in source
			if item.get("category") or item.get("procedure_type")
		),
		"",
	)
	locations = _join_unique(
		item.get("region_label") or item.get("body_region") or item.get("body_view") for item in source
	)
	product = next(
		(
			item.get("product_name") or item.get("device")
			for item in source
			if item.get("product_name") or item.get("device")
		),
		"",
	)
	dose_total = sum(flt(item.get("dose") or 0) for item in source)
	dose_unit = next((item.get("dose_unit") for item in source if item.get("dose_unit")), "")
	settings = next((item.get("settings") for item in source if item.get("settings")), "")
	diagnosis = _join_unique(item.get("diagnosis") for item in source)
	parts = [
		_("{0} mark(s)").format(len(marks)) if marks else "",
		category,
		locations,
		product,
		f"{dose_total:g} {dose_unit}".strip() if dose_total else "",
		settings,
		diagnosis,
	]
	return " · ".join(part for part in parts if part)


def _procedure_artifact_text(row: dict[str, Any]) -> str:
	parts = []
	if row.get("photo_count"):
		parts.append(_("{0} photo set(s)").format(row.get("photo_count")))
	if row.get("annotation_count"):
		parts.append(_("{0} drawing(s)").format(row.get("annotation_count")))
	return " · ".join(parts)


def _get_annotation_counts_for_procedures(procedure_names: list[str]) -> dict[str, int]:
	if not procedure_names or not _has_doctype("Health Annotation Table"):
		return {}
	rows = frappe.get_all(
		"Health Annotation Table",
		filters={"parenttype": "Clinical Procedure", "parent": ["in", procedure_names]},
		fields=["parent", "annotation"],
		limit=1000,
	)
	counts: dict[str, set[str]] = {}
	for row in rows:
		if row.get("parent") and row.get("annotation"):
			counts.setdefault(row.get("parent"), set()).add(row.get("annotation"))
	return {key: len(value) for key, value in counts.items()}


def _get_derma_photo_sets(
	patient: str, appointment: str | None = None, encounter: str | None = None
) -> list[dict[str, Any]]:
	if not _has_doctype("Derma Photo Set"):
		return []
	rows = frappe.get_all(
		"Derma Photo Set",
		filters=_base_filters(patient, appointment=appointment, encounter=encounter),
		fields=_select_existing_fields(
			"Derma Photo Set",
			[
				"name",
				"patient",
				"appointment",
				"encounter",
				"clinical_procedure",
				"set_type",
				"body_view",
				"body_region",
				"finding",
				"treatment_entry",
				"notes",
				"modified",
				"creation",
			],
		),
		order_by="modified desc",
		limit=50,
	)
	return _hydrate_photo_sets(rows)


def _get_previous_photo_sets(patient: str, current_encounter: str | None = None) -> list[dict[str, Any]]:
	if not _has_doctype("Derma Photo Set"):
		return []
	filters: dict[str, Any] = {"patient": patient}
	if current_encounter:
		filters["encounter"] = ["!=", current_encounter]
	rows = frappe.get_all(
		"Derma Photo Set",
		filters=filters,
		fields=_select_existing_fields(
			"Derma Photo Set",
			[
				"name",
				"patient",
				"appointment",
				"encounter",
				"clinical_procedure",
				"set_type",
				"body_view",
				"body_region",
				"finding",
				"treatment_entry",
				"notes",
				"modified",
				"creation",
			],
		),
		order_by="modified desc",
		limit=80,
	)
	return _hydrate_photo_sets(rows)


def _hydrate_photo_sets(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
	if not rows:
		return []
	parent_names = [row.get("name") for row in rows if row.get("name")]
	if not parent_names:
		return rows
	photos_by_parent: dict[str, list[dict[str, Any]]] = {}
	child_rows = frappe.get_all(
		"Derma Photo",
		filters={"parent": ["in", parent_names], "parenttype": "Derma Photo Set"},
		fields=[
			"name",
			"parent",
			"image",
			"photo_type",
			"view",
			"body_region",
			"finding",
			"treatment_entry",
			"notes",
			"idx",
		],
		order_by="parent asc, idx asc",
		limit=500,
	)
	for photo in child_rows:
		photos_by_parent.setdefault(photo.parent, []).append(photo)
	for row in rows:
		row["photos"] = photos_by_parent.get(row.get("name"), [])
		row["preview_image"] = row["photos"][0].get("image") if row["photos"] else ""
	return rows


def _load_derma_annotation_context(
	encounter: str | None = None, procedure_names: list[str] | None = None
) -> dict[str, Any]:
	"""This encounter's drawings and its procedures', never another visit's."""
	procedure_names = [name for name in (procedure_names or []) if name]
	parents: list[tuple[str, str]] = []
	if encounter:
		parents.append(("Patient Encounter", encounter))
	parents.extend(("Clinical Procedure", name) for name in procedure_names)

	rows = _load_annotations_for_parents(parents)

	encounter_annotations = [
		row
		for row in rows
		if row.get("source_doctype") == "Patient Encounter" or not row.get("source_doctype")
	]
	procedure_annotations: dict[str, list[dict[str, Any]]] = {}
	for row in rows:
		if row.get("source_doctype") == "Clinical Procedure" and row.get("source_name"):
			procedure_annotations.setdefault(row.get("source_name"), []).append(row)

	return {
		"annotations": rows,
		"encounter_annotations": encounter_annotations,
		"procedure_annotations": procedure_annotations,
		"latest_annotation": rows[0] if rows else None,
	}


def _load_annotations_for_parents(
	parents: list[tuple[str, str]], include_scene: bool = True
) -> list[dict[str, Any]]:
	"""Annotations hanging off the given parents. `include_scene` drops the `json` column, which
	averages 35 KB a row and is dead weight for anything that only lists them."""
	if not parents or not _has_doctype("Health Annotation Table") or not _has_doctype("Health Annotation"):
		return []
	parent_names = [name for _, name in parents if name]
	parent_types = [doctype for doctype, _ in parents if doctype]
	if not parent_names or not parent_types:
		return []
	child_fields = _select_existing_fields(
		"Health Annotation Table",
		["parent", "parenttype", "annotation", "type", "annotation_data", "creation", "idx"],
	)
	child_rows = frappe.get_all(
		"Health Annotation Table",
		filters={"parent": ["in", parent_names], "parenttype": ["in", list(set(parent_types))]},
		fields=child_fields,
		order_by="creation desc, idx desc",
		limit=1000,
	)
	annotation_names = [row.get("annotation") for row in child_rows if row.get("annotation")]
	if not annotation_names:
		return []
	wanted = [
		"name",
		"annotation_template",
		"custom_derma_body_template_title",
		"image",
		"json",
		"creation",
		"modified",
	]
	if not include_scene:
		wanted.remove("json")
	annotation_fields = _select_existing_fields("Health Annotation", wanted)
	annotations = {
		row.name: row
		for row in frappe.get_all(
			"Health Annotation",
			filters={"name": ["in", annotation_names]},
			fields=annotation_fields,
			limit=1000,
		)
	}
	rows = []
	seen = set()
	for child in child_rows:
		annotation_name = child.get("annotation")
		if not annotation_name or annotation_name in seen or annotation_name not in annotations:
			continue
		seen.add(annotation_name)
		annotation = dict(annotations[annotation_name])
		annotation["source_doctype"] = child.get("parenttype")
		annotation["source_name"] = child.get("parent")
		annotation["annotation_context"] = child.get("type")
		annotation["annotation_data"] = child.get("annotation_data")
		rows.append(annotation)
	rows.sort(key=lambda row: row.get("creation") or row.get("modified") or "", reverse=True)
	return rows


def _resolve_patient_encounter_doc(
	encounter: str | None = None,
	appointment: str | None = None,
	patient: str | None = None,
	ptype: str = "read",
):
	if encounter:
		doc = frappe.get_doc("Patient Encounter", encounter)
	else:
		filters: dict[str, Any] = {}
		if appointment:
			filters["appointment"] = appointment
		if patient:
			filters["patient"] = patient
		if not filters:
			return None
		name = frappe.db.get_value("Patient Encounter", filters, "name", order_by="creation desc")
		if not name:
			return None
		doc = frappe.get_doc("Patient Encounter", name)

	if ptype and not doc.has_permission(ptype):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	return doc


def _parse_payload(value: Any) -> Any:
	if isinstance(value, str):
		try:
			return json.loads(value)
		except Exception:
			return None
	return value


def _drug_prescription_fields() -> set[str]:
	if not _has_doctype("Drug Prescription"):
		return set()
	return {
		df.fieldname
		for df in frappe.get_meta("Drug Prescription").fields
		if df.fieldname and df.fieldname not in CHILD_INTERNAL_FIELDS
	}


def _drug_prescription_row(source: Any, allowed: set[str]) -> dict[str, Any]:
	"""One row, whether it arrives as a saved child document or a payload dict."""
	values = source if isinstance(source, dict) else source.as_dict()
	return {key: values.get(key) for key in allowed if key in values}


def _validate_prescription_rows(rows: list[dict[str, Any]]) -> None:
	"""Refuse a repeat count no clinician means, which a typo in the wrong column produces."""
	for index, row in enumerate(rows, start=1):
		repeats = flt(row.get("number_of_repeats_allowed") or 0)
		if repeats < 0 or repeats > MAX_PRESCRIPTION_REPEATS:
			frappe.throw(
				_("Row {0}: repeats must be between 0 and {1}.").format(index, MAX_PRESCRIPTION_REPEATS),
				frappe.ValidationError,
			)


def _merge_ordered_prescriptions(
	existing: list[dict[str, Any]], incoming: list[dict[str, Any]]
) -> list[dict[str, Any]]:
	"""Rows already ordered as a Medication Request are kept as stored, ahead of the new ones."""
	ordered = [row for row in existing if row.get("medication_request")]
	known = {row["medication_request"] for row in ordered}
	fresh = []
	for row in incoming:
		request = row.get("medication_request")
		if request and request not in known:
			frappe.throw(
				_("Medication Request {0} does not belong to this encounter.").format(request),
				frappe.ValidationError,
			)
		if not request:
			fresh.append(row)
	return [*ordered, *fresh]


def _drug_prescription_rows(encounter_doc) -> list[dict[str, Any]]:
	if not _has_field("Patient Encounter", "drug_prescription"):
		return []
	allowed = _drug_prescription_fields()
	return [_drug_prescription_row(row, allowed) for row in encounter_doc.get("drug_prescription") or []]


def _get_derma_prescription_count(encounter: str | None) -> int:
	"""How many drugs this visit prescribed. The chart badges the Prescription tab with this;
	the rows themselves stay behind `get_derma_prescriptions`, which the tab loads on demand."""
	if not encounter or not _has_field("Patient Encounter", "drug_prescription"):
		return 0
	return frappe.db.count(
		"Drug Prescription", {"parent": encounter, "parenttype": "Patient Encounter"}
	)


def _clinical_procedure_context_filters(
	encounter: str | None = None, appointment: str | None = None, patient: str | None = None
) -> dict[str, Any]:
	meta = frappe.get_meta("Clinical Procedure")
	filters: dict[str, Any] = {}
	encounter_field = _get_clinical_procedure_encounter_field()
	if encounter and encounter_field:
		filters[encounter_field] = encounter
	if appointment and meta.has_field("appointment"):
		filters["appointment"] = appointment
	if patient and meta.has_field("patient"):
		filters["patient"] = patient
	return filters


def _get_marks(
	patient: str, appointment: str | None = None, encounter: str | None = None
) -> list[dict[str, Any]]:
	if not _has_doctype("Derma Chart Mark"):
		return []

	filters = _base_filters(patient, appointment=appointment, encounter=encounter)
	marks = frappe.get_all(
		"Derma Chart Mark",
		filters=filters,
		fields=_select_existing_fields("Derma Chart Mark", DERMA_MARK_FIELDS),
		order_by="modified desc",
		limit=500,
	)
	_hydrate_mark_area_variables(marks)
	_merge_procedure_level_variables(marks)
	consumable_marks.hydrate(marks)
	return marks


@frappe.whitelist()
def get_inventory_readiness(
	patient: str | None = None, encounter: str | None = None, appointment: str | None = None
) -> list[dict[str, Any]]:
	_ensure_clinical_access()
	# Imported here rather than at module scope: the readiness engines read this module's
	# schema helpers, so the package must not be pulled in while api.py is still loading.
	from do_derma.readiness import inventory

	if not patient and encounter:
		patient = frappe.db.get_value("Patient Encounter", encounter, "patient")
	if not patient:
		return []
	return inventory.build(
		_get_marks(patient, appointment=appointment, encounter=encounter),
		consumable_procedures.get_carriers(
			_get_derma_procedures(patient, appointment=appointment, encounter=encounter)
		),
	)


def _get_previous_marks(patient: str, current_encounter: str | None = None) -> list[dict[str, Any]]:
	if not _has_doctype("Derma Chart Mark"):
		return []
	filters: dict[str, Any] = {"patient": patient}
	if current_encounter:
		filters["encounter"] = ["!=", current_encounter]
	rows = frappe.get_all(
		"Derma Chart Mark",
		filters=filters,
		fields=_select_existing_fields("Derma Chart Mark", [*DERMA_MARK_FIELDS, "creation"]),
		order_by="modified desc",
		limit=500,
	)
	visible = [row for row in rows if row.get("status") != "Archived"]
	consumable_marks.hydrate(visible)
	return visible


def _category_defaults(category: str | None) -> dict[str, Any]:
	if not category or not frappe.db.exists("Derma Procedure Category", category):
		return {}
	fields = [
		"marker_behavior",
		"marker_color",
		"marker_label",
		"default_body_template",
		"note_sentence_template",
		"workflow",
	]
	return frappe.db.get_value("Derma Procedure Category", category, fields, as_dict=True) or {}


def _template_defaults(template: str | None) -> dict[str, Any]:
	if not template or not frappe.db.exists("Clinical Procedure Template", template):
		return {}
	fields = _select_existing_fields(
		"Clinical Procedure Template",
		[
			"item",
			"custom_derma_category",
			"custom_derma_allowed_body_templates",
			"custom_derma_marker_behavior",
			"custom_derma_marker_color",
			MARKER_SIZE_FIELD,
			"custom_derma_note_template",
			"custom_derma_required_fields",
		],
	)
	return frappe.db.get_value("Clinical Procedure Template", template, fields, as_dict=True) or {}


@frappe.whitelist()
def ensure_chart_context(
	patient: str | None = None, appointment: str | None = None, encounter: str | None = None
):
	_ensure_clinical_access()
	"""Ensure an active Patient Encounter and return canonical derma chart route context."""

	context = _get_visit_context(patient=patient, appointment=appointment, encounter=encounter)
	return {
		"patient": context["patient_id"],
		"appointment": context["appointment_id"],
		"encounter": context["encounter_id"],
		"route": "derma-chart",
	}


@frappe.whitelist()
def get_chart_context(
	patient: str | None = None, appointment: str | None = None, encounter: str | None = None
):
	_ensure_clinical_access()
	context = _get_visit_context(patient=patient, appointment=appointment, encounter=encounter)
	patient_id = context["patient_id"]
	appointment_id = context["appointment_id"]
	encounter_id = context["encounter_id"]

	filters = _base_filters(patient_id, appointment=appointment_id, encounter=encounter_id)
	findings = frappe.get_all(
		"Derma Finding",
		filters=filters,
		fields=_select_existing_fields("Derma Finding", DERMA_FINDING_FIELDS),
		order_by="modified desc",
		limit=200,
	)
	treatments = frappe.get_all(
		"Derma Treatment Entry",
		filters=filters,
		fields=_select_existing_fields("Derma Treatment Entry", DERMA_TREATMENT_FIELDS),
		order_by="modified desc",
		limit=200,
	)
	photo_sets = frappe.get_all(
		"Derma Photo Set",
		filters=filters,
		fields=_select_existing_fields(
			"Derma Photo Set",
			[
				"name",
				"clinical_procedure",
				"set_type",
				"body_view",
				"body_region",
				"finding",
				"treatment_entry",
				"notes",
				"modified",
			],
		),
		order_by="modified desc",
		limit=50,
	)
	templates = frappe.get_all(
		"Derma Chart Template",
		filters={"disabled": 0},
		fields=[
			"name",
			"title",
			"workflow",
			"body_view",
			"default_finding_type",
			"narrative_template",
			"findings_json",
			"treatments_json",
		],
		order_by="title asc",
		limit=100,
	)

	for template in templates:
		template["findings"] = _parse_json(template.pop("findings_json", None), [])
		template["treatments"] = _parse_json(template.pop("treatments_json", None), [])

	return {
		**context,
		"findings": findings,
		"treatments": treatments,
		"photo_sets": photo_sets,
		"templates": templates,
		"marks": _get_marks(patient_id, appointment=appointment_id, encounter=encounter_id),
		"categories": _get_categories(),
		"body_templates": _get_body_templates(),
		"template_sets": _get_template_sets(),
		"procedure_templates": _get_derma_procedure_templates(),
		"timeline": get_patient_timeline(patient_id, current_encounter=encounter_id),
		"narrative": build_visit_narrative(findings, treatments),
		"voice_scribe_enabled": voice.is_enabled(),
		"voice_scribe": voice.client_config(),
		"letterhead_logo": letterhead.get_logo_url(
			(context["encounter"] or {}).get("practitioner_name") or (context["appointment"] or {}).get("practitioner_name")
		),
	}


def get_session_readiness(
	patient: str | None, appointment: str | None = None, encounter: str | None = None
) -> dict[str, Any]:
	"""This module's two readers of readiness - the chart payload and the completion gate -
	ask here; `do_derma.readiness.session` owns the answer."""
	from do_derma.readiness import session as readiness_session

	return readiness_session.get_session_readiness(patient, appointment=appointment, encounter=encounter)


def _add_submitted_invoices(procedures: list[dict[str, Any]]) -> None:
	"""Stamp each procedure row with the Sales Invoice that bills it, or an empty string."""
	for row in procedures:
		row.setdefault("submitted_invoice", "")
	invoices = reopen.get_submitted_invoices(
		[row.get("clinical_procedure") or row.get("name") for row in procedures if row.get("name")]
	)
	for row in procedures:
		row["submitted_invoice"] = invoices.get(row.get("clinical_procedure") or row.get("name"), "")


@frappe.whitelist()
def get_patient_derma_chart(
	patient_id: str | None = None, encounter: str | None = None, appointment: str | None = None
):
	_ensure_clinical_access()
	"""Dental-shaped derma chart context for /app/derma-chart."""

	context = _get_visit_context(patient=patient_id, appointment=appointment, encounter=encounter)
	patient = context["patient_id"]
	appointment_id = context["appointment_id"]
	encounter_id = context["encounter_id"]
	context_errors: list[str] = []

	def section(label: str, fallback: Any, getter):
		return _safe_derma_context(label, fallback, getter, errors=context_errors)

	procedures = section(
		"procedures",
		[],
		lambda: _get_derma_procedures(patient, appointment=appointment_id, encounter=encounter_id),
	)
	section("submitted invoices", None, lambda: _add_submitted_invoices(procedures))
	annotation_context = section(
		"annotations",
		{
			"annotations": [],
			"encounter_annotations": [],
			"procedure_annotations": {},
			"latest_annotation": None,
		},
		lambda: _load_derma_annotation_context(
			encounter=encounter_id,
			procedure_names=[row.get("name") for row in procedures],
		),
	)

	return {
		**context,
		"voice_scribe_enabled": voice.is_enabled(),
		"voice_scribe": voice.client_config(),
		"procedure_templates": section("procedure templates", [], _get_derma_procedure_templates),
		"procedures": procedures,
		"annotations": annotation_context["annotations"],
		"encounter_annotations": annotation_context["encounter_annotations"],
		"procedure_annotations": annotation_context["procedure_annotations"],
		"latest_annotation": annotation_context["latest_annotation"],
		"body_templates": section("body templates", [], _get_body_templates),
		"template_sets": section("template sets", [], _get_template_sets),
		"photo_sets": section(
			"photo sets",
			[],
			lambda: _get_derma_photo_sets(patient, appointment=appointment_id, encounter=encounter_id),
		),
		"previous_photo_sets": section(
			"previous photo sets",
			[],
			lambda: _get_previous_photo_sets(patient, current_encounter=encounter_id),
		),
		"prescription_count": section(
			"prescription count", 0, lambda: _get_derma_prescription_count(encounter_id)
		),
		"marks": section(
			"marks",
			[],
			lambda: _get_marks(patient, appointment=appointment_id, encounter=encounter_id),
		),
		"previous_marks": section(
			"previous marks",
			[],
			lambda: _get_previous_marks(patient, current_encounter=encounter_id),
		),
		"categories": section("categories", [], _get_categories),
		"timeline": section(
			"patient timeline",
			[],
			lambda: get_patient_timeline(patient, current_encounter=encounter_id),
		),
		"visit_timeline": section(
			"visit timeline",
			[],
			lambda: get_visit_timeline(patient, current_encounter=encounter_id),
		),
		"readiness": section(
			"readiness",
			{"items": [], "blockers": [], "enforcement": ENFORCEMENT_WARN},
			lambda: get_session_readiness(patient, appointment=appointment_id, encounter=encounter_id),
		),
		"visit_summary": section(
			"visit summary",
			"",
			lambda: generate_visit_summary(encounter_id, patient),
		),
		"settings": get_feature_toggles(),
		"context_errors": context_errors,
		"latest_encounter": previous_visits.get_latest_encounter(patient) if patient else None,
		**_get_visit_moment_fields(encounter_id),
		"permissions": {
			"can_reopen_encounter": bool(encounter_id)
			and bool(frappe.has_permission("Patient Encounter", "cancel", doc=encounter_id)),
			"can_reopen_procedure": bool(frappe.has_permission("Clinical Procedure", "cancel")),
		},
	}


@frappe.whitelist()
def create_derma_chart_procedure(payload: str | dict[str, Any]):
	_ensure_clinical_access()
	values = json.loads(payload) if isinstance(payload, str) else dict(payload or {})
	patient = values.get("patient")
	encounter = values.get("encounter")
	procedure_template = values.get("procedure_template")
	if not patient:
		frappe.throw(_("Patient is required."))
	if not encounter:
		frappe.throw(_("An active Patient Encounter is required to create a Clinical Procedure."))
	if not procedure_template:
		frappe.throw(_("Clinical Procedure Template is required."))

	encounter_doc = frappe.get_doc("Patient Encounter", encounter)
	_ensure_encounter_open(encounter)
	appointment = values.get("appointment") or encounter_doc.get("appointment")
	template_doc = frappe.get_doc("Clinical Procedure Template", procedure_template)
	procedure = frappe.new_doc("Clinical Procedure")
	procedure.patient = patient
	if _has_field("Clinical Procedure", "patient_name"):
		procedure.patient_name = frappe.db.get_value("Patient", patient, "patient_name")
	if appointment and _has_field("Clinical Procedure", "appointment"):
		procedure.appointment = appointment
	if _has_field("Clinical Procedure", "procedure_template"):
		procedure.procedure_template = procedure_template
	if _has_field("Clinical Procedure", "title"):
		procedure.title = template_doc.get("template") or template_doc.name
	if _has_field("Clinical Procedure", "status"):
		procedure.status = "Draft"
	if _has_field("Clinical Procedure", "start_date"):
		procedure.start_date = nowdate()
	encounter_field = _get_clinical_procedure_encounter_field()
	if encounter_field:
		procedure.set(encounter_field, encounter)
	if _has_field("Clinical Procedure", "practitioner"):
		procedure.practitioner = encounter_doc.get("practitioner")
	if _has_field("Clinical Procedure", "practitioner_name"):
		procedure.practitioner_name = encounter_doc.get("practitioner_name")
	if _has_field("Clinical Procedure", "medical_department"):
		procedure.medical_department = template_doc.get("medical_department") or encounter_doc.get(
			"medical_department"
		)
	procedure_notes = _append_body_template_note(_append_variable_note(values.get("notes"), values), values)
	if _has_field("Clinical Procedure", "notes") and procedure_notes:
		procedure.notes = procedure_notes
	source_mark = values.get("mark")
	mark_doc = (
		frappe.get_doc("Derma Chart Mark", source_mark)
		if source_mark and frappe.db.exists("Derma Chart Mark", source_mark)
		else None
	)
	if mark_doc:
		consumable_marks.apply_to_procedure(procedure, mark_doc)
	procedure.insert(ignore_permissions=True)

	if mark_doc:
		# The mark that gave the procedure its materials owns them from here, and only the
		# link on the mark says so.
		mark_doc.clinical_procedure = procedure.name
		mark_doc.save(ignore_permissions=True)

	treatment = None
	if _has_doctype("Derma Treatment Entry") and _has_derma_treatment_data(values):
		treatment = frappe.new_doc("Derma Treatment Entry")
		treatment.patient = patient
		treatment.patient_name = frappe.db.get_value("Patient", patient, "patient_name")
		treatment.appointment = appointment
		treatment.encounter = encounter
		if _has_field("Derma Treatment Entry", "clinical_procedure"):
			treatment.clinical_procedure = procedure.name
		category = template_doc.get("custom_derma_category") or values.get("category") or "Other"
		treatment.workflow = "Aesthetic" if category in {"Botox", "Filler", "Laser", "Peel"} else "Medical"
		treatment.procedure_type = _treatment_procedure_type(category)
		treatment.body_view = values.get("body_view")
		treatment.body_region = _normalize_derma_body_region(
			values.get("body_region")
			or values.get("body_template_title")
			or values.get("body_template")
			or values.get("body_view")
		)
		treatment.x_percent = flt(values.get("x_percent") or 0)
		treatment.y_percent = flt(values.get("y_percent") or 0)
		treatment.product_name = values.get("product_name") or _procedure_variable(
			values, "product_name", "injectable", "product", "device"
		)
		dose_value = values.get("dose") or _procedure_variable(values, "dose", "units", "ml", "quantity")
		treatment.dose = _coerce_dose(dose_value)
		treatment.dose_unit = values.get("dose_unit") or _infer_dose_unit(values, dose_value)
		treatment.lot_no = values.get("lot_no") or _procedure_variable(values, "lot_no", "lot")
		treatment.device = values.get("device") or _procedure_variable(values, "device")
		treatment.settings = values.get("settings") or _procedure_settings_from_variables(values)
		if _has_field("Derma Treatment Entry", "variables_json"):
			treatment.variables_json = json.dumps(values.get("procedure_variables") or {}, ensure_ascii=False)
		treatment.notes = values.get("notes")
		treatment.save(ignore_permissions=True)

	return {
		"clinical_procedure": procedure.as_dict(),
		"treatment_entry": treatment.as_dict() if treatment else None,
	}


def _treatment_procedure_type(category: str | None) -> str:
	"""Derma Procedure Category is clinic-defined, Derma Treatment Entry.procedure_type is a
	fixed Select. A category the Select does not offer lands on Other rather than throwing."""
	field = frappe.get_meta("Derma Treatment Entry").get_field("procedure_type")
	options = (field.options or "").split("\n") if field else []
	return category if category in options else "Other"


def _has_derma_treatment_data(values: dict[str, Any]) -> bool:
	return any(
		values.get(field)
		for field in [
			"product_name",
			"dose",
			"lot_no",
			"settings",
			"notes",
			"procedure_variables",
			"body_template",
		]
	)


def _procedure_variable(values: dict[str, Any], *keys: str) -> Any:
	variables = values.get("procedure_variables") or {}
	if not isinstance(variables, dict):
		return None
	normalized = {_variable_fieldname(key): value for key, value in variables.items()}
	for key in keys:
		value = normalized.get(_variable_fieldname(key))
		if value not in (None, ""):
			return value
	return None


def _coerce_dose(value: Any) -> float:
	if value in (None, ""):
		return 0
	return flt("".join(char for char in str(value) if char.isdigit() or char == "."))


def _infer_dose_unit(values: dict[str, Any], dose_value: Any = None) -> str | None:
	dose_text = str(dose_value or "").lower()
	if _procedure_variable(values, "units") or dose_text.endswith("u"):
		return "Units"
	if _procedure_variable(values, "ml") or "ml" in dose_text:
		return "ml"
	return None


def _normalize_derma_body_region(value: Any) -> str | None:
	if value in (None, ""):
		return None
	region = str(value).strip()
	if not region:
		return None
	allowed = {
		"Head",
		"Face",
		"Scalp",
		"Neck",
		"Chest",
		"Abdomen",
		"Back",
		"Arm",
		"Hand",
		"Groin",
		"Leg",
		"Foot",
		"Other",
	}
	if region in allowed:
		return region
	normalized = region.lower()
	mappings = [
		(("face", "forehead", "cheek", "nose", "mouth", "ear", "temple", "jaw", "chin"), "Face"),
		(("scalp",), "Scalp"),
		(("neck",), "Neck"),
		(("chest", "breast"), "Chest"),
		(("abdomen", "belly", "stomach"), "Abdomen"),
		(("back",), "Back"),
		(("arm", "elbow", "shoulder"), "Arm"),
		(("hand", "finger", "palm"), "Hand"),
		(("groin", "genital"), "Groin"),
		(("leg", "thigh", "knee", "calf"), "Leg"),
		(("foot", "feet", "toe"), "Foot"),
		(("head",), "Head"),
	]
	for tokens, mapped_region in mappings:
		if any(token in normalized for token in tokens):
			return mapped_region
	return "Other"


def _procedure_settings_from_variables(values: dict[str, Any]) -> str:
	variables = values.get("procedure_variables") or {}
	if not isinstance(variables, dict):
		return ""
	ignored = {"product_name", "injectable", "lot_no", "lot", "dose", "units", "ml", "quantity"}
	parts = []
	for key, value in variables.items():
		if value in (None, "") or _variable_fieldname(key) in ignored:
			continue
		parts.append(f"{key}: {value}")
	return "; ".join(parts)


def _append_variable_note(notes: str | None, values: dict[str, Any]) -> str:
	settings = _procedure_settings_from_variables(values)
	if not settings:
		return notes or ""
	variable_note = _("Procedure details: {0}.").format(settings)
	if not notes:
		return variable_note
	if variable_note in notes:
		return notes
	return f"{notes}\n{variable_note}"


def _append_body_template_note(notes: str | None, values: dict[str, Any]) -> str:
	body_view = values.get("body_view")
	if not body_view:
		return notes or ""
	template_note = _("Chart template: {0}.").format(body_view)
	if not notes:
		return template_note
	if template_note in notes:
		return notes
	return f"{notes}\n{template_note}"


@frappe.whitelist()
def get_derma_annotations(encounter: str | None = None, clinical_procedure: str | None = None):
	_ensure_clinical_access()
	procedure_names = [clinical_procedure] if clinical_procedure else []
	return _load_derma_annotation_context(encounter=encounter, procedure_names=procedure_names)


ANNOTATION_SUMMARY_PARENTS = ("Patient Encounter", "Clinical Procedure")


@frappe.whitelist()
def get_derma_annotation_summary(doctype: str, docname: str):
	"""Annotations on one encounter or procedure, for the desk form's toolbar button.

	Deliberately omits the scene JSON: this runs on every form refresh and the button only needs
	a thumbnail, a date and the badge legend.
	"""
	_ensure_clinical_access()
	if doctype not in ANNOTATION_SUMMARY_PARENTS:
		frappe.throw(_("Annotations are only kept on an encounter or a procedure."))
	if not docname or not frappe.db.exists(doctype, docname):
		return []

	rows = _load_annotations_for_parents([(doctype, docname)], include_scene=False)
	return [
		{
			"name": row.get("name"),
			"image": row.get("image"),
			"creation": row.get("creation"),
			"annotation_data": row.get("annotation_data"),
			"label": row.get("custom_derma_body_template_title")
			or row.get("annotation_template")
			or _("Drawing"),
		}
		for row in rows
	]


def _save_health_annotation(
	docname: str,
	doctype: str,
	annotation_template: str = "",
	annotation_name: str | None = None,
	encounter_type: str = "",
	file_data: str | None = None,
	json_text: str = "",
	annotation_type: str = "Free Drawing",
	annotation_data: str = "",
	body_template_title: str = "",
) -> str:
	"""Create/update a Health Annotation (do_health) directly - no dependency on the separate annotation app."""
	if not file_data:
		frappe.throw(_("Drawing image data is required."))
	if not file_data.startswith("data:image"):
		frappe.throw(_("Invalid drawing image data."))

	has_title_field = _has_field("Health Annotation", "custom_derma_body_template_title")
	has_annotation_data_field = _has_field("Health Annotation Table", "annotation_data")

	if annotation_name and frappe.db.exists("Health Annotation", annotation_name):
		health_annotation = frappe.get_doc("Health Annotation", annotation_name)
		health_annotation.annotation_template = annotation_template
		health_annotation.annotation_type = annotation_type
		health_annotation.json = json_text
		if has_title_field:
			health_annotation.custom_derma_body_template_title = body_template_title

		doc = frappe.get_doc(doctype, docname)
		for row in doc.get("custom_annotations", []):
			if has_annotation_data_field and row.annotation == annotation_name:
				row.annotation_data = annotation_data
				break
		doc.flags.ignore_mandatory = True
		doc.flags.ignore_validate_update_after_submit = True
		doc.save(ignore_permissions=True)
	else:
		health_annotation = frappe.new_doc("Health Annotation")
		health_annotation.annotation_type = annotation_type
		health_annotation.annotation_template = annotation_template
		health_annotation.json = json_text
		if has_title_field:
			health_annotation.custom_derma_body_template_title = body_template_title
		health_annotation.insert(ignore_permissions=True)

		doc = frappe.get_doc(doctype, docname)
		child = {
			"annotation": health_annotation.name,
			"type": encounter_type,
		}
		if has_annotation_data_field:
			child["annotation_data"] = annotation_data
		doc.append("custom_annotations", child)
		doc.flags.ignore_mandatory = True
		doc.flags.ignore_validate_update_after_submit = True
		doc.save(ignore_permissions=True)

	header, base64_data = file_data.split(",", 1)
	extension = header.split("/")[1].split(";")[0]
	file_doc = save_file(
		f"annotation.{extension}",
		base64.b64decode(base64_data),
		"Health Annotation",
		health_annotation.name,
		is_private=1,
		df="image",
	)
	health_annotation.image = file_doc.file_url
	health_annotation.save(ignore_permissions=True)
	return health_annotation.name


def _sync_chart_marks_for_annotation(
	annotation_name: str,
	scene: dict[str, Any] | None,
	patient: str | None,
	appointment: str | None,
	encounter: str | None,
	clinical_procedure: str | None,
) -> None:
	"""Fan out one Derma Chart Mark per procedure-tagged canvas element, linked back to the
	Health Annotation snapshot. Idempotent on re-save: matches existing marks to elements by
	the element's stable Excalidraw id (stored in annotation_json), so re-editing an annotation
	updates marks in place instead of duplicating them. Marks already promoted to a real
	Clinical Procedure are never auto-deleted, even if their element is later removed/untagged.
	"""
	if not annotation_name or not isinstance(scene, dict):
		return
	elements = scene.get("elements")
	if not isinstance(elements, list):
		return

	template_element = next(
		(
			el
			for el in elements
			if isinstance(el, dict)
			and (el.get("customData") or {}).get("kind") in ("derma_template", "derma_template_image")
		),
		None,
	)
	if not template_element:
		return
	tx, ty = flt(template_element.get("x")), flt(template_element.get("y"))
	tw = flt(template_element.get("width")) or 1.0
	th = flt(template_element.get("height")) or 1.0

	tagged: dict[str, dict[str, Any]] = {}
	for element in elements:
		if not isinstance(element, dict) or element.get("isDeleted"):
			continue
		custom = element.get("customData") or {}
		if custom.get("kind"):
			continue
		procedure_template = custom.get("procedure") or custom.get("type")
		element_id = element.get("id")
		if not procedure_template or not element_id:
			continue
		if not frappe.db.exists("Clinical Procedure Template", procedure_template):
			continue
		cx = flt(element.get("x")) + flt(element.get("width")) / 2
		cy = flt(element.get("y")) + flt(element.get("height")) / 2
		tagged[element_id] = {
			"procedure_template": procedure_template,
			"variables": custom.get("variables") or {},
			"x_percent": max(0.0, min(100.0, ((cx - tx) / tw) * 100)),
			"y_percent": max(0.0, min(100.0, ((cy - ty) / th) * 100)),
		}

	existing = frappe.get_all(
		"Derma Chart Mark",
		filters={"annotation": annotation_name},
		fields=["name", "annotation_json", "clinical_procedure"],
	)
	existing_by_element: dict[str, dict[str, Any]] = {}
	for row in existing:
		info = _parse_json(row.get("annotation_json"), {})
		element_id = info.get("element_id") if isinstance(info, dict) else None
		if element_id:
			existing_by_element[element_id] = row

	base_payload = {
		"patient": patient,
		"appointment": appointment,
		"encounter": encounter,
		"clinical_procedure": clinical_procedure,
		"annotation": annotation_name,
	}

	for element_id, item in tagged.items():
		mark_values = dict(base_payload)
		mark_values["name"] = existing_by_element.get(element_id, {}).get("name")
		mark_values["procedure_template"] = item["procedure_template"]
		mark_values["x_percent"] = item["x_percent"]
		mark_values["y_percent"] = item["y_percent"]
		mark_values["annotation_json"] = json.dumps({"element_id": element_id})
		# Unmapped variables pass through too - save_chart_mark stores them as rows now.
		for field, value in (item.get("variables") or {}).items():
			if field not in mark_values and value not in (None, ""):
				mark_values[field] = value
		try:
			save_chart_mark(mark_values)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Failed syncing derma chart mark from annotation")

	# A mark is only orphaned once the element that represents it has actually left the scene.
	# Marks drawn with the area or line tool are stamped in real time and carry their element
	# id, so without this they would be deleted on the next save of the same drawing - `tagged`
	# only ever holds elements from the element-tagging branch above. Freehand strokes no longer
	# become marks, but the ones drawn while they did are protected here too.
	live_element_ids = {
		element.get("id")
		for element in elements
		if isinstance(element, dict) and not element.get("isDeleted") and element.get("id")
	}
	for element_id, row in existing_by_element.items():
		if element_id in tagged or element_id in live_element_ids or row.get("clinical_procedure"):
			continue
		frappe.delete_doc("Derma Chart Mark", row.get("name"), ignore_permissions=True)

	# Marks placed in real time via the stamp tool (save_chart_mark called immediately on
	# placement, see EmbeddedExcalidraw's onMarkPlaced) already exist by the time the whole
	# scene is saved - just link them back to this snapshot rather than re-creating them.
	stamped_mark_names = {
		(element.get("customData") or {}).get("derma_chart_mark")
		or (element.get("customData") or {}).get("mark_name")
		for element in elements
		if isinstance(element, dict)
		and not element.get("isDeleted")
		and (element.get("customData") or {}).get("kind") == "derma_mark"
	}
	stamped_mark_names.discard(None)
	for mark_name in stamped_mark_names:
		if frappe.db.exists("Derma Chart Mark", mark_name):
			frappe.db.set_value(
				"Derma Chart Mark", mark_name, "annotation", annotation_name, update_modified=False
			)


@frappe.whitelist()
def delete_derma_annotation(annotation_name: str, doctype: str, docname: str):
	_ensure_clinical_access()
	if doctype not in ANNOTATION_SUMMARY_PARENTS:
		frappe.throw(_("Annotations are only kept on an encounter or a procedure."))
	if not annotation_name or not frappe.db.exists("Health Annotation", annotation_name):
		frappe.throw(_("Annotation not found."))
	if not docname or not frappe.db.exists(doctype, docname):
		frappe.throw(_("{0} not found.").format(doctype))
	_ensure_owner_open(doctype, docname)

	frappe.db.delete(
		"Health Annotation Table",
		{"annotation": annotation_name, "parent": docname, "parenttype": doctype},
	)

	has_other_table_row = bool(frappe.db.exists("Health Annotation Table", {"annotation": annotation_name}))
	if not has_other_table_row:
		from do_derma.teardown import annotations as annotation_teardown

		if not annotation_teardown.has_live_links(annotation_name):
			annotation_teardown.clear_preview(annotation_name)
			frappe.delete_doc("Health Annotation", annotation_name, ignore_permissions=True)


@frappe.whitelist()
def save_derma_annotation(payload: str | dict[str, Any]):
	_ensure_clinical_access()
	values = json.loads(payload) if isinstance(payload, str) else dict(payload or {})
	clinical_procedure = values.get("clinical_procedure")
	if clinical_procedure and not frappe.db.exists("Clinical Procedure", clinical_procedure):
		frappe.throw(_("Clinical Procedure not found."))
	doctype = values.get("doctype") or ("Clinical Procedure" if clinical_procedure else "Patient Encounter")
	docname = values.get("docname") or clinical_procedure or values.get("encounter")
	if not docname:
		frappe.throw(_("Encounter is required."))
	_ensure_owner_open(doctype, docname)
	if not values.get("file_data"):
		frappe.throw(_("Drawing image data is required."))
	json_text = values.get("json_text") or ""
	scene = _parse_json(json_text, None)
	if json_text and not isinstance(scene, dict):
		# The column refuses invalid JSON anyway, and the old fallback quietly replaced the
		# drawing with the two keys added below. Refuse it where the bug is instead.
		frappe.throw(_("The drawing could not be read and was not saved."), frappe.ValidationError)
	if isinstance(scene, dict):
		if (
			values.get("body_template")
			or values.get("body_template_title")
			or values.get("body_template_image")
		):
			scene["derma_template"] = {
				"name": values.get("body_template"),
				"title": values.get("body_template_title"),
				"image": values.get("body_template_image"),
			}
		area_values = _resolve_area_values(values.get("area_values"), values.get("annotation_name"))
		if area_values is not None:
			scene["derma_area_values"] = area_values
		selected_areas = _resolve_selected_areas(values.get("selected_areas"), values.get("annotation_name"))
		if selected_areas is not None:
			scene["derma_selected_areas"] = selected_areas
		json_text = json.dumps(scene)

	annotation_type = values.get("annotation_type") or "Free Drawing"
	if annotation_type not in {"Predefined Areas", "Predefined Annotations", "Free Drawing"}:
		annotation_type = "Free Drawing"

	patient = values.get("patient") or frappe.db.get_value(doctype, docname, "patient")

	annotation_name = _save_health_annotation(
		docname=docname,
		doctype=doctype,
		# annotation_template is a Link to the separate annotation app's "Annotation
		# Template" doctype, which do_derma does not use or depend on - writing a
		# display string here throws LinkValidationError. The human-readable label
		# goes into custom_derma_body_template_title (a do_derma-owned field) instead.
		annotation_template=values.get("annotation_template") or "",
		annotation_name=values.get("annotation_name"),
		encounter_type=values.get("encounter_type")
		or ("Treatment" if doctype == "Clinical Procedure" else ""),
		file_data=values.get("file_data"),
		json_text=json_text,
		annotation_type=annotation_type,
		annotation_data=values.get("annotation_data") or "",
		body_template_title=values.get("body_template_title") or "",
	)

	_sync_chart_marks_for_annotation(
		annotation_name,
		scene if isinstance(scene, dict) else None,
		patient,
		values.get("appointment"),
		values.get("encounter") or (docname if doctype == "Patient Encounter" else None),
		clinical_procedure or (docname if doctype == "Clinical Procedure" else None),
	)

	context = _load_derma_annotation_context(
		encounter=values.get("encounter") or (docname if doctype == "Patient Encounter" else None),
		procedure_names=[clinical_procedure or docname] if doctype == "Clinical Procedure" else [],
	)
	if doctype == "Clinical Procedure":
		rows = context.get("procedure_annotations", {}).get(clinical_procedure or docname, [])
	else:
		rows = context.get("encounter_annotations") or context.get("annotations") or []
	# The anchor can hold several drawings: hand back the one actually saved, or a
	# picker-resumed older drawing would get the newest one's name and overwrite it
	# on the studio's next save.
	saved_row = next((row for row in rows if row.get("name") == annotation_name), None)
	if not saved_row:
		saved_row = rows[0] if rows else None
	if saved_row and doctype == "Clinical Procedure":
		_link_procedure_annotation(clinical_procedure or docname, saved_row.get("name"))
	return saved_row


def _resolve_area_values(raw: Any, annotation_name: str | None) -> dict[str, dict[str, str]] | None:
	"""The area values to store on the scene, or None to leave the saved ones alone.

	Nothing to say - an absent key, or the empty map a studio that seeded nothing sends -
	keeps what is stored. A cleared area still names itself with a blank value, so clearing
	is never silent.
	"""
	values = _parse_json(raw, None) if isinstance(raw, str) else raw
	if not isinstance(values, dict) or not values:
		return _get_stored_area_values(annotation_name)
	return {
		str(part): {str(key): _stringify_variable_value(value) for key, value in fields.items()}
		for part, fields in values.items()
		if isinstance(fields, dict)
	}


def _resolve_selected_areas(raw: Any, annotation_name: str | None) -> list[str] | None:
	"""The selected areas to store on the scene, or None to leave the saved ones alone.

	Unlike the area values, an empty list is the practitioner unselecting everything and
	clears the stored selection. Anything that is not a list says nothing.
	"""
	areas = _parse_json(raw, None) if isinstance(raw, str) else raw
	if not isinstance(areas, list):
		return _get_stored_selected_areas(annotation_name)
	return list(dict.fromkeys(area for area in areas if isinstance(area, str) and area))


def _get_stored_selected_areas(annotation_name: str | None) -> list[str] | None:
	if not annotation_name or not frappe.db.exists("Health Annotation", annotation_name):
		return None
	stored = _parse_json(frappe.db.get_value("Health Annotation", annotation_name, "json"), {})
	saved = stored.get("derma_selected_areas") if isinstance(stored, dict) else None
	return saved if isinstance(saved, list) else None


def _get_stored_area_values(annotation_name: str | None) -> dict[str, dict[str, str]] | None:
	if not annotation_name or not frappe.db.exists("Health Annotation", annotation_name):
		return None
	stored = _parse_json(frappe.db.get_value("Health Annotation", annotation_name, "json"), {})
	saved = stored.get("derma_area_values") if isinstance(stored, dict) else None
	return saved if isinstance(saved, dict) else None


def _link_procedure_annotation(clinical_procedure: str | None, annotation: str | None) -> None:
	if not clinical_procedure or not annotation or not _has_doctype("Derma Treatment Entry"):
		return
	treatments = frappe.get_all(
		"Derma Treatment Entry",
		filters={"clinical_procedure": clinical_procedure},
		fields=["name", "annotation"],
		limit=20,
	)
	for row in treatments:
		if row.get("annotation") == annotation:
			continue
		frappe.db.set_value("Derma Treatment Entry", row.name, "annotation", annotation, update_modified=True)


@frappe.whitelist()
def get_derma_assessment(encounter=None, appointment=None, patient=None):
	_ensure_clinical_access()
	encounter_doc = _resolve_patient_encounter_doc(
		encounter=encounter, appointment=appointment, patient=patient
	)
	if not encounter_doc:
		return assessment.empty_assessment()
	return assessment.read_assessment(encounter_doc)


@frappe.whitelist()
def set_derma_assessment(payload=None, mode=None, encounter=None, appointment=None, patient=None):
	_ensure_clinical_access()
	values = _parse_payload(payload) or {}
	if not isinstance(values, dict):
		frappe.throw(_("Assessment payload must be an object."), frappe.ValidationError)

	encounter_doc = _resolve_patient_encounter_doc(
		encounter=encounter, appointment=appointment, patient=patient, ptype="write"
	)
	if not encounter_doc:
		frappe.throw(_("No encounter found for this session."), frappe.DoesNotExistError)

	layout = assessment.get_layout(assessment.normalize_mode(mode) or assessment.get_assessment_mode(encounter_doc))
	before = assessment.serialize_values(encounter_doc, layout)
	assessment.apply_assessment(encounter_doc, values, mode=mode)
	# apply_assessment already drops any field it cannot write on a submitted encounter, so the
	# doc state never shows a blocked attempt. Diff the raw request instead of the saved fields,
	# in the same comparable form as `before` - a native Date and its JSON string twin must match.
	attempted = {fieldname: value for fieldname, value in values.items() if fieldname in before}
	_ensure_changes_allowed_on_submit(
		encounter_doc, assessment.normalized_values(before, layout), assessment.normalized_values(attempted, layout)
	)
	encounter_doc.flags.ignore_validate_update_after_submit = True
	encounter_doc.save(ignore_permissions=True)
	return get_derma_assessment(encounter=encounter_doc.name)


def _assessment_writable_on_submit(mode) -> bool:
	"""True when at least one field of the mode may still be written after submit."""
	return any(cint(row.get("allow_on_submit")) for row in assessment.get_layout(mode) if row.get("is_value_field"))


@frappe.whitelist()
def set_derma_assessment_all(payloads=None, mode=None, encounter=None, appointment=None, patient=None):
	"""Write every format one dictation produced, in one save.

	`payloads` is {"Structured": {...}, "SOAP": {...}, "HP": {...}}; any format may be
	omitted. `mode` is the format the doctor is looking at - it is written last and
	stamped, so the encounter still reopens in the format it was documented in.
	"""
	_ensure_clinical_access()
	bundle = _parse_payload(payloads) or {}
	if not isinstance(bundle, dict):
		frappe.throw(_("Assessment payloads must be an object."), frappe.ValidationError)

	encounter_doc = _resolve_patient_encounter_doc(
		encounter=encounter, appointment=appointment, patient=patient, ptype="write"
	)
	if not encounter_doc:
		frappe.throw(_("No encounter found for this session."), frappe.DoesNotExistError)

	active = assessment.normalize_mode(mode) or assessment.get_assessment_mode(encounter_doc)
	others = [m for m in assessment.ASSESSMENT_MODES if m != active and assessment.mode_is_supported(m)]
	# A completed encounter only accepts allow-on-submit fields. Say so, rather than
	# letting a dictation the doctor just recorded disappear into a silent no-op.
	dictated = [m for m in [*others, active] if isinstance(bundle.get(m), dict) and bundle.get(m)]
	if cint(encounter_doc.docstatus) == 1 and any(not _assessment_writable_on_submit(m) for m in dictated):
		frappe.throw(
			_("This encounter is completed, so the note cannot be written into it. Reopen the encounter to save a dictated note."),
			frappe.ValidationError,
		)
	for target in [*others, active]:
		values = bundle.get(target)
		if isinstance(values, dict) and values:
			assessment.apply_assessment(encounter_doc, values, mode=target)
	if assessment.mode_is_supported(active) and not cint(encounter_doc.docstatus):
		assessment.stamp_mode(encounter_doc, active)
	encounter_doc.flags.ignore_validate_update_after_submit = True
	encounter_doc.save(ignore_permissions=True)
	return get_derma_assessment(encounter=encounter_doc.name)


@frappe.whitelist()
def set_derma_assessment_mode(mode, encounter=None, appointment=None, patient=None):
	"""Change the documented format. Writes no content and deletes nothing."""
	_ensure_clinical_access()
	encounter_doc = _resolve_patient_encounter_doc(
		encounter=encounter, appointment=appointment, patient=patient, ptype="write"
	)
	if not encounter_doc:
		frappe.throw(_("No encounter found for this session."), frappe.DoesNotExistError)
	_ensure_encounter_open(encounter_doc.name)

	assessment.stamp_mode(encounter_doc, mode)
	encounter_doc.save(ignore_permissions=True)
	return get_derma_assessment(encounter=encounter_doc.name)


@frappe.whitelist()
def get_derma_prescriptions(encounter=None, appointment=None, patient=None):
	_ensure_clinical_access()
	encounter_doc = _resolve_patient_encounter_doc(
		encounter=encounter, appointment=appointment, patient=patient
	)
	return {
		"encounter": encounter_doc.name if encounter_doc else encounter or "",
		"drug_prescription": _drug_prescription_rows(encounter_doc) if encounter_doc else [],
	}


@frappe.whitelist()
def set_derma_prescriptions(payload=None, encounter=None, appointment=None, patient=None):
	_ensure_clinical_access()
	if not _has_field("Patient Encounter", "drug_prescription"):
		return {"encounter": encounter or "", "drug_prescription": []}
	rows = _parse_payload(payload) or []
	if not isinstance(rows, list):
		frappe.throw(_("Prescription payload must be a list."), frappe.ValidationError)
	encounter_doc = _resolve_patient_encounter_doc(
		encounter=encounter, appointment=appointment, patient=patient, ptype="write"
	)
	if not encounter_doc:
		return {"encounter": "", "drug_prescription": []}
	_ensure_encounter_open(encounter_doc.name)
	allowed = _drug_prescription_fields()
	prescriptions = [_drug_prescription_row(row, allowed) for row in rows if isinstance(row, dict)]
	_validate_prescription_rows(prescriptions)
	prescriptions = _merge_ordered_prescriptions(_drug_prescription_rows(encounter_doc), prescriptions)
	encounter_doc.set("drug_prescription", prescriptions)
	encounter_doc.save(ignore_permissions=True)
	return {"encounter": encounter_doc.name, "drug_prescription": _drug_prescription_rows(encounter_doc)}


@frappe.whitelist()
def get_derma_anesthesia(encounter=None, appointment=None, patient=None):
	_ensure_clinical_access()
	encounter_doc = _resolve_patient_encounter_doc(
		encounter=encounter, appointment=appointment, patient=patient
	)
	return {"encounter": encounter_doc.name if encounter_doc else encounter or "", "anesthesia": []}


@frappe.whitelist()
def get_derma_consents(encounter=None, appointment=None, patient=None):
	_ensure_clinical_access()
	doctype = "Encounter Consent" if _has_doctype("Encounter Consent") else "Consent Form"
	if not _has_doctype(doctype):
		return []
	filters: dict[str, Any] = {}
	if encounter and _has_field(doctype, "encounter"):
		filters["encounter"] = encounter
	if appointment and _has_field(doctype, "appointment"):
		filters["appointment"] = appointment
	if patient and _has_field(doctype, "patient"):
		filters["patient"] = patient
	if not filters:
		return []
	fields = _select_existing_fields(
		doctype,
		["name", "consent_form_template", "status", "signed_by", "signed_on", "modified", "docstatus"],
	)
	rows = frappe.get_all(
		doctype, filters=filters, fields=fields, order_by="modified desc", limit_page_length=100
	)
	for row in rows:
		row["doctype"] = doctype
	return rows


@frappe.whitelist()
def create_derma_consent(payload=None):
	_ensure_clinical_access()
	values = _parse_payload(payload) or {}
	patient = values.get("patient")
	encounter_doc = _resolve_patient_encounter_doc(
		encounter=values.get("encounter"),
		appointment=values.get("appointment"),
		patient=patient,
		ptype="read",
	)
	encounter = encounter_doc.name if encounter_doc else None
	appointment = values.get("appointment") or (encounter_doc.get("appointment") if encounter_doc else None)
	if not patient and encounter_doc:
		patient = encounter_doc.get("patient")
	if not patient:
		frappe.throw(_("Patient is required."), frappe.ValidationError)
	if not encounter:
		frappe.throw(_("No encounter found for this session."), frappe.DoesNotExistError)
	_ensure_encounter_open(encounter)

	doctype = "Encounter Consent" if _has_doctype("Encounter Consent") else "Consent Form"
	if not _has_doctype(doctype):
		frappe.throw(_("Consent Form is not installed."))

	doc = frappe.new_doc(doctype)
	for fieldname, value in {
		"patient": patient,
		"encounter": encounter,
		"appointment": appointment,
		"consent_form_template": values.get("consent_form_template"),
		"company": values.get("company") or frappe.defaults.get_user_default("Company"),
		"signature": values.get("signature"),
		"signed_by": values.get("signed_by"),
		"relationship": values.get("relationship"),
	}.items():
		if value and _has_field(doctype, fieldname):
			doc.set(fieldname, value)

	procedure_items = values.get("procedure_items") or values.get("procedure_selection") or []
	if doctype == "Encounter Consent" and _has_field(doctype, "procedure_items"):
		for row in procedure_items:
			if isinstance(row, str):
				row = {"clinical_procedure": row}
			if not isinstance(row, dict):
				continue
			doc.append(
				"procedure_items",
				{
					"clinical_procedure": row.get("clinical_procedure") or row.get("value"),
					"procedure_template": row.get("procedure_template"),
					"display_name": row.get("display_name") or row.get("label"),
					"teeth": row.get("teeth") or row.get("location") or "",
				},
			)
	elif doctype == "Consent Form" and procedure_items:
		first = procedure_items[0]
		if isinstance(first, str):
			first = {"clinical_procedure": first}
		if isinstance(first, dict):
			clinical_procedure = first.get("clinical_procedure") or first.get("value")
			if clinical_procedure and _has_field(doctype, "clinical_procedure"):
				doc.clinical_procedure = clinical_procedure
			procedure_template = first.get("procedure_template")
			if (
				not procedure_template
				and clinical_procedure
				and _has_field("Clinical Procedure", "procedure_template")
			):
				procedure_template = frappe.db.get_value(
					"Clinical Procedure", clinical_procedure, "procedure_template"
				)
			if procedure_template and _has_field(doctype, "procedure_template"):
				doc.procedure_template = procedure_template

	if doc.get("consent_form_template") and hasattr(doc, "render_template"):
		doc.render_template()
	if values.get("rendered_html") and _has_field(doctype, "rendered_html"):
		doc.rendered_html = values.get("rendered_html")

	doc.insert(ignore_permissions=True)
	if doc.meta.is_submittable and doc.get("signature") and doc.get("signed_by"):
		doc.submit()
	return {
		"name": doc.name,
		"doctype": doctype,
		"rendered_html": doc.get("rendered_html"),
		"status": doc.get("status"),
		"docstatus": doc.docstatus,
		"created_on": now_datetime(),
	}


@frappe.whitelist()
def render_derma_consent_preview(payload=None):
	_ensure_clinical_access()
	values = _parse_payload(payload) or {}
	consent_template = values.get("consent_form_template")
	if not consent_template:
		return {"rendered_html": ""}
	doctype = "Encounter Consent" if _has_doctype("Encounter Consent") else "Consent Form"
	if not _has_doctype(doctype):
		return {"rendered_html": ""}
	doc = frappe.new_doc(doctype)
	for fieldname, value in {
		"patient": values.get("patient"),
		"encounter": values.get("encounter"),
		"appointment": values.get("appointment"),
		"consent_form_template": consent_template,
		"company": values.get("company") or frappe.defaults.get_user_default("Company"),
	}.items():
		if value and _has_field(doctype, fieldname):
			doc.set(fieldname, value)
	if hasattr(doc, "render_template"):
		try:
			doc.render_template()
		except Exception as exc:
			# The renderer lives in the health app and trips over templates it cannot read.
			# Name the template so an administrator can fix it instead of the clinician retrying.
			frappe.log_error(frappe.get_traceback(), "Derma consent preview render failed")
			return {
				"rendered_html": "",
				"error": _("Consent template {0} could not be rendered: {1}").format(
					consent_template, str(exc) or exc.__class__.__name__
				),
			}
	return {"rendered_html": doc.get("rendered_html") or ""}


@frappe.whitelist()
def get_derma_consent_html(name: str):
	_ensure_clinical_access()
	if not name:
		frappe.throw(_("Consent is required."), frappe.ValidationError)
	doctype = (
		"Encounter Consent"
		if _has_doctype("Encounter Consent") and frappe.db.exists("Encounter Consent", name)
		else "Consent Form"
	)
	doc = frappe.get_doc(doctype, name)
	if not doc.get("rendered_html") and doc.get("consent_form_template") and hasattr(doc, "render_template"):
		doc.render_template()
		doc.save(ignore_permissions=True)
	return {
		"name": doc.name,
		"doctype": doctype,
		"consent_form_template": doc.get("consent_form_template"),
		"rendered_html": doc.get("rendered_html"),
		"status": doc.get("status"),
		"signed_by": doc.get("signed_by"),
		"signed_on": doc.get("signed_on"),
	}


@frappe.whitelist()
def get_procedure_price(procedure_name: str, price_list: str | None = None):
	_ensure_clinical_access()
	if not procedure_name:
		frappe.throw(_("Procedure is required."))
	procedure = frappe.get_doc("Clinical Procedure", procedure_name)
	rate = flt(procedure.get("rate") or procedure.get("amount") or 0)
	template_name = procedure.get("procedure_template")
	if template_name:
		template = frappe.get_doc("Clinical Procedure Template", template_name)
		item_code = template.get("item_code") or template.get("item")
		if item_code and _has_doctype("Item Price"):
			price_filters = {"item_code": item_code}
			if price_list:
				price_filters["price_list"] = price_list
			item_rate = frappe.db.get_value(
				"Item Price", price_filters, "price_list_rate", order_by="valid_from desc"
			)
			if item_rate is not None:
				rate = flt(item_rate)
		if not rate:
			rate = flt(template.get("rate") or 0)
	return {"rate": rate, "price_list": price_list or ""}


@frappe.whitelist()
def update_clinical_procedure_fields(procedure_name: str, updates=None):
	_ensure_clinical_access()
	if not procedure_name:
		frappe.throw(_("Procedure is required."))
	values = _parse_payload(updates) or {}
	if not isinstance(values, dict):
		frappe.throw(_("Updates must be an object."), frappe.ValidationError)
	doc = frappe.get_doc("Clinical Procedure", procedure_name)
	if not doc.has_permission("write"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	before = {fieldname: doc.get(fieldname) for fieldname in values}
	for fieldname, value in values.items():
		if fieldname in {"name", "doctype", "docstatus"}:
			continue
		if not doc.meta.has_field(fieldname):
			# Skipping would hand the chart a success it did not earn and lose the edit.
			frappe.throw(_("Clinical Procedure has no field {0}.").format(fieldname), frappe.ValidationError)
		doc.set(fieldname, value)
	if cint(doc.docstatus):
		_ensure_changes_allowed_on_submit(doc, before, {fieldname: doc.get(fieldname) for fieldname in values})
	else:
		_ensure_encounter_open(_get_owning_encounter("Clinical Procedure", doc.name))
	doc.flags.ignore_validate_update_after_submit = True
	doc.save(ignore_permissions=True)
	return doc.as_dict()


@frappe.whitelist()
def delete_clinical_procedure_entry(doctype: str, name: str):
	_ensure_clinical_access()
	if doctype != "Clinical Procedure":
		frappe.throw(_("Not allowed"), frappe.PermissionError)
	if not frappe.db.exists(doctype, name):
		frappe.throw(_("Document does not exist."))
	doc = frappe.get_doc(doctype, name)
	if not doc.has_permission("delete"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	if cint(doc.docstatus) != 0:
		frappe.throw(_("Only draft procedures can be deleted. Cancel a submitted procedure instead."))
	frappe.delete_doc(doctype, name, ignore_permissions=True)
	return True


@frappe.whitelist()
def sync_derma_billables(
	encounter: str | None = None, appointment: str | None = None, patient: str | None = None
):
	"""Aggregate this session's Clinical Procedure rows into Patient Appointment billing items.

	This endpoint deliberately stays derma-local. Billing integration can be
	expanded here once the shared
	do_health billing item schema is stabilized for specialty procedures.
	"""
	_ensure_clinical_access()
	context = _get_visit_context(patient=patient, appointment=appointment, encounter=encounter)
	appointment_id = context["appointment_id"]
	encounter_id = context["encounter_id"]
	_ensure_encounter_open(encounter_id)
	if not appointment_id:
		frappe.throw(_("An appointment is required to sync billing."))
	filters = _clinical_procedure_context_filters(
		encounter=encounter_id, appointment=appointment_id, patient=context["patient_id"]
	)
	count = frappe.db.count("Clinical Procedure", filters) if filters else 0
	return {
		"appointment": appointment_id,
		"encounter": encounter_id,
		"added": 0,
		"updated": 0,
		"skipped": count,
		"message": _("Billing sync is not configured for derma procedures yet."),
	}


def _complete_derma_procedures_for_session(patient: str, encounter: str) -> dict[str, Any]:
	"""Submit every still-draft Clinical Procedure for this session."""
	encounter_field = _get_clinical_procedure_encounter_field()
	if not encounter_field or not patient or not encounter:
		return {"completed": [], "failed": []}

	names = frappe.get_all(
		"Clinical Procedure",
		filters={"patient": patient, encounter_field: encounter, "docstatus": 0},
		pluck="name",
	)
	completed = []
	failed = []
	for name in names:
		try:
			doc = frappe.get_doc("Clinical Procedure", name)
			if not doc.has_permission("submit"):
				frappe.throw(_("Not permitted"), frappe.PermissionError)
			before = reopen.get_nursing_tasks(name)
			doc.submit()
			reopen.drop_repeated_nursing_tasks(name, before)
			if _has_field("Clinical Procedure", "status"):
				doc.db_set("status", "Completed", update_modified=True)
			completed.append(name)
		except Exception as exc:
			failed.append({"procedure": name, "error": str(exc)})
	return {"completed": completed, "failed": failed}


@frappe.whitelist()
def complete_derma_session(
	encounter: str | None = None,
	appointment: str | None = None,
	patient: str | None = None,
	submit_invoice: int = 0,
	override_reason: str | None = None,
):
	"""Finalize a derma visit: complete draft procedures, sync billables, raise/update
	the patient invoice, and submit the encounter."""
	_ensure_clinical_access()
	context = _get_visit_context(patient=patient, appointment=appointment, encounter=encounter)
	appointment_id = context["appointment_id"]
	encounter_id = context["encounter_id"]
	patient_id = context["patient_id"]
	if not encounter_id:
		frappe.throw(_("No encounter found for this session."))
	_ensure_encounter_open(encounter_id)

	readiness = get_session_readiness(patient_id, appointment=appointment_id, encounter=encounter_id)
	_gate_session_completion(readiness, encounter_id, override_reason)

	procedure_completion = _complete_derma_procedures_for_session(patient_id, encounter_id)

	billing_sync = None
	invoice = None
	invoice_error = None
	if appointment_id:
		billing_sync = sync_derma_billables(
			encounter=encounter_id, appointment=appointment_id, patient=patient_id
		)
		invoice, invoice_error = _create_visit_invoice(appointment_id, encounter_id, submit_invoice)

	encounter_doc = frappe.get_doc("Patient Encounter", encounter_id)
	submitted = False
	if encounter_doc.docstatus == 0:
		encounter_doc.submit()
		submitted = True

	_drop_uninstalled_app_messages()
	return {
		"encounter": encounter_id,
		"encounter_submitted": submitted,
		"procedures_completed": procedure_completion["completed"],
		"procedures_failed": procedure_completion["failed"],
		"billing_sync": billing_sync,
		"invoice": invoice,
		"invoice_error": invoice_error,
		"readiness": readiness,
	}


@frappe.whitelist()
def reopen_derma_session(encounter: str, reason: str | None = None):
	"""Put a completed visit back to draft. Procedures, invoices and orders stay submitted."""
	_ensure_clinical_access()
	doc = frappe.get_doc("Patient Encounter", encounter)
	reopen.ensure_can_reopen(doc)
	reopen.ensure_no_therapy_plan(doc.name)
	reopen.reopen_document(doc, reason, status="Open")
	return {"encounter": doc.name, "docstatus": 0}


@frappe.whitelist()
def reopen_derma_procedure(procedure: str, reason: str | None = None):
	"""Put one completed procedure back to draft inside a reopened visit."""
	_ensure_clinical_access()
	doc = frappe.get_doc("Clinical Procedure", procedure)
	encounter = _get_owning_encounter("Clinical Procedure", doc.name)
	if not encounter:
		frappe.throw(
			_("Procedure {0} is not linked to a visit, so it cannot be reopened here.").format(doc.name),
			frappe.ValidationError,
		)
	_ensure_encounter_open(encounter)
	reopen.ensure_can_reopen(doc)
	invoice = reopen.get_submitted_invoices([doc.name]).get(doc.name)
	if invoice:
		frappe.throw(
			_("Procedure {0} is billed on submitted invoice {1}. Cancel the invoice first.").format(
				doc.name, invoice
			),
			frappe.ValidationError,
		)
	reopen.reopen_document(doc, reason, status="In Progress")
	return {"procedure": doc.name, "docstatus": 0}


def _drop_uninstalled_app_messages() -> None:
	"""Completion reports on the visit, not on apps this clinic never installed.

	do_health's billing path msgprints about sibling specialty apps, which makes a
	completed session look failed to the practitioner.
	"""
	log = frappe.local.message_log or []
	if not log:
		return
	installed = set(frappe.get_installed_apps())
	kept = []
	for message in log:
		text = str(message.get("message") if isinstance(message, dict) else message)
		match = UNINSTALLED_APP_MESSAGE.search(text)
		if match and match.group(1) not in installed:
			continue
		kept.append(message)
	frappe.local.message_log = kept


def _create_visit_invoice(appointment: str, encounter: str, submit_invoice: int) -> tuple[Any, str | None]:
	"""Raise the visit invoice through do_health, keeping that app's chatter out of our response.

	create_invoice_for_visit msgprints about apps this clinic does not run, and those
	messages otherwise make a completed session look failed.
	"""
	messages_before = list(frappe.local.message_log or [])
	try:
		from do_health.api.methods import create_invoice_for_visit

		invoice = create_invoice_for_visit(
			appointment=appointment,
			encounter=encounter,
			submit_invoice=cint(submit_invoice),
		)
		return invoice, None
	except Exception as exc:
		frappe.log_error(frappe.get_traceback(), "Derma session invoice creation failed")
		# The message log now holds the failure's own chatter; the caller gets the reason instead.
		frappe.local.message_log = messages_before
		return None, str(exc) or exc.__class__.__name__


def _gate_session_completion(readiness: dict[str, Any], encounter: str, override_reason: str | None) -> None:
	"""Refuse a session the clinic's settings say is not ready, unless the clinician says
	why. Runs before anything is submitted, so a refused session submits nothing."""
	from do_derma.readiness.session import is_completion_blocked

	_ensure_consumable_batches(readiness)
	if not is_completion_blocked(readiness):
		return

	blockers = readiness["blockers"]
	reason = (override_reason or "").strip()
	if not reason:
		frappe.throw(
			_("{0} blockers must be resolved, or the session completed with a reason.").format(len(blockers))
		)
	_record_completion_override(encounter, reason, blockers)


def _ensure_consumable_batches(readiness: dict[str, Any]) -> None:
	"""A material of a batch-tracked item with no batch stops completion outright.

	Unlike the readiness blockers around it, no override reason gets past this: the stock
	entry it would produce cannot be posted at all.
	"""
	names = [
		str(item.get("product_name") or item.get("product_item") or _("Material"))
		for item in readiness["items"]
		if item.get("is_hard_blocking")
	]
	if names:
		frappe.throw(
			_("These materials are tracked by batch and none is chosen: {0}.").format(", ".join(names))
		)


def _record_completion_override(encounter: str, reason: str, blockers: list[dict[str, Any]]) -> None:
	"""Why a blocked session was completed anyway: on the encounter, and in its timeline.

	The Comment is the record that always survives - the field is written only on a site
	whose schema has converged, which `ensure_derma_schema` does on every migrate."""
	if _has_field("Patient Encounter", COMPLETION_OVERRIDE_FIELD):
		frappe.db.set_value("Patient Encounter", encounter, COMPLETION_OVERRIDE_FIELD, reason)

	titles = [str(blocker.get("title") or _("Readiness")) for blocker in blockers]
	frappe.get_doc("Patient Encounter", encounter).add_comment(
		"Comment",
		_("Session completed by {0} with {1} unresolved blockers ({2}). Reason: {3}").format(
			frappe.session.user, len(titles), ", ".join(titles), reason
		),
	)


def _ensure_body_template_allowed(
	procedure_template: str | None, body_template: str | None, template_row: dict[str, Any]
) -> None:
	"""Refuse a mark on a body map the procedure's template does not allow.

	The list is free text a clinic types into a Small Text field, so it is read
	case-insensitively and an empty value means no restriction. A body template is
	named by its title, which `field:title` autonaming makes its name.
	"""

	allowed = _split_csv(template_row.get("custom_derma_allowed_body_templates"))
	if not (body_template and allowed):
		return
	if str(body_template).casefold() in {entry.casefold() for entry in allowed}:
		return
	frappe.throw(_("{0} cannot be charted on {1}.").format(procedure_template, body_template))


@frappe.whitelist()
def save_chart_mark(values: str | dict[str, Any]):
	_ensure_clinical_access()
	payload = json.loads(values) if isinstance(values, str) else dict(values or {})
	name = payload.pop("name", None)
	if not payload.get("patient"):
		frappe.throw(_("Patient is required."))
	if not payload.get("encounter") and payload.get("appointment"):
		context = ensure_chart_context(payload.get("patient"), payload.get("appointment"))
		payload["encounter"] = context.get("encounter")
	if payload.get("encounter") and not payload.get("appointment"):
		payload["appointment"] = frappe.db.get_value(
			"Patient Encounter", payload.get("encounter"), "appointment"
		)

	if name:
		_ensure_owner_open("Derma Chart Mark", name)
	_ensure_encounter_open(payload.get("encounter"))
	_ensure_owner_open("Clinical Procedure", payload.get("clinical_procedure"))

	_normalize_position(payload)
	if MARK_SIZE_FIELD in payload:
		payload[MARK_SIZE_FIELD] = validated_marker_size(payload[MARK_SIZE_FIELD])
	category_defaults = _category_defaults(payload.get("category"))
	template_defaults = _template_defaults(payload.get("procedure_template"))
	for field, value in {
		"category": template_defaults.get("custom_derma_category") or payload.get("category"),
		"product_item": template_defaults.get("item"),
		"marker_behavior": template_defaults.get("custom_derma_marker_behavior")
		or category_defaults.get("marker_behavior"),
		"marker_color": template_defaults.get("custom_derma_marker_color")
		or category_defaults.get("marker_color"),
		MARK_SIZE_FIELD: template_defaults.get(MARKER_SIZE_FIELD),
		"marker_label": category_defaults.get("marker_label"),
		"body_template": category_defaults.get("default_body_template"),
	}.items():
		if value and not payload.get(field):
			payload[field] = value

	_ensure_body_template_allowed(
		payload.get("procedure_template"), payload.get("body_template"), template_defaults
	)

	if payload.get("body_template") and not payload.get("body_view"):
		payload["body_view"] = frappe.db.get_value("Derma Body Template", payload["body_template"], "title")
	if payload.get("body_region"):
		payload["body_region"] = _normalize_derma_body_region(payload.get("body_region"))
	_resolve_mark_template_part(payload)

	doc = frappe.get_doc("Derma Chart Mark", name) if name else frappe.new_doc("Derma Chart Mark")
	for field in DERMA_MARK_FIELDS:
		if field in {"name", "modified"}:
			continue
		if field in payload:
			doc.set(field, payload[field])
	_apply_mark_area_variables(doc, payload.get("area_variables"))
	_apply_mark_procedure_variables(doc, payload)
	_set_patient_name(doc)
	if not doc.sequence:
		doc.sequence = _next_mark_sequence(doc.patient, doc.encounter, doc.category)
	doc.save(ignore_permissions=True)
	return doc.as_dict()


CONSUMABLE_OWNERS = {
	"Derma Chart Mark": consumable_marks,
	"Clinical Procedure": consumable_procedures,
}


@frappe.whitelist()
def save_consumables(
	owner_doctype: str, owner_name: str, rows: str | list[dict[str, Any]] | None = None
) -> dict[str, Any]:
	"""Replace one owner's consumables outright and answer what the chart should now show."""
	_ensure_clinical_access()
	from do_derma.consumables import rows as consumable_rows

	owner = CONSUMABLE_OWNERS.get(owner_doctype)
	if not owner:
		frappe.throw(_("Materials cannot be recorded on {0}.").format(owner_doctype or _("nothing")))
	if not owner_name:
		frappe.throw(_("{0} is required.").format(_(owner_doctype)))
	if not owner.is_available():
		frappe.throw(_("Consumables are not available on this site."))

	# Every row is validated before the first write, so a bad third row cannot leave the
	# first two stored against a list the clinician never confirmed.
	return owner.save(owner_name, consumable_rows.clean_rows(_parse_payload(rows) or []))


@frappe.whitelist()
def get_consumable_item_options(
	item_code: str, owner_doctype: str | None = None, owner_name: str | None = None
) -> dict[str, Any]:
	"""The units and batches one item offers this owner, for the chart's add row."""
	_ensure_clinical_access()
	from do_derma.consumables import items as consumable_items

	if not item_code:
		frappe.throw(_("Every consumable line needs an item."))
	return consumable_items.get_options(item_code, owner_doctype, owner_name)


def _ensure_encounter_open(encounter: str | None) -> None:
	"""A closed encounter is read-only on the chart; a stale tab must not write past it."""
	if not encounter:
		return
	docstatus = cint(frappe.db.get_value("Patient Encounter", encounter, "docstatus"))
	if docstatus == 1:
		frappe.throw(_("This encounter is completed. Reopen it to make changes."), frappe.ValidationError)
	if docstatus == 2:
		frappe.throw(_("This encounter is cancelled and can no longer be edited."), frappe.ValidationError)


def _get_owning_encounter(doctype: str, name: str | None) -> str | None:
	if not name:
		return None
	if doctype == "Patient Encounter":
		return name
	if doctype == "Clinical Procedure":
		field = _get_clinical_procedure_encounter_field()
		return frappe.db.get_value(doctype, name, field) if field else None
	return frappe.db.get_value(doctype, name, "encounter")


def _ensure_owner_open(doctype: str, name: str | None) -> None:
	"""A procedure, or a mark placed on one, must be a draft itself as well as sit in an open encounter."""
	procedure = name if doctype == "Clinical Procedure" else None
	if doctype == "Derma Chart Mark" and name:
		procedure = frappe.db.get_value(doctype, name, "clinical_procedure")
	if procedure and cint(frappe.db.get_value("Clinical Procedure", procedure, "docstatus")):
		frappe.throw(_("This procedure is completed. Reopen it to make changes."), frappe.ValidationError)
	_ensure_encounter_open(_get_owning_encounter(doctype, name))


def _ensure_changes_allowed_on_submit(doc, before: dict[str, Any], after: dict[str, Any]) -> None:
	"""A submitted document may change only the fields Frappe allows on submit."""
	if not cint(doc.docstatus):
		return
	locked = [
		fieldname
		for fieldname, value in after.items()
		if value != before.get(fieldname)
		and doc.meta.has_field(fieldname)
		and not doc.meta.get_field(fieldname).allow_on_submit
	]
	if locked:
		labels = ", ".join(_(doc.meta.get_label(fieldname)) for fieldname in locked)
		frappe.throw(
			_("{0} is completed, so {1} cannot change. Reopen it to make changes.").format(_(doc.doctype), labels),
			frappe.ValidationError,
		)


def _next_mark_sequence(patient: str, encounter: str | None = None, category: str | None = None) -> int:
	filters = {"patient": patient}
	if encounter:
		filters["encounter"] = encounter
	if category:
		filters["category"] = category
	return cint(frappe.db.count("Derma Chart Mark", filters)) + 1


@frappe.whitelist()
def create_procedure_from_mark(
	mark: str, procedure_template: str | None = None, values: str | dict[str, Any] | None = None
):
	_ensure_clinical_access()
	from do_derma.readiness import procedure as procedure_readiness

	if not mark:
		frappe.throw(_("Chart mark is required."))
	_ensure_owner_open("Derma Chart Mark", mark)
	mark_doc = frappe.get_doc("Derma Chart Mark", mark)
	payload = json.loads(values) if isinstance(values, str) else dict(values or {})
	if payload:
		for field in DERMA_MARK_FIELDS:
			if field in {"name", "modified"}:
				continue
			if field in payload:
				mark_doc.set(field, payload[field])

	procedure_template = procedure_template or mark_doc.procedure_template
	if not procedure_template:
		frappe.throw(_("Select a Clinical Procedure Template before creating the procedure."))
	if not mark_doc.encounter:
		frappe.throw(_("Procedure creation requires an appointment-linked encounter."))
	encounter_doc = frappe.get_doc("Patient Encounter", mark_doc.encounter)
	if not mark_doc.appointment and encounter_doc.get("appointment"):
		mark_doc.appointment = encounter_doc.get("appointment")
	if not mark_doc.encounter or not mark_doc.appointment:
		frappe.throw(_("Procedure creation requires an appointment-linked encounter."))

	template_doc = frappe.get_doc("Clinical Procedure Template", procedure_template)
	_ensure_body_template_allowed(procedure_template, mark_doc.body_template, template_doc.as_dict())
	# Read before the mark is re-anchored below: per-procedure values were captured against the
	# procedure the studio was open on, and the gate has to see them.
	anchor_procedure = mark_doc.clinical_procedure
	procedure_readiness.validate_marks_ready([mark_doc], template_doc, clinical_procedure=anchor_procedure)
	procedure = frappe.new_doc("Clinical Procedure")
	procedure.patient = mark_doc.patient
	procedure.patient_name = mark_doc.patient_name
	procedure.appointment = mark_doc.appointment
	procedure.procedure_template = procedure_template
	procedure.title = template_doc.template
	procedure.status = "Draft"
	procedure.start_date = nowdate()
	if mark_doc.note and _has_field("Clinical Procedure", "notes"):
		procedure.notes = mark_doc.note
	for encounter_field in ["patient_encounter", "custom_patient_encounter"]:
		if _has_field("Clinical Procedure", encounter_field):
			procedure.set(encounter_field, mark_doc.encounter)
	if _has_field("Clinical Procedure", "practitioner"):
		procedure.practitioner = encounter_doc.get("practitioner")
	if _has_field("Clinical Procedure", "practitioner_name"):
		procedure.practitioner_name = encounter_doc.get("practitioner_name")
	if _has_field("Clinical Procedure", "medical_department"):
		procedure.medical_department = template_doc.get("medical_department") or encounter_doc.get(
			"medical_department"
		)
	consumable_marks.apply_to_procedure(procedure, mark_doc)
	procedure.insert(ignore_permissions=True)
	_carry_procedure_variables(anchor_procedure, procedure.name, procedure_template)

	mark_doc.procedure_template = procedure_template
	mark_doc.clinical_procedure = procedure.name
	_set_patient_name(mark_doc)
	mark_doc.save(ignore_permissions=True)

	treatment = _upsert_treatment_from_mark(mark_doc, template_doc)
	return {
		"mark": mark_doc.as_dict(),
		"clinical_procedure": procedure.as_dict(),
		"treatment_entry": treatment,
	}


@frappe.whitelist()
def delete_chart_mark(name: str):
	_ensure_clinical_access()
	if not name:
		frappe.throw(_("Chart mark is required."))
	mark_doc = frappe.get_doc("Derma Chart Mark", name)
	if mark_doc.clinical_procedure and frappe.db.exists("Clinical Procedure", mark_doc.clinical_procedure):
		procedure = frappe.get_doc("Clinical Procedure", mark_doc.clinical_procedure)
		status = procedure.get("status")
		if procedure.docstatus == 1 or status not in {"Cancelled", "Canceled"}:
			frappe.throw(_("This mark is linked to an active Clinical Procedure and cannot be deleted."))
	mark_doc.delete(ignore_permissions=True)
	return {"deleted": name}


@frappe.whitelist()
def discard_chart_marks(names: str | list[str]):
	"""Undo the marks an abandoned annotation session placed, and report the ones it may not.

	Marks are written at placement time, so a discarded drawing leaves them behind. Being
	linked to the procedure it was drawn on is not documentation - `delete_chart_mark`
	refuses on exactly that, which is why this rule is its own.
	"""
	_ensure_clinical_access()
	requested = json.loads(names) if isinstance(names, str) else list(names or [])
	deleted: list[str] = []
	kept: list[str] = []
	for name in requested:
		if not name or not frappe.db.exists("Derma Chart Mark", name):
			continue
		mark_doc = frappe.get_doc("Derma Chart Mark", name)
		_ensure_encounter_open(mark_doc.encounter)
		if _is_mark_documented(mark_doc):
			kept.append(name)
			continue
		mark_doc.delete(ignore_permissions=True)
		deleted.append(name)
	return {"deleted": deleted, "kept": kept}


@frappe.whitelist()
def prune_chart_marks(names: str | list[str], annotation: str | None = None):
	"""Delete marks whose elements were removed from a drawing before it saved.

	The saving drawing is the caller, so being linked to it is not documentation -
	without this the deleted mark haunts the chart and resurfaces on the next open.
	Anything else that claims the mark keeps it, and the caller is told."""
	_ensure_clinical_access()
	requested = json.loads(names) if isinstance(names, str) else list(names or [])
	deleted: list[str] = []
	kept: list[str] = []
	for name in requested:
		if not name or not frappe.db.exists("Derma Chart Mark", name):
			continue
		mark_doc = frappe.get_doc("Derma Chart Mark", name)
		_ensure_encounter_open(mark_doc.encounter)
		if _is_mark_documented(mark_doc, ignore_annotation=annotation):
			kept.append(name)
			continue
		mark_doc.delete(ignore_permissions=True)
		deleted.append(name)
	return {"deleted": deleted, "kept": kept}


def _is_mark_documented(mark_doc, ignore_annotation: str | None = None) -> bool:
	"""True once something other than the drawing that placed it depends on the mark."""
	if any(mark_doc.get(field) for field in ("finding", "treatment_entry", "photo_set")):
		return True
	annotation = mark_doc.get("annotation")
	if annotation and annotation != ignore_annotation:
		return True
	procedure = mark_doc.get("clinical_procedure")
	if not procedure or not frappe.db.exists("Clinical Procedure", procedure):
		return False
	return cint(frappe.db.get_value("Clinical Procedure", procedure, "docstatus")) == 1


@frappe.whitelist()
def carry_forward_marks(
	marks: str | list[str],
	patient: str | None = None,
	encounter: str | None = None,
	appointment: str | None = None,
	status: str = "Monitoring",
):
	_ensure_clinical_access()
	mark_names = json.loads(marks) if isinstance(marks, str) else list(marks or [])
	mark_names = [name for name in mark_names if name]
	if not mark_names:
		frappe.throw(_("Select at least one previous mark."))
	context = _get_visit_context(patient=patient, appointment=appointment, encounter=encounter)
	target_patient = context["patient_id"]
	target_appointment = context["appointment_id"]
	target_encounter = context["encounter_id"]
	if not target_encounter:
		frappe.throw(_("An active Patient Encounter is required."))
	_ensure_encounter_open(target_encounter)

	copied = []
	for source_name in mark_names:
		source = frappe.get_doc("Derma Chart Mark", source_name)
		if source.patient != target_patient:
			frappe.throw(_("Previous marks must belong to the selected patient."))
		if source.encounter == target_encounter:
			continue
		doc = frappe.new_doc("Derma Chart Mark")
		for field in DERMA_MARK_FIELDS:
			if field in {
				"name",
				"modified",
				"patient",
				"patient_name",
				"appointment",
				"encounter",
				"clinical_procedure",
				"finding",
				"treatment_entry",
				"annotation",
				"annotation_json",
				"sequence",
			}:
				continue
			if hasattr(source, field):
				doc.set(field, source.get(field))
		doc.patient = target_patient
		doc.patient_name = frappe.db.get_value("Patient", target_patient, "patient_name")
		doc.appointment = target_appointment
		doc.encounter = target_encounter
		doc.status = status or source.get("status") or "Monitoring"
		doc.note = (
			_("Carried forward from {0}.").format(source.name)
			if not source.get("note")
			else f"{source.get('note')}\n" + _("Carried forward from {0}.").format(source.name)
		)
		doc.sequence = _next_mark_sequence(doc.patient, doc.encounter, doc.category)
		doc.save(ignore_permissions=True)
		copied.append(doc.as_dict())
	return {"marks": copied}


def _upsert_treatment_from_mark(mark_doc, template_doc=None) -> dict[str, Any]:
	treatment = (
		frappe.get_doc("Derma Treatment Entry", mark_doc.treatment_entry)
		if mark_doc.treatment_entry
		else frappe.new_doc("Derma Treatment Entry")
	)
	category = mark_doc.category or (template_doc.get("custom_derma_category") if template_doc else None)
	treatment.patient = mark_doc.patient
	treatment.patient_name = mark_doc.patient_name
	treatment.appointment = mark_doc.appointment
	treatment.encounter = mark_doc.encounter
	if _has_field("Derma Treatment Entry", "clinical_procedure"):
		treatment.clinical_procedure = mark_doc.clinical_procedure
	treatment.workflow = "Aesthetic" if category in {"Botox", "Filler", "Laser", "Peel"} else "Medical"
	treatment.procedure_type = _treatment_procedure_type(category)
	treatment.body_view = mark_doc.body_view
	treatment.body_region = _normalize_derma_body_region(
		mark_doc.body_region or mark_doc.body_template or mark_doc.body_view
	)
	treatment.region_label = mark_doc.region_label
	treatment.side = mark_doc.side
	treatment.x_percent = mark_doc.x_percent
	treatment.y_percent = mark_doc.y_percent
	treatment.product_item = mark_doc.product_item
	treatment.product_name = mark_doc.product_name
	treatment.dose = mark_doc.dose
	treatment.dose_unit = mark_doc.dose_unit
	treatment.device = mark_doc.device
	treatment.settings = mark_doc.settings
	treatment.lot_no = mark_doc.lot_no
	treatment.expiry_date = mark_doc.expiry_date
	treatment.photo_set = mark_doc.photo_set
	treatment.annotation = mark_doc.annotation
	treatment.notes = mark_doc.note
	treatment.save(ignore_permissions=True)
	if mark_doc.treatment_entry != treatment.name:
		mark_doc.db_set("treatment_entry", treatment.name)
	return treatment.as_dict()


def _join_unique(values) -> str:
	seen = []
	for value in values:
		if value in (None, ""):
			continue
		text = str(value)
		if text not in seen:
			seen.append(text)
	return ", ".join(seen)


@frappe.whitelist()
def generate_visit_summary(encounter: str | None = None, patient: str | None = None):
	_ensure_clinical_access()
	filters: dict[str, Any] = {}
	if encounter:
		filters["encounter"] = encounter
	elif patient:
		filters["patient"] = patient
	else:
		return ""

	marks = []
	if _has_doctype("Derma Chart Mark"):
		marks = frappe.get_all(
			"Derma Chart Mark",
			filters=filters,
			fields=_select_existing_fields("Derma Chart Mark", DERMA_MARK_FIELDS),
			order_by="sequence asc, modified asc",
			limit=500,
		)

	if not marks:
		findings = frappe.get_all(
			"Derma Finding",
			filters=filters,
			fields=_select_existing_fields("Derma Finding", DERMA_FINDING_FIELDS),
			limit=200,
		)
		treatments = frappe.get_all(
			"Derma Treatment Entry",
			filters=filters,
			fields=_select_existing_fields("Derma Treatment Entry", DERMA_TREATMENT_FIELDS),
			limit=200,
		)
		return build_visit_narrative(findings, treatments)

	return build_mark_narrative(marks)


@frappe.whitelist()
def create_photo_set(values: str | dict[str, Any]):
	_ensure_clinical_access()
	payload = json.loads(values) if isinstance(values, str) else dict(values or {})
	clinical_procedure = payload.get("clinical_procedure")
	if clinical_procedure:
		if not frappe.db.exists("Clinical Procedure", clinical_procedure):
			frappe.throw(_("Clinical Procedure not found."))
		procedure_doc = frappe.get_doc("Clinical Procedure", clinical_procedure)
		payload.setdefault("patient", procedure_doc.get("patient"))
		payload.setdefault("appointment", procedure_doc.get("appointment"))
		encounter_field = _get_clinical_procedure_encounter_field()
		if encounter_field:
			payload.setdefault("encounter", procedure_doc.get(encounter_field))
		payload.setdefault("treatment_entry", _first_treatment_for_procedure(clinical_procedure))
	if not payload.get("patient"):
		frappe.throw(_("Patient is required."))
	if not payload.get("encounter"):
		context = ensure_chart_context(payload.get("patient"), payload.get("appointment"))
		payload["encounter"] = context.get("encounter")
	_ensure_encounter_open(payload.get("encounter"))

	doc = frappe.new_doc("Derma Photo Set")
	mark_doc = (
		frappe.get_doc("Derma Chart Mark", payload.get("chart_mark")) if payload.get("chart_mark") else None
	)
	if mark_doc:
		payload.setdefault("body_view", mark_doc.body_view)
		payload.setdefault("body_region", mark_doc.body_region or mark_doc.body_template)
		payload.setdefault("treatment_entry", mark_doc.treatment_entry)
	if payload.get("body_region"):
		payload["body_region"] = _normalize_derma_body_region(payload.get("body_region"))
	payload["body_view"] = _normalize_derma_body_view(payload.get("body_view"))
	stage = _derive_photo_stage(payload.get("clinical_procedure"))
	payload.setdefault("set_type", PHOTO_STAGE_VISIT if stage == PHOTO_STAGE_VISIT else BEFORE_AFTER_SET_TYPE)
	for field in [
		"patient",
		"appointment",
		"encounter",
		"clinical_procedure",
		"set_type",
		"body_view",
		"body_region",
		"finding",
		"treatment_entry",
		"notes",
	]:
		if field in payload:
			doc.set(field, payload[field])
	_set_patient_name(doc)
	for photo in payload.get("photos") or []:
		doc.append("photos", {**photo, "photo_type": photo.get("photo_type") or stage})
	doc.save(ignore_permissions=True)
	_attach_photo_files(doc)
	if mark_doc:
		mark_doc.photo_set = doc.name
		mark_doc.save(ignore_permissions=True)
	if payload.get("treatment_entry") and frappe.db.exists(
		"Derma Treatment Entry", payload.get("treatment_entry")
	):
		frappe.db.set_value(
			"Derma Treatment Entry",
			payload.get("treatment_entry"),
			"photo_set",
			doc.name,
			update_modified=True,
		)
	return _hydrate_photo_sets([doc.as_dict()])[0]


def _first_treatment_for_procedure(clinical_procedure: str | None) -> str | None:
	if not clinical_procedure or not _has_doctype("Derma Treatment Entry"):
		return None
	return frappe.db.get_value("Derma Treatment Entry", {"clinical_procedure": clinical_procedure}, "name")


def _attach_photo_files(doc) -> None:
	"""Uploads arrive private and unowned; reading one has to follow reading the set."""
	images = [photo.image for photo in doc.photos if photo.image]
	if not images:
		return
	unowned = frappe.get_all(
		"File",
		filters={"file_url": ["in", images], "attached_to_doctype": ["in", ["", None]]},
		pluck="name",
	)
	for name in unowned:
		frappe.db.set_value(
			"File",
			name,
			{"attached_to_doctype": doc.doctype, "attached_to_name": doc.name},
			update_modified=False,
		)


def _normalize_derma_body_view(value: Any) -> str | None:
	"""The chart names body templates freely; the set stores one of a fixed set of views."""
	view = str(value or "").strip()
	if not view:
		return None
	options = frappe.get_meta("Derma Photo Set").get_field("body_view").options.split("\n")
	offered = [option for option in options if option]
	for option in offered:
		if view.lower() == option.lower():
			return option
	for option in offered:
		if option != "Custom" and option.lower() in view.lower():
			return option
	# The chart's own body templates are named freely; "Custom" is what the set has for them.
	return "Custom"


def _derive_photo_stage(clinical_procedure: str | None) -> str:
	"""Capture asks the clinician nothing, so the visit's own state names the stage."""
	if not clinical_procedure:
		return PHOTO_STAGE_VISIT
	status = frappe.db.get_value("Clinical Procedure", clinical_procedure, "status")
	return PHOTO_STAGE_AFTER if status in STARTED_PROCEDURE_STATUSES else PHOTO_STAGE_BEFORE


def _get_editable_photo_set(photo: str) -> str:
	"""A photo is editable from the visit that captured it, by someone who may write it."""
	parent = frappe.db.get_value("Derma Photo", photo, ["parent", "parenttype"], as_dict=True)
	if not parent or parent.parenttype != "Derma Photo Set":
		frappe.throw(_("This photo no longer exists."))
	photo_set = frappe.db.get_value(
		"Derma Photo Set", parent.parent, ["name", "patient", "encounter"], as_dict=True
	)
	if not photo_set:
		frappe.throw(_("This photo no longer exists."))
	frappe.has_permission("Derma Photo Set", "write", doc=photo_set.name, throw=True)
	if not photo_set.encounter:
		frappe.throw(_("This photo is not linked to a visit, so the chart cannot change it."))
	_ensure_encounter_open(photo_set.encounter)
	_ensure_current_encounter(photo_set.patient, photo_set.encounter)
	return photo_set.name


def _ensure_current_encounter(patient: str | None, encounter: str) -> None:
	"""The open visit is the patient's newest one, ordered as _ensure_encounter orders it."""
	if not patient:
		return
	current = frappe.db.get_value(
		"Patient Encounter",
		{"patient": patient, "docstatus": ["<", 2]},
		"name",
		order_by="creation desc",
	)
	if current and current != encounter:
		frappe.throw(_("Photos from an earlier visit can no longer be changed."))


@frappe.whitelist()
def update_photo_stage(photo: str, stage: str):
	"""Correct the stage the upload guessed."""
	_ensure_clinical_access()
	if stage not in CHART_PHOTO_STAGES:
		frappe.throw(_("{0} is not a stage the chart can set.").format(stage))
	photo_set = _get_editable_photo_set(photo)
	frappe.db.set_value("Derma Photo", photo, "photo_type", stage, update_modified=True)
	return _hydrate_photo_sets([frappe.get_doc("Derma Photo Set", photo_set).as_dict()])[0]


@frappe.whitelist()
def delete_photo(photo: str):
	"""Drop one photo, and the set it leaves empty."""
	_ensure_clinical_access()
	if not frappe.db.exists("Derma Photo", photo):
		return {"photo_set": "", "set_deleted": False}
	doc = frappe.get_doc("Derma Photo Set", _get_editable_photo_set(photo))
	doc.photos = [row for row in doc.photos if row.name != photo]
	if not doc.photos:
		_release_photo_set_links(doc.name)
		frappe.delete_doc("Derma Photo Set", doc.name, ignore_permissions=True)
		return {"photo_set": doc.name, "set_deleted": True}
	doc.save(ignore_permissions=True)
	return {"photo_set": doc.name, "set_deleted": False}


def _release_photo_set_links(photo_set: str) -> None:
	"""Frappe refuses to delete a set that anything still links to."""
	for doctype in ("Derma Chart Mark", "Derma Treatment Entry"):
		if _has_doctype(doctype) and _has_field(doctype, "photo_set"):
			frappe.db.set_value(doctype, {"photo_set": photo_set}, "photo_set", None)


@frappe.whitelist()
def get_patient_timeline(patient: str, current_encounter: str | None = None, limit: int = 20):
	_ensure_clinical_access()
	if not patient:
		return []

	marks = []
	if _has_doctype("Derma Chart Mark"):
		marks = frappe.get_all(
			"Derma Chart Mark",
			filters={"patient": patient},
			fields=[
				"name",
				"encounter",
				"appointment",
				"category",
				"body_region",
				"region_label",
				"clinical_procedure",
				"diagnosis",
				"status",
				"modified",
			],
			order_by="modified desc",
			limit=cint(limit),
		)
	findings = frappe.get_all(
		"Derma Finding",
		filters={"patient": patient},
		fields=[
			"name",
			"encounter",
			"appointment",
			"finding_type",
			"diagnosis",
			"body_region",
			"status",
			"severity",
			"modified",
		],
		order_by="modified desc",
		limit=cint(limit),
	)
	treatments = frappe.get_all(
		"Derma Treatment Entry",
		filters={"patient": patient},
		fields=[
			"name",
			"encounter",
			"appointment",
			"workflow",
			"procedure_type",
			"product_name",
			"body_region",
			"dose",
			"dose_unit",
			"modified",
		],
		order_by="modified desc",
		limit=cint(limit),
	)

	rows = []
	for row in marks:
		if current_encounter and row.encounter == current_encounter:
			continue
		rows.append({"kind": "Chart Mark", **row})
	for row in findings:
		if current_encounter and row.encounter == current_encounter:
			continue
		rows.append({"kind": "Finding", **row})
	for row in treatments:
		if current_encounter and row.encounter == current_encounter:
			continue
		rows.append({"kind": "Treatment", **row})
	rows.sort(key=lambda row: row.get("modified") or "", reverse=True)
	return rows[: cint(limit)]


def _load_visit_drawings(encounter: str) -> list[dict[str, Any]]:
	"""An encounter's drawings and its procedures', without the scene JSON."""
	field = _get_clinical_procedure_encounter_field()
	procedures = (
		frappe.get_all("Clinical Procedure", filters={field: encounter, "docstatus": ["<", 2]}, pluck="name")
		if field
		else []
	)
	parents = [("Patient Encounter", encounter), *(("Clinical Procedure", name) for name in procedures)]
	return _load_annotations_for_parents(parents, include_scene=False)


@frappe.whitelist()
def get_previous_visits(patient: str, current_encounter: str | None = None, start: int = 0, page_length: int = 5):
	_ensure_clinical_access()
	if not patient:
		frappe.throw(_("Patient is required."))
	if cint(start) < 0:
		frappe.throw(_("Start must not be negative."), frappe.ValidationError)
	return previous_visits.get_page(
		patient,
		current_encounter,
		cint(start),
		max(1, min(cint(page_length) or 5, 20)),
		_load_visit_drawings,
	)


def _get_visit_moment_fields(encounter: str | None) -> dict[str, Any]:
	if not encounter:
		return {"visit_date": None, "visit_time": None}
	moment = previous_visits.get_visit_moment(encounter)
	return {"visit_date": moment.visit_date, "visit_time": moment.visit_time}


@frappe.whitelist()
def get_visit_summary(encounter: str):
	_ensure_clinical_access()
	if not encounter or not frappe.db.exists("Patient Encounter", encounter):
		frappe.throw(_("Patient Encounter {0} not found.").format(encounter), frappe.DoesNotExistError)
	doc = _resolve_patient_encounter_doc(encounter=encounter)
	if cint(doc.docstatus) == 2:
		frappe.throw(_("Patient Encounter {0} is cancelled.").format(encounter), frappe.ValidationError)
	return {
		"encounter": doc.name,
		"visit_date": previous_visits.get_visit_moment(doc.name).visit_date,
		"practitioner_name": doc.practitioner_name or doc.practitioner or "",
		"mode_label": _(assessment.MODE_LABELS[assessment.get_assessment_mode(doc)]),
		"assessment": assessment.get_summary(doc),
		"patient_advice": assessment.get_plain_text(doc.get("custom_derma_patient_advice")),
		"procedures": _get_visit_summary_procedures(doc),
		"prescriptions": _get_visit_summary_prescriptions(doc),
		"drawings": _load_visit_drawings(doc.name),
	}


def _get_visit_summary_procedures(doc) -> list[dict[str, Any]]:
	"""`title` follows procedureDisplayName in shared/procedure_label.js."""
	if not _get_clinical_procedure_encounter_field():
		return []
	return [
		{
			"name": row.name,
			"title": row.get("template_label") or row.get("procedure_template") or row.name,
			"status": row.get("status") or "",
			"practitioner_name": row.get("practitioner_name") or row.get("practitioner") or "",
			"notes": assessment.get_plain_text(row.get("notes")),
		}
		for row in _get_derma_procedures(doc.patient, encounter=doc.name)
		if cint(row.get("docstatus")) != 2
	]


def _get_visit_summary_prescriptions(doc) -> list[dict[str, Any]]:
	return [
		{
			"drug": row.get("drug_name") or row.get("medication") or row.get("drug_code") or "",
			"dosage": cstr(row.get("dosage")),
			"period": cstr(row.get("period")),
			"comment": assessment.get_plain_text(row.get("comment")),
		}
		for row in _drug_prescription_rows(doc)
	]


@frappe.whitelist()
def get_visit_timeline(patient: str, current_encounter: str | None = None, limit: int = 12):
	_ensure_clinical_access()
	if not patient:
		return []

	visits: dict[str, dict[str, Any]] = {}

	def visit_key(row: dict[str, Any]) -> str:
		return (
			row.get("encounter")
			or row.get("appointment")
			or str(row.get("creation") or row.get("modified") or "")[:10]
			or _("Unlinked")
		)

	def ensure_visit(row: dict[str, Any]) -> dict[str, Any]:
		key = visit_key(row)
		visit = visits.setdefault(
			key,
			{
				"key": key,
				"encounter": row.get("encounter"),
				"appointment": row.get("appointment"),
				"date": str(row.get("creation") or row.get("modified") or row.get("start_date") or "")[:10],
				"modified": row.get("modified") or row.get("creation") or row.get("start_date"),
				"marks": [],
				"procedures": [],
				"photo_sets": [],
				"categories": [],
				"products": [],
				"status_changes": [],
				"totals": {},
				"preview_image": "",
				"summary": "",
			},
		)
		for field in ["encounter", "appointment"]:
			if not visit.get(field) and row.get(field):
				visit[field] = row.get(field)
		if row.get("modified") and (
			not visit.get("modified") or str(row.get("modified")) > str(visit.get("modified"))
		):
			visit["modified"] = row.get("modified")
		return visit

	for mark in _get_previous_marks(patient, current_encounter=current_encounter):
		visit = ensure_visit(mark)
		visit["marks"].append(mark)
		category = mark.get("category")
		if category and category not in visit["categories"]:
			visit["categories"].append(category)
		product = mark.get("product_name") or mark.get("device")
		if product and product not in visit["products"]:
			visit["products"].append(product)
		if mark.get("status") and mark.get("status") not in {"Active", ""}:
			visit["status_changes"].append(
				{
					"status": mark.get("status"),
					"label": mark.get("diagnosis") or category,
					"location": _meaningful_location(mark),
				}
			)
		if mark.get("dose"):
			unit = mark.get("dose_unit") or _("dose")
			visit["totals"][unit] = flt(visit["totals"].get(unit) or 0) + flt(mark.get("dose") or 0)

	for procedure in _get_derma_procedures(patient):
		if current_encounter and _procedure_encounter(procedure) == current_encounter:
			continue
		visit = ensure_visit({**procedure, "encounter": _procedure_encounter(procedure)})
		visit["procedures"].append(procedure)
		category = procedure.get("derma_category")
		if category and category not in visit["categories"]:
			visit["categories"].append(category)

	for photo_set in _get_previous_photo_sets(patient, current_encounter=current_encounter):
		visit = ensure_visit(photo_set)
		visit["photo_sets"].append(photo_set)
		if not visit.get("preview_image") and photo_set.get("preview_image"):
			visit["preview_image"] = photo_set.get("preview_image")

	for visit in visits.values():
		if not visit["preview_image"]:
			linked_photo = next(
				(row.get("preview_image") for row in visit["photo_sets"] if row.get("preview_image")), ""
			)
			visit["preview_image"] = linked_photo
		total_text = ", ".join(
			f"{value:g} {unit}" for unit, value in visit.get("totals", {}).items() if value
		)
		parts = [
			_("{0} mark(s)").format(len(visit["marks"])) if visit["marks"] else "",
			_("{0} procedure(s)").format(len(visit["procedures"])) if visit["procedures"] else "",
			_("{0} photo set(s)").format(len(visit["photo_sets"])) if visit["photo_sets"] else "",
			", ".join(visit["categories"][:4]),
			total_text,
		]
		visit["summary"] = " · ".join(part for part in parts if part)

	rows = sorted(
		visits.values(), key=lambda row: str(row.get("modified") or row.get("date") or ""), reverse=True
	)
	return rows[: cint(limit)]


def _procedure_encounter(row: dict[str, Any]) -> str | None:
	return row.get("patient_encounter") or row.get("custom_patient_encounter") or row.get("encounter")


@frappe.whitelist()
def get_followup_intelligence(
	patient: str, encounter: str | None = None, appointment: str | None = None
) -> list[dict[str, Any]]:
	_ensure_clinical_access()
	from do_derma.readiness import followup

	if not patient:
		return []
	return followup.build(_get_marks(patient, appointment=appointment, encounter=encounter))


@frappe.whitelist()
def create_followup_todo(payload: str | dict[str, Any]):
	_ensure_clinical_access()
	from do_derma.readiness import followup

	values = json.loads(payload) if isinstance(payload, str) else dict(payload or {})
	mark = values.get("mark")
	if not mark:
		frappe.throw(_("Chart mark is required."))
	if not frappe.db.exists("Derma Chart Mark", mark):
		frappe.throw(_("Chart mark {0} was not found.").format(mark))

	existing = followup.open_todos_for_marks([mark]).get(mark)
	if existing:
		return frappe.get_doc("ToDo", existing).as_dict()

	doc = frappe.new_doc("ToDo")
	doc.description = values.get("description") or values.get("title") or _("Derma follow-up")
	doc.reference_type = "Derma Chart Mark"
	doc.reference_name = mark
	doc.status = "Open"
	if _has_field("ToDo", "priority"):
		doc.priority = "High" if values.get("severity") == "high" else "Medium"
	if _has_field("ToDo", "date"):
		doc.date = values.get("due_date") or nowdate()
	if _has_field("ToDo", "allocated_to"):
		doc.allocated_to = values.get("allocated_to") or frappe.session.user
	doc.insert(ignore_permissions=True)
	return doc.as_dict()


def build_visit_narrative(findings: list[dict[str, Any]], treatments: list[dict[str, Any]]) -> str:
	parts = []
	if findings:
		descriptions = []
		for row in findings[:8]:
			label = row.get("diagnosis") or row.get("finding_type") or _("finding")
			location = row.get("region_label") or row.get("body_region") or row.get("body_view")
			status = row.get("status")
			severity = row.get("severity")
			descriptions.append(", ".join(value for value in [severity, label, location, status] if value))
		parts.append(_("Skin findings: {0}.").format("; ".join(descriptions)))
	if treatments:
		descriptions = []
		for row in treatments[:8]:
			label = row.get("procedure_type") or _("treatment")
			product = row.get("product_name")
			dose = " ".join(str(value) for value in [row.get("dose"), row.get("dose_unit")] if value)
			location = row.get("region_label") or row.get("body_region") or row.get("body_view")
			descriptions.append(", ".join(value for value in [label, product, dose, location] if value))
		parts.append(_("Treatments recorded: {0}.").format("; ".join(descriptions)))
	return "\n".join(parts)


def build_mark_narrative(marks: list[dict[str, Any]]) -> str:
	if not marks:
		return ""

	procedure_categories = {"Botox", "Filler", "Laser", "Peel", "Biopsy"}
	findings: list[dict[str, Any]] = []
	procedures: list[dict[str, Any]] = []
	by_category: dict[str, list[dict[str, Any]]] = {}
	for row in marks:
		category = row.get("category") or _("Chart")
		if category in procedure_categories or row.get("clinical_procedure"):
			procedures.append(row)
		else:
			findings.append(row)
		by_category.setdefault(category, []).append(row)

	parts = [_("Derma Chart Summary")]
	if findings:
		finding_lines = []
		for row in findings[:12]:
			label = row.get("diagnosis") or row.get("category") or _("finding")
			location = _meaningful_location(row)
			state = ", ".join(value for value in [row.get("severity"), row.get("status")] if value)
			finding_lines.append("- " + ", ".join(value for value in [label, location, state] if value))
		parts.append(_("Findings") + "\n" + "\n".join(finding_lines))
	if procedures:
		procedure_lines = []
		for category, rows in by_category.items():
			if category not in procedure_categories and not any(
				row.get("clinical_procedure") for row in rows
			):
				continue
			dose_total = sum(flt(row.get("dose") or 0) for row in rows)
			dose_unit = next((row.get("dose_unit") for row in rows if row.get("dose_unit")), "")
			product = next(
				(
					row.get("product_name") or row.get("product_item") or row.get("device")
					for row in rows
					if row.get("product_name") or row.get("product_item") or row.get("device")
				),
				"",
			)
			locations = sorted({loc for row in rows if (loc := _meaningful_location(row))})
			line_bits = [
				_("{0}: {1} mark(s)").format(category, len(rows)),
				", ".join(locations[:4]),
				product,
				f"{dose_total:g} {dose_unit}".strip() if dose_total else "",
			]
			procedure_lines.append("- " + " · ".join(value for value in line_bits if value))
		if procedure_lines:
			parts.append(_("Procedures") + "\n" + "\n".join(procedure_lines))

	product_lines = []
	for row in marks:
		product = row.get("product_name") or row.get("device")
		if not product:
			continue
		dose = _meaningful_dose(row)
		lot = row.get("lot_no")
		expiry = row.get("expiry_date")
		product_line = " · ".join(
			value
			for value in [product, dose, f"Lot {lot}" if lot else "", f"Exp {expiry}" if expiry else ""]
			if value
		)
		if product_line and product_line not in product_lines:
			product_lines.append("- " + product_line)
	if product_lines:
		parts.append(_("Products / Devices") + "\n" + "\n".join(product_lines[:12]))

	photo_count = len([row for row in marks if row.get("photo_set")])
	annotation_count = len([row for row in marks if row.get("annotation")])
	if photo_count:
		parts.append(
			_("Photos / Annotations")
			+ "\n"
			+ _("- {0} chart mark(s) include linked photo evidence.").format(photo_count)
		)
	elif annotation_count:
		parts.append(
			_("Photos / Annotations")
			+ "\n"
			+ _("- {0} chart mark(s) include linked drawings.").format(annotation_count)
		)

	follow_up_rows = [
		row
		for row in marks
		if row.get("status") in {"Monitoring", "Follow-up", "Worse", "Biopsied", "Excised"}
	]
	if follow_up_rows:
		lines = []
		for row in follow_up_rows[:8]:
			lines.append(
				"- "
				+ ", ".join(
					value
					for value in [
						row.get("status"),
						row.get("diagnosis") or row.get("category"),
						_meaningful_location(row),
					]
					if value
				)
			)
		parts.append(_("Follow-up") + "\n" + "\n".join(lines))
	return "\n\n".join(parts)


def _meaningful_location(row: dict[str, Any]) -> str | None:
	location = row.get("region_label") or row.get("body_region") or row.get("body_view")
	if (
		location
		and location == row.get("body_view")
		and row.get("body_region") in {"Face", "Body", "Scalp", "Hands"}
	):
		return row.get("body_view")
	return location


def _meaningful_dose(row: dict[str, Any]) -> str | None:
	dose = row.get("dose")
	if dose in (None, "", 0):
		return None
	unit = row.get("dose_unit")
	return " ".join(str(value) for value in [dose, unit] if value)
