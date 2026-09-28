from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, cstr

from do_derma import api
from do_derma.overview.text import short_date

MAX_PHOTOS = 6
PHOTO_TYPE_RANK = {"Before": 0, "After": 0, "Visit": 1}
DERMA_ANNOTATION_TYPE = "Derma Annotation"


def get_photos(
	patient: str,
	visit: frappe._dict,
	exclude_encounter: str | None,
	procedure_names: list[str],
	visit_marks: list[frappe._dict],
) -> list[dict[str, str]]:
	"""The visit's clinical photos, else the patient's latest set, then the body-map drawing.

	Listed through get_list so a user who cannot read a set never gets its file URLs. The URLs
	go out as stored: File.make_thumbnail writes a copy under the public folder, which nginx
	serves without asking who is looking.
	"""
	photos = _clinical_photos(patient, visit, exclude_encounter)
	drawings = _drawings(visit.name, procedure_names, {mark.annotation for mark in visit_marks if mark.annotation})
	room = MAX_PHOTOS - min(len(drawings), 1)
	return photos[:room] + drawings[: MAX_PHOTOS - len(photos[:room])]


def _clinical_photos(patient: str, visit: frappe._dict, exclude_encounter: str | None) -> list[dict[str, str]]:
	if not frappe.has_permission("Derma Photo Set", "read"):
		return []
	rows = _photo_rows({"patient": patient, "encounter": visit.name})
	earlier = False
	if not rows:
		filters: dict[str, Any] = {"patient": patient}
		if exclude_encounter:
			filters["encounter"] = ["!=", exclude_encounter]
		rows = _photo_rows(filters)
		rows = [row for row in rows if row.name == rows[0].name] if rows else []
		earlier = True

	# Before/After pairs lead, then visit photos, then the rest; newest set first, each set in its
	# own order. Sorts are stable, so the last one applied is the primary key.
	rows.sort(key=lambda row: cint(row.idx))
	rows.sort(key=lambda row: cstr(row.creation), reverse=True)
	rows.sort(key=lambda row: PHOTO_TYPE_RANK.get(row.photo_type, 2))
	photos = []
	for row in rows:
		caption = [
			row.photo_type or row.set_type or _("Photo"),
			row.photo_region or row.view or row.body_region or row.body_view,
			short_date(row.creation) if earlier else "",
		]
		photos.append({"url": row.image, "caption": " · ".join(part for part in caption if part)})
	return photos


def _photo_rows(filters: dict[str, Any]) -> list[frappe._dict]:
	rows = frappe.get_list(
		"Derma Photo Set",
		filters=filters,
		fields=[
			"name",
			"set_type",
			"body_view",
			"body_region",
			"creation",
			"photos.image as image",
			"photos.photo_type as photo_type",
			"photos.view as view",
			"photos.body_region as photo_region",
			"photos.idx as idx",
		],
		order_by="creation desc",
		limit=30,
	)
	return [row for row in rows if row.image]


def _drawings(encounter: str, procedure_names: list[str], mark_annotations: set[str]) -> list[dict[str, str]]:
	"""The body map drawn at that visit. An encounter can also hold a dental or GP drawing, so
	only derma-tagged rows, the visit's procedures' own drawings and those the marks name count."""
	if not frappe.has_permission("Health Annotation", "read"):
		return []
	fields = ["parenttype", "type", "annotation", "annotation.image as image"]
	if api._has_field("Health Annotation", "custom_derma_body_template_title"):
		fields.append("annotation.custom_derma_body_template_title as title")
	rows = frappe.get_all(
		"Health Annotation Table",
		filters={
			"parent": ["in", [encounter, *procedure_names]],
			"parenttype": ["in", ["Patient Encounter", "Clinical Procedure"]],
			"annotation": ["is", "set"],
		},
		fields=fields,
		order_by="creation desc",
		limit=20,
	)
	drawings, seen = [], set()
	for row in rows:
		derma = row.parenttype == "Clinical Procedure" or row.type == DERMA_ANNOTATION_TYPE or row.annotation in mark_annotations
		if not derma or not row.image or row.annotation in seen:
			continue
		seen.add(row.annotation)
		drawings.append({"url": row.image, "caption": " · ".join(part for part in [_("Body map"), row.get("title")] if part)})
	return drawings
