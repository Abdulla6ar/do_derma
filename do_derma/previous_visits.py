from __future__ import annotations

from collections.abc import Callable
from typing import Any

import frappe

from do_derma import assessment

BATCH_SIZE = 20


def get_page(
	patient: str,
	current_encounter: str | None,
	start: int,
	page_length: int,
	load_drawings: Callable[[str], list[dict[str, Any]]],
) -> dict[str, Any]:
	"""Earlier visits with a drawing or an assessment, newest first.

	`next_start` is an encounter offset, not a visit count: visits with neither are skipped.
	"""
	visits: list[dict[str, Any]] = []
	offset = start
	while True:
		batch = _get_encounters(patient, current_encounter, offset)
		if not batch:
			return {"visits": visits, "has_more": False, "next_start": offset}
		for row in batch:
			visit = _build_visit(row, load_drawings)
			if visit and len(visits) == page_length:
				return {"visits": visits, "has_more": True, "next_start": offset}
			if visit:
				visits.append(visit)
			offset += 1


def _get_encounters(patient: str, current_encounter: str | None, start: int) -> list[dict[str, Any]]:
	filters: dict[str, Any] = {"patient": patient, "docstatus": ["<", 2]}
	if current_encounter:
		filters["name"] = ["!=", current_encounter]
	return frappe.get_all(
		"Patient Encounter",
		filters=filters,
		fields=["name", "encounter_date", "practitioner", "practitioner_name"],
		order_by="encounter_date desc, creation desc",
		limit_start=start,
		limit_page_length=BATCH_SIZE,
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
