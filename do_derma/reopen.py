from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, get_fullname


def reopen_document(doc, reason: str | None, status: str) -> None:
	"""Put a submitted document and its child rows back to draft, and say why on its timeline."""
	if not doc.has_permission("cancel"):
		frappe.throw(
			_("You are not permitted to reopen {0} {1}.").format(_(doc.doctype), doc.name),
			frappe.PermissionError,
		)
	reason = (reason or "").strip()
	if not reason:
		frappe.throw(_("A reason is required to reopen {0}.").format(doc.name), frappe.ValidationError)
	if cint(doc.docstatus) != 1:
		frappe.throw(
			_("{0} {1} is not completed, so it cannot be reopened.").format(_(doc.doctype), doc.name),
			frappe.ValidationError,
		)

	for table in doc.meta.get_table_fields():
		child = frappe.qb.DocType(table.options)
		(
			frappe.qb.update(child)
			.set(child.docstatus, 0)
			.where((child.parent == doc.name) & (child.parenttype == doc.doctype))
			.run()
		)
	doc.db_set({"docstatus": 0, "status": status}, update_modified=True)
	doc.add_comment("Comment", _("Reopened by {0}: {1}").format(get_fullname(), reason))


def get_submitted_invoices(procedures: list[str]) -> dict[str, str]:
	"""The submitted Sales Invoice billing each procedure, keyed by procedure."""
	if not procedures:
		return {}
	invoices = {
		row.reference_dn: row.parent
		for row in frappe.get_all(
			"Sales Invoice Item",
			filters={
				"reference_dt": "Clinical Procedure",
				"reference_dn": ["in", procedures],
				"docstatus": 1,
			},
			fields=["reference_dn", "parent"],
		)
	}
	if frappe.db.exists("DocType", "Billing Charge"):
		for row in frappe.get_all(
			"Billing Charge",
			filters={"clinical_procedure": ["in", procedures]},
			fields=["clinical_procedure", "lite_patient_invoice", "lite_insurance_invoice"],
		):
			for invoice in (row.lite_patient_invoice, row.lite_insurance_invoice):
				if invoice and cint(frappe.db.get_value("Sales Invoice", invoice, "docstatus")) == 1:
					invoices.setdefault(row.clinical_procedure, invoice)
	return invoices


def get_nursing_tasks(procedure: str) -> list[dict]:
	if not frappe.db.exists("DocType", "Nursing Task"):
		return []
	return frappe.get_all(
		"Nursing Task",
		filters={"reference_doctype": "Clinical Procedure", "reference_name": procedure},
		fields=["name", "activity"],
	)


def drop_repeated_nursing_tasks(procedure: str, before: list[dict]) -> None:
	"""healthcare requests the pre-op checklist on every submit; a reopened procedure has it already."""
	requested = {row.activity for row in before}
	existing = {row.name for row in before}
	for row in get_nursing_tasks(procedure):
		if row.name in existing or row.activity not in requested:
			continue
		task = frappe.get_doc("Nursing Task", row.name)
		if task.docstatus == 1:
			# A "Requested" task submits itself on insert, and submitted records cannot be deleted.
			task.flags.ignore_permissions = True
			task.cancel()
		frappe.delete_doc("Nursing Task", row.name, ignore_permissions=True)
