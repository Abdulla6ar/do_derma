from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import add_days, cstr, date_diff, flt, getdate, nowdate

from do_derma.overview.summary import get_advice
from do_derma.overview.text import clean, clip, listed, short_date, unique
from do_derma.overview.treatments import SERIES_WINDOW_DAYS, WATCH_STATUSES, get_area_label, get_dose_text, is_treatment
from do_derma.readiness.followup import NEXT_SESSION_INTERVALS, STATUS_DUE_DAYS
from do_derma.schema import COMPLETION_OVERRIDE_FIELD

TONE_RANK = {"danger": 0, "warning": 1, "info": 2}
MAX_LISTED = 3


def get_callouts(
	visit: frappe._dict, marks: list[frappe._dict], groups: list[dict[str, Any]], summary: list[dict[str, Any]]
) -> list[dict[str, Any]]:
	callouts = [*_watch_callouts(visit, marks), *_next_session_callouts(visit, groups)]

	override = clean(visit.get(COMPLETION_OVERRIDE_FIELD), 200)
	if override:
		callouts.append({"label": _("Completed with override"), "value": override, "tone": "warning"})

	advice = get_advice(visit)
	if advice and advice not in {item["value"] for item in summary}:
		callouts.append({"label": _("Advice given"), "value": advice, "tone": "info"})

	# Stable: within a tone the order above - lesions, then sessions - still holds.
	return sorted(callouts, key=lambda callout: TONE_RANK[callout["tone"]])


def _watch_callouts(visit: frappe._dict, marks: list[frappe._dict]) -> list[dict[str, Any]]:
	"""Lesions under watch at that visit, and those from earlier visits nobody has charted since.

	A lesion is followed by carrying its mark forward to the next visit, which copies the lesion
	id, category and position. The latest mark of each lesion therefore holds its current status.
	"""
	latest: dict[tuple, frappe._dict] = {}
	for mark in marks:
		key = _lesion_key(mark)
		current = latest.get(key)
		if not current or _mark_order(mark) >= _mark_order(current):
			latest[key] = mark

	by_status: dict[str, list[frappe._dict]] = {}
	for mark in latest.values():
		if mark.status in WATCH_STATUSES:
			by_status.setdefault(mark.status, []).append(mark)

	labels = {
		"Biopsied": _("Biopsied — pathology pending"),
		"Worse": _("Worsening lesion"),
		"Excised": _("Excised — follow-up"),
		"Monitoring": _("Under monitoring"),
	}
	callouts = []
	for status in WATCH_STATUSES:
		lesions = sorted(by_status.get(status, []), key=_mark_order)
		if not lesions:
			continue
		texts = []
		for mark in lesions:
			text = " · ".join(part for part in [mark.diagnosis or mark.category or _("Lesion"), get_area_label(mark)] if part)
			if mark.encounter != visit.name:
				text += " (" + short_date(mark.encounter_date) + ")"
			texts.append(text)
		due = add_days(lesions[0].encounter_date, STATUS_DUE_DAYS[status])
		overdue = date_diff(nowdate(), due) > 0
		tone = "danger" if status == "Biopsied" else "warning" if status in ("Worse", "Excised") or overdue else "info"
		callouts.append({"label": labels[status], "value": f"{listed(unique(texts), MAX_LISTED)} · {_due_text(due)}", "tone": tone})
	return callouts


def _next_session_callouts(visit: frappe._dict, groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
	"""When the course is due again, counted from the session itself.

	followup.build counts the same intervals from today, which is right for the chart of the
	visit under way and wrong for a visit that happened weeks ago.
	"""
	due_by_category: dict[str, Any] = {}
	for group in groups:
		interval = NEXT_SESSION_INTERVALS.get(group["category"])
		if not interval:
			continue
		due = getdate(add_days(group["session_date"] or visit.encounter_date, interval))
		if group["category"] not in due_by_category or due < due_by_category[group["category"]]:
			due_by_category[group["category"]] = due
	if not due_by_category:
		return []
	overdue = any(date_diff(nowdate(), due) > 0 for due in due_by_category.values())
	value = "; ".join(f"{category} · {_due_text(due)}" for category, due in sorted(due_by_category.items(), key=lambda item: item[1]))
	return [{"label": _("Next session due"), "value": value, "tone": "warning" if overdue else "info"}]


def _lesion_key(mark: frappe._dict) -> tuple:
	if cstr(mark.lesion_id).strip():
		return ("lesion", cstr(mark.lesion_id).strip().lower())
	return ("position", mark.category, mark.body_view, round(flt(mark.x_percent), 1), round(flt(mark.y_percent), 1))


def _mark_order(mark: frappe._dict) -> tuple[str, str]:
	return (cstr(mark.encounter_date), cstr(mark.creation))


def _due_text(due) -> str:
	days = date_diff(nowdate(), due)
	if days > 0:
		return _("due {0} · overdue {1}d").format(short_date(due), days)
	if days == 0:
		return _("due today")
	return _("due {0}").format(short_date(due))


def get_series_facts(marks: list[frappe._dict], visit: frappe._dict, treated_categories: set[str]) -> list[dict[str, Any]]:
	"""Treatment courses over the last 12 months, e.g. "Botox · 3 sessions · 64 U in 12 months".

	A category treated once, at the visit on the card, is left out: the procedure row says it all.
	"""
	cutoff = getdate(add_days(nowdate(), -SERIES_WINDOW_DAYS))
	series: dict[str, dict[str, Any]] = {}
	for mark in marks:
		if not mark.category or not mark.encounter_date or getdate(mark.encounter_date) < cutoff or not is_treatment(mark):
			continue
		entry = series.setdefault(mark.category, {"sessions": set(), "totals": {}, "last": None})
		entry["sessions"].add(mark.encounter)
		if flt(mark.dose) > 0:
			entry["totals"][mark.dose_unit] = entry["totals"].get(mark.dose_unit, 0) + flt(mark.dose)
		entry["last"] = max(filter(None, [entry["last"], getdate(mark.encounter_date)]))

	facts = []
	for category, entry in sorted(series.items(), key=lambda item: (item[1]["last"], len(item[1]["sessions"])), reverse=True):
		sessions = len(entry["sessions"])
		if sessions < 2 and category in treated_categories:
			continue
		parts = [_("1 session") if sessions == 1 else _("{0} sessions").format(sessions)]
		parts.extend(get_dose_text(value, unit) for unit, value in entry["totals"].items())
		value = _("{0} in 12 months").format(" · ".join(parts))
		if entry["last"] != getdate(visit.encounter_date):
			value += " · " + _("last {0}").format(short_date(entry["last"]))
		facts.append({"label": category, "value": value})
	return facts


def get_prescription_facts(encounter: str) -> list[dict[str, Any]]:
	rows = frappe.get_all(
		"Drug Prescription",
		filters={"parent": encounter, "parenttype": "Patient Encounter", "parentfield": "drug_prescription"},
		fields=["drug_name", "drug_code", "dosage", "period"],
		order_by="idx asc",
		limit=0,
	)
	drugs = unique(
		" · ".join(part for part in [cstr(row.drug_name or row.drug_code).strip(), cstr(row.dosage), cstr(row.period)] if part)
		for row in rows
		if row.drug_name or row.drug_code
	)
	return [{"label": _("Prescribed"), "value": clip(listed(drugs, MAX_LISTED, "; "))}] if drugs else []
