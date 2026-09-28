from __future__ import annotations

from collections.abc import Callable
from typing import Any

import frappe
from frappe import _

from do_derma import assessment

BATCH_SIZE = 20
SCAN_LIMIT = 100


def get_page(
	patient: str,
	current_encounter: str | None,
	start: int,
	page_length: int,
	load_drawings: Callable[[str], list[dict[str, Any]]],
) -> dict[str, Any]:
	"""Visits before the open one with a drawing or an assessment, newest first.

	`next_start` is an encounter offset, not a visit count: visits with neither are skipped.
	One call reads at most SCAN_LIMIT encounters; a capped scan answers `has_more` so Load more continues.
	"""
	visits: list[dict[str, Any]] = []
	cutoff = _get_cutoff(current_encounter)
	offset = start
	while offset - start < SCAN_LIMIT:
		batch = _get_encounters(patient, cutoff, offset, min(BATCH_SIZE, start + SCAN_LIMIT - offset))
		if not batch:
			return {"visits": visits, "has_more": False, "next_start": offset}
		for row in batch:
			visit = _build_visit(row, load_drawings)
			if visit and len(visits) == page_length:
				return {"visits": visits, "has_more": True, "next_start": offset}
			if visit:
				visits.append(visit)
			offset += 1
	return {"visits": visits, "has_more": True, "next_start": offset}


def get_latest_encounter(patient: str) -> str | None:
	"""The patient's newest visit that is not cancelled, in the order Previous Visits lists them."""
	latest = _get_encounters(patient, None, 0, 1)
	return latest[0].name if latest else None


def _get_cutoff(current_encounter: str | None) -> dict[str, Any] | None:
	if not current_encounter:
		return None
	cutoff = frappe.db.get_value(
		"Patient Encounter", current_encounter, ["encounter_date", "creation"], as_dict=True
	)
	if not cutoff:
		frappe.throw(
			_("Patient Encounter {0} not found.").format(current_encounter), frappe.DoesNotExistError
		)
	return cutoff


def _get_encounters(
	patient: str, cutoff: dict[str, Any] | None, start: int, limit: int
) -> list[dict[str, Any]]:
	filters: list[list[Any]] = [["patient", "=", patient], ["docstatus", "<", 2]]
	or_filters: list[list[Any]] = []
	if cutoff:
		# Strictly earlier in (encounter_date desc, creation desc) order.
		filters.append(["encounter_date", "<=", cutoff.encounter_date])
		or_filters = [["encounter_date", "<", cutoff.encounter_date], ["creation", "<", cutoff.creation]]
	return frappe.get_all(
		"Patient Encounter",
		filters=filters,
		or_filters=or_filters,
		fields=["name", "encounter_date", "practitioner", "practitioner_name"],
		order_by="encounter_date desc, creation desc",
		limit_start=start,
		limit_page_length=limit,
	)


def _build_visit(row, load_drawings) -> dict[str, Any] | None:
	drawings = load_drawings(row.name)
	preview = assessment.get_preview(frappe.get_doc("Patient Encounter", row.name))
	if not drawings and not preview:
		return None
	return {
		"encounter": row.name,
		"encounter_date": row.encounter_date,
		"practitioner_name": row.practitioner_name or row.practitioner or "",
		"drawings": drawings,
		"assessment": preview,
	}
