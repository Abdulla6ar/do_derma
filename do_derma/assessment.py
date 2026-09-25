"""Assessment Mode resolution, layout and serialisation for the derma chart.

Owns everything about how a visit is documented: which of the three Assessment Modes
an encounter is written in, the field layout each mode renders, and the rules that
stamp a mode without ever discarding the other mode's content.
"""

from typing import Any

import frappe
from frappe import _
from frappe.utils import cast, cint, cstr, strip_html
from frappe.utils.html_utils import unescape_html

from do_derma.settings import SETTINGS_DOCTYPE, get_settings_doc

STRUCTURED = "Structured"
SOAP = "SOAP"
HP = "HP"
ASSESSMENT_MODES = (STRUCTURED, SOAP, HP)
MODE_LABELS = {STRUCTURED: "Structured Assessment", SOAP: "SOAP Note", HP: "History & Physical"}

MODE_FIELD = "custom_derma_assessment_mode"
PRACTITIONER_DEFAULT_FIELD = "custom_derma_default_assessment_mode"

SOAP_FIELDS = (
	"custom_derma_soap_subjective",
	"custom_derma_soap_objective",
	"custom_derma_soap_assessment",
	"custom_derma_soap_plan",
)
HP_FIELDS = (
	"custom_derma_hp_chief_complaint",
	"custom_derma_hp_history",
	"custom_derma_hp_past_history",
	"custom_derma_hp_examination",
	"custom_derma_hp_assessment",
	"custom_derma_hp_plan",
)
# Free-text modes: fixed custom fields, rendered as one textarea per field.
MODE_FIELDS = {SOAP: SOAP_FIELDS, HP: HP_FIELDS}

# The Structured Assessment defaults. Seeded into Derma Settings once; a clinic
# that edits the list keeps its edit across migrates.
DEFAULT_STRUCTURED_FIELDS = (
	"symptoms",
	"custom_symptom_duration",
	"custom_symptoms_notes",
	"custom_illness_progression",
	"diagnosis",
	"custom_differential_diagnosis",
	"custom_diagnosis_note",
	"custom_physical_examination",
	"custom_other_examination",
)

TABLE_FIELD_TYPES = {"Table", "Table MultiSelect"}
NO_VALUE_FIELD_TYPES = {
	"Section Break",
	"Column Break",
	"Tab Break",
	"Button",
	"Image",
	"HTML",
	"Fold",
	"Heading",
}
CHILD_INTERNAL_FIELDS = {
	"name",
	"doctype",
	"parent",
	"parenttype",
	"parentfield",
	"idx",
	"owner",
	"creation",
	"modified",
	"modified_by",
	"docstatus",
}


def has_field(doctype: str, fieldname: str) -> bool:
	try:
		return bool(frappe.get_meta(doctype).has_field(fieldname))
	except Exception:
		return False


def mode_is_supported(mode: str) -> bool:
	"""A free-text mode needs its custom fields; a site that has not migrated lacks them."""
	if mode == STRUCTURED:
		return True
	if not has_field("Patient Encounter", MODE_FIELD):
		return False
	return all(has_field("Patient Encounter", fieldname) for fieldname in MODE_FIELDS.get(mode, ()))


def soap_is_supported() -> bool:
	return mode_is_supported(SOAP)


def hp_is_supported() -> bool:
	return mode_is_supported(HP)


def available_modes() -> list[str]:
	return [mode for mode in ASSESSMENT_MODES if mode_is_supported(mode)]


def get_structured_fieldnames() -> list[str]:
	"""Configured field list, falling back to the defaults when unset."""
	settings = get_settings_doc()
	rows = settings.get("structured_assessment_fields") if settings else None
	configured = [row.fieldname for row in rows or [] if row.fieldname and cint(row.enabled)]
	return configured or list(DEFAULT_STRUCTURED_FIELDS)


def get_structured_layout() -> list[dict[str, Any]]:
	"""Layout rows for the configured fields, silently dropping absent ones."""
	meta = frappe.get_meta("Patient Encounter")
	layout = []
	for fieldname in get_structured_fieldnames():
		df = meta.get_field(fieldname)
		if df:
			layout.append(_layout_row(df))
	return layout


def get_mode_layout(mode: str) -> list[dict[str, Any]]:
	"""Fixed-field layout for SOAP or H&P; empty when the site lacks the fields."""
	if not mode_is_supported(mode):
		return []
	meta = frappe.get_meta("Patient Encounter")
	return [_layout_row(meta.get_field(fieldname)) for fieldname in MODE_FIELDS[mode] if meta.get_field(fieldname)]


def get_soap_layout() -> list[dict[str, Any]]:
	return get_mode_layout(SOAP)


def get_hp_layout() -> list[dict[str, Any]]:
	return get_mode_layout(HP)


def get_layout(mode: str) -> list[dict[str, Any]]:
	return get_mode_layout(mode) if mode in MODE_FIELDS else get_structured_layout()


def get_assessment_mode(encounter_doc) -> str:
	"""The stamped mode always wins, so a note reopens as it was written."""
	stamped = _stamped_mode(encounter_doc)
	if stamped:
		return stamped
	if not soap_is_supported():
		return STRUCTURED
	return practitioner_default(encounter_doc.get("practitioner")) or STRUCTURED


def practitioner_default(practitioner: str | None) -> str:
	if not practitioner or not has_field("Healthcare Practitioner", PRACTITIONER_DEFAULT_FIELD):
		return ""
	value = frappe.db.get_value("Healthcare Practitioner", practitioner, PRACTITIONER_DEFAULT_FIELD)
	return value if value in ASSESSMENT_MODES else ""


def serialize_values(encounter_doc, layout: list[dict[str, Any]]) -> dict[str, Any]:
	values = {}
	for row in layout:
		fieldname = row.get("fieldname")
		if not fieldname or not row.get("is_value_field"):
			continue
		if row.get("fieldtype") in TABLE_FIELD_TYPES:
			allowed = {field.get("fieldname") for field in row.get("fields") or [] if field.get("fieldname")}
			# Child rows are Document instances, not dicts - `key in child` raises
			# TypeError on frappe v16 (Document no longer implements __contains__).
			values[fieldname] = [
				{key: child_values.get(key) for key in allowed if key in child_values}
				for child_values in (child.as_dict() for child in encounter_doc.get(fieldname) or [])
			]
		else:
			values[fieldname] = encounter_doc.get(fieldname)
	return values


def normalized_values(values: dict[str, Any], layout: list[dict[str, Any]]) -> dict[str, Any]:
	"""Values in one comparable form, so a native doc value and its raw JSON twin compare equal.

	Each value is cast to its own field's Python type first - the rule `BaseDocument.cast` uses -
	so a Float `1.0` and its JSON `1` land on one value instead of `"1.0" != "1"`, then stringified
	for a simple diff. Table rows are trimmed to the row's own child fields, like `serialize_values`
	does, with each child value cast by its own child fieldtype the same way.
	"""
	field_map = {row["fieldname"]: row for row in layout if row.get("fieldname")}
	normalized = {}
	for fieldname, value in values.items():
		row = field_map.get(fieldname)
		if row and row.get("fieldtype") in TABLE_FIELD_TYPES:
			child_types = {
				field["fieldname"]: field.get("fieldtype")
				for field in row.get("fields") or []
				if field.get("fieldname")
			}
			normalized[fieldname] = [
				{key: _cast_str(child.get(key), child_types[key]) for key in child_types if key in child}
				for child in (value or [])
				if isinstance(child, dict)
			]
		else:
			normalized[fieldname] = _cast_str(value, row.get("fieldtype") if row else None)
	return normalized


def _cast_str(value: Any, fieldtype: str | None) -> str:
	return cstr(cast(fieldtype, value))


def read_assessment(encounter_doc) -> dict[str, Any]:
	"""The full assessment payload for one encounter, in both modes."""
	mode = get_assessment_mode(encounter_doc)
	structured_layout = get_structured_layout()
	soap_layout = get_soap_layout()
	hp_layout = get_hp_layout()
	values = serialize_values(encounter_doc, structured_layout)
	soap_values = serialize_values(encounter_doc, soap_layout)
	hp_values = serialize_values(encounter_doc, hp_layout)
	return {
		"encounter": encounter_doc.name,
		"docstatus": cint(encounter_doc.docstatus),
		"mode": mode,
		"is_stamped": bool(_stamped_mode(encounter_doc)),
		"is_filled": any(_has_content(value) for value in [*values.values(), *soap_values.values(), *hp_values.values()]),
		"available_modes": available_modes(),
		"soap_supported": soap_is_supported(),
		"hp_supported": hp_is_supported(),
		"layout": structured_layout,
		"values": values,
		"soap_layout": soap_layout,
		"soap_values": soap_values,
		"hp_layout": hp_layout,
		"hp_values": hp_values,
		"patient_advice": cstr(encounter_doc.get("custom_derma_patient_advice")),
		"patient_advice_ar": cstr(encounter_doc.get("custom_derma_patient_advice_ar")),
		"print_patient_advice": cint(encounter_doc.get("custom_derma_print_patient_advice")),
		"patient_advice_language": cstr(encounter_doc.get("custom_derma_patient_advice_language")) or "Auto",
		"context_values": {
			"patient": encounter_doc.get("patient"),
			"appointment": encounter_doc.get("appointment"),
			"practitioner": encounter_doc.get("practitioner"),
		},
	}


def get_preview(encounter_doc) -> list[dict[str, str]]:
	"""The documented format's filled fields as label and plain text."""
	layout = get_layout(get_assessment_mode(encounter_doc))
	values = serialize_values(encounter_doc, layout)
	preview = []
	for row in layout:
		text = _preview_text(row, values.get(row.get("fieldname")))
		if text:
			preview.append({"label": _(row.get("label") or row.get("fieldname")), "value": text})
	return preview


def _preview_text(row: dict[str, Any], value: Any) -> str:
	if row.get("fieldtype") in TABLE_FIELD_TYPES:
		return _("{0} row(s)").format(len(value)) if value else ""
	if row.get("fieldtype") == "Check":
		return _("Yes") if cint(value) else ""
	return unescape_html(strip_html(cstr(value or ""))).strip()


def empty_assessment() -> dict[str, Any]:
	structured_layout = get_structured_layout()
	return {
		"encounter": "",
		"docstatus": None,
		"mode": STRUCTURED,
		"is_stamped": False,
		"is_filled": False,
		"available_modes": available_modes(),
		"soap_supported": soap_is_supported(),
		"hp_supported": hp_is_supported(),
		"layout": structured_layout,
		"values": {},
		"soap_layout": get_soap_layout(),
		"soap_values": {},
		"hp_layout": get_hp_layout(),
		"hp_values": {},
		"patient_advice": "",
		"patient_advice_ar": "",
		"print_patient_advice": 0,
		"patient_advice_language": "Auto",
		"context_values": {},
	}


def apply_assessment(encounter_doc, values: dict[str, Any], mode: str | None = None) -> None:
	"""Write whitelisted fields for one mode, then stamp the mode if content landed.

	Only fields belonging to the resolved mode are writable, so a client cannot
	name an arbitrary Patient Encounter column.
	"""
	if cint(encounter_doc.docstatus) == 2:
		frappe.throw(_("Cancelled encounters cannot be edited."))

	target_mode = normalize_mode(mode) or get_assessment_mode(encounter_doc)
	layout = get_layout(target_mode)
	field_map = {row["fieldname"]: row for row in layout if row.get("is_value_field")}
	only_allow_on_submit = cint(encounter_doc.docstatus) == 1

	wrote_content = False
	for fieldname, value in (values or {}).items():
		row = field_map.get(fieldname)
		if not row:
			continue
		if only_allow_on_submit and not cint(row.get("allow_on_submit")):
			continue
		if row.get("fieldtype") in TABLE_FIELD_TYPES:
			child_fields = {
				field.get("fieldname") for field in row.get("fields") or [] if field.get("fieldname")
			}
			encounter_doc.set(
				fieldname,
				[
					{key: child.get(key) for key in child_fields if key in child}
					for child in (value or [])
					if isinstance(child, dict)
				],
			)
		else:
			encounter_doc.set(fieldname, value)
		if _has_content(value):
			wrote_content = True

	if wrote_content and not _stamped_mode(encounter_doc) and has_field("Patient Encounter", MODE_FIELD):
		encounter_doc.set(MODE_FIELD, target_mode)


def stamp_mode(encounter_doc, mode: str) -> None:
	"""Change the documented format. Writes no content and deletes nothing."""
	target_mode = normalize_mode(mode)
	if not target_mode:
		frappe.throw(_("{0} is not a valid Assessment Mode.").format(mode), frappe.ValidationError)
	if not mode_is_supported(target_mode):
		frappe.throw(_("{0} fields are not installed on this site.").format(MODE_LABELS[target_mode]))
	if cint(encounter_doc.docstatus) != 0:
		frappe.throw(_("The documentation format can only be changed while the encounter is a draft."))
	if not has_field("Patient Encounter", MODE_FIELD):
		frappe.throw(_("Assessment Mode is not installed on this site."))
	encounter_doc.set(MODE_FIELD, target_mode)


def normalize_mode(mode: str | None) -> str:
	return mode if mode in ASSESSMENT_MODES else ""


def ensure_derma_settings_defaults() -> bool:
	"""Seed the structured field list once. Never overwrites a clinic's edit."""
	if not frappe.db.exists("DocType", SETTINGS_DOCTYPE):
		return False
	settings = frappe.get_doc(SETTINGS_DOCTYPE)
	if settings.get("structured_assessment_fields"):
		return False
	for fieldname in DEFAULT_STRUCTURED_FIELDS:
		settings.append("structured_assessment_fields", {"fieldname": fieldname, "enabled": 1})
	settings.save(ignore_permissions=True)
	return True


def _stamped_mode(encounter_doc) -> str:
	return normalize_mode(encounter_doc.get(MODE_FIELD))


def _has_content(value: Any) -> bool:
	if value is None:
		return False
	if isinstance(value, str):
		return bool(value.strip())
	if isinstance(value, list | tuple):
		return bool(value)
	return True


def _layout_row(df) -> dict[str, Any]:
	row = {
		"fieldname": df.fieldname,
		"fieldtype": df.fieldtype,
		"label": df.label,
		"options": df.options,
		"reqd": cint(df.reqd),
		"read_only": cint(df.read_only),
		"hidden": cint(df.hidden),
		"depends_on": df.depends_on,
		"read_only_depends_on": df.read_only_depends_on,
		"mandatory_depends_on": df.mandatory_depends_on,
		"default": df.default,
		"allow_on_submit": cint(df.allow_on_submit),
		"is_value_field": df.fieldtype not in NO_VALUE_FIELD_TYPES,
		"show_if_empty": cint(getattr(df, "show_if_empty", 0)),
		"layout_key": f"{df.fieldname}-{df.idx}",
		"idx": df.idx,
	}
	if df.fieldtype in TABLE_FIELD_TYPES and df.options:
		row["fields"] = child_table_layout(df.options)
	return row


def child_table_layout(doctype: str) -> list[dict[str, Any]]:
	if not frappe.db.exists("DocType", doctype):
		return []
	fields = []
	for df in frappe.get_meta(doctype).fields:
		if not df.fieldname or df.fieldname in CHILD_INTERNAL_FIELDS:
			continue
		fields.append(
			{
				"fieldname": df.fieldname,
				"fieldtype": df.fieldtype,
				"label": df.label,
				"options": df.options,
				"reqd": cint(df.reqd),
				"read_only": cint(df.read_only),
				"hidden": cint(df.hidden),
				"in_list_view": cint(df.in_list_view),
				"columns": cint(getattr(df, "columns", 0) or 0),
				"default": df.default,
			}
		)
	return fields
