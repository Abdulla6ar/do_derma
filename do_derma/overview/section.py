from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, cstr, get_time, getdate, nowdate

from do_derma import api
from do_derma.assessment import HP_FIELDS, MODE_FIELD, SOAP_FIELDS
from do_derma.overview.callouts import get_callouts, get_prescription_facts, get_series_facts
from do_derma.overview.photos import MAX_PHOTOS, get_photos
from do_derma.overview.summary import ADVICE_FIELDS, get_summary
from do_derma.overview.text import existing_fields, join_unique
from do_derma.overview.treatments import ARCHIVED, DERMA_TEMPLATE_FLAGS, get_procedure_row, get_treatment_groups, load_marks, load_procedures
from do_derma.schema import COMPLETION_OVERRIDE_FIELD

SECTION_KEY = "derma"
MAX_SUMMARY = 5
MAX_PROCEDURES = 8
MAX_CALLOUTS = 4
MAX_FACTS = 5
ENCOUNTER_FIELDS = [
	"name",
	"appointment",
	"appointment_type",
	"encounter_date",
	"encounter_time",
	"practitioner_name",
	"docstatus",
	"creation",
	"custom_appointment_category",
	"custom_encounter_state",
	MODE_FIELD,
	*SOAP_FIELDS,
	*HP_FIELDS,
	*ADVICE_FIELDS,
	COMPLETION_OVERRIDE_FIELD,
	"custom_symptoms_notes",
	"custom_chief_complaints",
	"custom_physical_examination",
	"custom_diagnosis_note",
]


def get_patient_overview_section(
	patient: str, appointment: str | None = None, exclude_encounter: str | None = None
) -> dict[str, Any] | None:
	"""The patient's last derma visit, or None when there is none or the user may not see it.

	`exclude_encounter` is today's in-progress encounter, which must not pass for the last visit.
	`appointment` is accepted for the hook's signature; the excluded encounter already covers it.
	Not whitelisted - do_health calls it server-side after its own Patient permission check.
	"""
	# The same gate as api._ensure_clinical_access, answered with None instead of a throw: the
	# drawer is opened by the front desk too, and it simply shows no derma card to them.
	if not patient or not set(frappe.get_roles()) & api.CLINICAL_ACCESS_ROLES:
		return None

	visit = _latest_derma_encounter(patient, exclude_encounter)
	if not visit:
		return None

	marks = load_marks(patient, visit.name, exclude_encounter)
	visit_marks = [mark for mark in marks if mark.encounter == visit.name]
	procedures = load_procedures(patient, visit.name, visit_marks)
	groups = get_treatment_groups(visit_marks, procedures)
	treated_categories = {group["category"] for group in groups if group["category"]}

	summary = get_summary(visit, visit_marks)
	procedure_rows = [get_procedure_row(group) for group in groups]
	callouts = get_callouts(visit, marks, groups, summary)
	facts = get_series_facts(marks, visit, treated_categories)
	facts = facts[: MAX_FACTS - 1] + get_prescription_facts(visit.name)
	photos = get_photos(patient, visit, exclude_encounter, list(procedures), visit_marks)

	return {
		"key": SECTION_KEY,
		"specialty": _("Derma"),
		"title": _("Last derma visit"),
		"icon": "fa-regular fa-hand-holding-medical",
		"accent": "violet",
		"visit": _visit_header(visit),
		"summary": summary[:MAX_SUMMARY],
		"procedures": procedure_rows[:MAX_PROCEDURES],
		"procedure_total": len(procedure_rows),
		"callouts": callouts[:MAX_CALLOUTS],
		"facts": facts[:MAX_FACTS],
		"photos": photos[:MAX_PHOTOS],
	}


def _latest_derma_encounter(patient: str, exclude_encounter: str | None) -> frappe._dict | None:
	conditions: list[list[Any]] = []
	candidates = _derma_encounter_candidates(patient)
	if candidates:
		conditions.append(["name", "in", candidates])
	conditions.extend(
		[fieldname, "is", "set"]
		for fieldname in (MODE_FIELD, *SOAP_FIELDS, *HP_FIELDS, *ADVICE_FIELDS)
		if api._has_field("Patient Encounter", fieldname)
	)
	if not conditions:
		return None

	filters: list[list[Any]] = [["patient", "=", patient], ["docstatus", "<", 2]]
	if exclude_encounter:
		filters.append(["name", "!=", exclude_encounter])
	rows = frappe.get_all(
		"Patient Encounter",
		filters=filters,
		or_filters=conditions,
		fields=existing_fields("Patient Encounter", ENCOUNTER_FIELDS),
		# Clinical date, never creation: legacy visits were imported long after they happened.
		order_by="encounter_date desc, encounter_time desc, creation desc",
		limit=1,
	)
	return rows[0] if rows else None


def _derma_encounter_candidates(patient: str) -> list[str]:
	"""Encounters the chart has left a record on, in one round trip.

	Plucked by patient first rather than tested per encounter: none of these tables indexes
	`encounter`, so a correlated EXISTS would scan each of them once for every visit the
	patient has ever had.
	"""
	selects = [
		"select encounter from `tabDerma Chart Mark` where patient = %(patient)s"
		" and ifnull(encounter, '') != '' and ifnull(status, '') != %(archived)s",
		"select encounter from `tabDerma Treatment Entry` where patient = %(patient)s"
		" and ifnull(encounter, '') != ''",
		"select encounter from `tabDerma Photo Set` where patient = %(patient)s"
		" and ifnull(encounter, '') != ''",
	]
	encounter_field = api._get_clinical_procedure_encounter_field()
	flags = [flag for flag in DERMA_TEMPLATE_FLAGS if api._has_field("Clinical Procedure Template", flag)]
	if encounter_field and flags:
		flagged = " or ".join(f"ifnull(cpt.`{flag}`, '') != ''" for flag in flags)
		live = " and ifnull(cp.custom_entered_in_error, 0) = 0" if api._has_field("Clinical Procedure", "custom_entered_in_error") else ""
		selects.append(
			f"select cp.`{encounter_field}` from `tabClinical Procedure` cp"
			" join `tabClinical Procedure Template` cpt on cpt.name = cp.procedure_template"
			f" where cp.patient = %(patient)s and cp.docstatus < 2 and ifnull(cp.`{encounter_field}`, '') != ''"
			f" and ({flagged}){live}"
		)
	rows = frappe.db.sql(" union ".join(selects), {"patient": patient, "archived": ARCHIVED})
	return [row[0] for row in rows if row[0]]


def _visit_header(visit: frappe._dict) -> dict[str, Any]:
	visit_date = getdate(visit.encounter_date or visit.creation)
	category = join_unique(
		[visit.get("custom_appointment_category"), visit.get("custom_encounter_state")], " · "
	) or cstr(visit.get("appointment_type"))
	warning = None
	# A draft from an earlier day is a session nobody closed; today's may still be under way.
	if cint(visit.docstatus) == 0 and visit_date < getdate(nowdate()):
		warning = _("Session not completed")
	return {
		"encounter": visit.name,
		"appointment": visit.get("appointment") or None,
		"date": str(visit_date),
		"time": get_time(visit.encounter_time).strftime("%H:%M:%S") if visit.get("encounter_time") else None,
		"practitioner_name": visit.get("practitioner_name") or None,
		"category": category or None,
		"warning": warning,
	}
