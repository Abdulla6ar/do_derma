from __future__ import annotations

from collections.abc import Callable
from typing import Any

import frappe
from frappe import _
from frappe.query_builder import Order
from frappe.query_builder.functions import Coalesce

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


def get_visit_moment(encounter: str) -> dict[str, Any]:
	"""When the visit happened: its appointment's date and time, else the encounter's own."""
	visit = VisitQuery()
	rows = visit.select().where(visit.encounter.name == encounter).run(as_dict=True)
	if not rows:
		frappe.throw(_("Patient Encounter {0} not found.").format(encounter), frappe.DoesNotExistError)
	return rows[0]


class VisitQuery:
	"""Encounters with the moment their visit happened.

	An encounter opened today for an old appointment is dated today, so the appointment decides the order.
	"""

	def __init__(self):
		self.encounter = frappe.qb.DocType("Patient Encounter")
		self.appointment = frappe.qb.DocType("Patient Appointment")
		self.visit_date = Coalesce(self.appointment.appointment_date, self.encounter.encounter_date)
		self.visit_time = Coalesce(
			self.appointment.appointment_time, self.encounter.encounter_time, "00:00:00"
		)

	def select(self):
		return (
			frappe.qb.from_(self.encounter)
			.left_join(self.appointment)
			.on(self.appointment.name == self.encounter.appointment)
			.select(
				self.encounter.name,
				self.encounter.creation,
				self.encounter.practitioner,
				self.encounter.practitioner_name,
				self.visit_date.as_("visit_date"),
				self.visit_time.as_("visit_time"),
			)
		)

	def get_before(self, cutoff: dict[str, Any]):
		"""Strictly earlier in (visit date, visit time, creation) order."""
		same_date = self.visit_date == cutoff.visit_date
		same_moment = same_date & (self.visit_time == cutoff.visit_time)
		return (
			(self.visit_date < cutoff.visit_date)
			| (same_date & (self.visit_time < cutoff.visit_time))
			| (same_moment & (self.encounter.creation < cutoff.creation))
		)


def _get_cutoff(current_encounter: str | None) -> dict[str, Any] | None:
	return get_visit_moment(current_encounter) if current_encounter else None


def _get_encounters(
	patient: str, cutoff: dict[str, Any] | None, start: int, limit: int
) -> list[dict[str, Any]]:
	visit = VisitQuery()
	query = visit.select().where((visit.encounter.patient == patient) & (visit.encounter.docstatus < 2))
	if cutoff:
		query = query.where(visit.get_before(cutoff))
	return (
		query.orderby(visit.visit_date, order=Order.desc)
		.orderby(visit.visit_time, order=Order.desc)
		.orderby(visit.encounter.creation, order=Order.desc)
		.offset(start)
		.limit(limit)
		.run(as_dict=True)
	)


def _build_visit(row, load_drawings) -> dict[str, Any] | None:
	drawings = load_drawings(row.name)
	preview = assessment.get_preview(frappe.get_doc("Patient Encounter", row.name))
	if not drawings and not preview:
		return None
	return {
		"encounter": row.name,
		"visit_date": row.visit_date,
		"practitioner_name": row.practitioner_name or row.practitioner or "",
		"drawings": drawings,
		"assessment": preview,
	}
