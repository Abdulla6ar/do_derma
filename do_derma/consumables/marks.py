"""A mark's consumables as the chart reads them and as a Clinical Procedure takes them."""

from __future__ import annotations

from typing import Any

import frappe

from do_derma.consumables import conversion, snapshot
from do_derma.consumables.defaults import CONSUMABLE_FIELDS, select_fields


def hydrate(mark_rows: list[dict[str, Any]]) -> None:
	"""Attach each mark's live consumables, already judged against its frozen defaults."""
	if not mark_rows or not is_available():
		return
	names = [row.get("name") for row in mark_rows if row.get("name")]
	live = _live_rows(names)
	frozen = _frozen_rows(names)
	for mark in mark_rows:
		name = mark.get("name")
		defaults = frozen.get(name, [])
		compared = snapshot.compare(live.get(name, []), defaults)
		mark["consumables"] = compared["consumables"]
		mark["removed_consumables"] = compared["removed"]
		mark["default_consumables"] = defaults


def get_payload(mark_doc) -> dict[str, Any]:
	"""The shape both the chart read and a save answer with, so the panel can swap state."""
	frozen = snapshot.load(mark_doc.default_consumables_json)
	compared = snapshot.compare(select_fields(mark_doc.consumables), frozen)
	return {
		"owner_doctype": "Derma Chart Mark",
		"owner_name": mark_doc.name,
		"consumables": compared["consumables"],
		"removed_consumables": compared["removed"],
		"default_consumables": frozen,
	}


def save(mark_name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
	"""Replace one mark's consumables outright and answer what the chart should now show."""
	from do_derma import api

	api._ensure_owner_open("Derma Chart Mark", mark_name)
	mark_doc = frappe.get_doc("Derma Chart Mark", mark_name)
	mark_doc.set("consumables", rows)
	_apply_batch_identity(mark_doc, rows)
	mark_doc.save(ignore_permissions=True)
	return get_payload(mark_doc)


def _apply_batch_identity(mark_doc, rows: list[dict[str, Any]]) -> None:
	"""One batch on the list is the mark's lot too, so the same box is not typed twice.

	Two batches describe no single lot, so the mark is left saying nothing rather than
	naming one of them, and a lot the clinician typed is never overwritten.
	"""
	batches = [row.get("batch_no") for row in rows if row.get("batch_no")]
	if len(batches) != 1:
		return
	expiry = frappe.db.get_value("Batch", batches[0], "expiry_date")
	if not mark_doc.get("lot_no"):
		mark_doc.lot_no = batches[0]
	if expiry and not mark_doc.get("expiry_date"):
		mark_doc.expiry_date = expiry


def apply_to_procedure(procedure, mark_doc) -> None:
	"""The mark's consumables replace the procedure's outright.

	The clinician's list is the more recent statement about the same procedure. A mark that
	recorded none leaves the document exactly as healthcare wrote it.
	"""
	from do_derma import api

	if not mark_doc or not is_available() or not api._has_field("Clinical Procedure", "items"):
		return
	rows = select_fields(mark_doc.get("consumables"))
	if not rows:
		return
	for row in rows:
		conversion.ensure_convertible(row.get("item_code"), row.get("uom"), row.get("conversion_factor"))
	procedure.set("items", rows)
	if api._has_field("Clinical Procedure", "consume_stock"):
		procedure.consume_stock = 1


def is_available() -> bool:
	# Imported inside the function, not at module scope: `api` reads this package, and the
	# Derma Chart Mark controller imports this package before `api` has ever been loaded.
	from do_derma import api

	return api._has_doctype("Clinical Procedure Item") and api._has_field("Derma Chart Mark", "consumables")


def _live_rows(names: list[str]) -> dict[str, list[dict[str, Any]]]:
	rows = frappe.get_all(
		"Clinical Procedure Item",
		filters={"parent": ["in", names], "parenttype": "Derma Chart Mark"},
		fields=["parent", *CONSUMABLE_FIELDS],
		order_by="parent asc, idx asc",
		# Unpaged for the same reason area variables are: the caller caps the parents.
		limit=0,
	)
	by_parent: dict[str, list[dict[str, Any]]] = {}
	for row in rows:
		by_parent.setdefault(row.get("parent"), []).extend(select_fields([row]))
	return by_parent


def _frozen_rows(names: list[str]) -> dict[str, list[dict[str, Any]]]:
	rows = frappe.get_all(
		"Derma Chart Mark",
		filters={"name": ["in", names]},
		fields=["name", "default_consumables_json"],
		limit=0,
	)
	return {row.name: snapshot.load(row.default_consumables_json) for row in rows}
