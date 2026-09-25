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
