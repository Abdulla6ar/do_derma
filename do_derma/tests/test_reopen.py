from __future__ import annotations

import json
from unittest.mock import patch

import frappe
from do_health.billing import hooks as billing_hooks
from frappe.tests import IntegrationTestCase

import do_derma.api as api
from do_derma import reopen
from do_derma.tests.test_encounter_tabs import PrescriptionHelpers


class TestReopenSession(PrescriptionHelpers, IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.patient = self._make_patient()
		self.encounter = self._make_encounter(self.patient)
		api.complete_derma_session(encounter=self.encounter.name, patient=self.patient)

	def _reopen(self, reason="Added a missed finding"):
		return api.reopen_derma_session(self.encounter.name, reason)

	def test_puts_the_encounter_back_to_draft(self):
		self._reopen()
		values = frappe.db.get_value(
			"Patient Encounter", self.encounter.name, ["docstatus", "status"], as_dict=True
		)
		self.assertEqual((values.docstatus, values.status), (0, "Open"))

	def test_writes_the_reason_on_the_timeline(self):
		self._reopen("Added a missed finding")
		comments = frappe.get_all(
			"Comment",
			filters={
				"reference_doctype": "Patient Encounter",
				"reference_name": self.encounter.name,
				"comment_type": "Comment",
			},
			pluck="content",
		)
		self.assertTrue(any("Added a missed finding" in (content or "") for content in comments), comments)

	def test_a_blank_reason_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self._reopen("   ")
		self.assertEqual(frappe.db.get_value("Patient Encounter", self.encounter.name, "docstatus"), 1)

	def test_reopening_twice_is_refused(self):
		self._reopen()
		with self.assertRaises(frappe.ValidationError):
			self._reopen()

	def test_needs_cancel_permission(self):
		"""Nursing User has clinical access (CLINICAL_ACCESS_ROLES) but no DocPerm at all on
		Patient Encounter on this site - only Physician carries cancel there. The failure must
		be the reopen-specific message, not the earlier clinical-access gate."""
		frappe.set_user(self._make_user_with_role("Nursing User"))
		with self.assertRaises(frappe.PermissionError) as caught:
			self._reopen()
		self.assertIn("not permitted to reopen", str(caught.exception))

	def test_submitted_procedures_stay_submitted(self):
		encounter = self._make_encounter(self.patient)
		procedure = self._make_clinical_procedure(self.patient)
		procedure.db_set(api._get_clinical_procedure_encounter_field(), encounter.name)
		api.complete_derma_session(encounter=encounter.name, patient=self.patient)

		api.reopen_derma_session(encounter.name, "Fix the note")

		self.assertEqual(frappe.db.get_value("Clinical Procedure", procedure.name, "docstatus"), 1)

	def test_completing_again_orders_nothing_twice(self):
		encounter = self._make_encounter(self.patient)
		api.set_derma_prescriptions(
			payload=json.dumps([self._row(drug_name="First")]), encounter=encounter.name
		)
		api.complete_derma_session(encounter=encounter.name, patient=self.patient)
		api.reopen_derma_session(encounter.name, "Add a second drug")
		api.set_derma_prescriptions(
			payload=json.dumps([self._row(drug_name="Second")]), encounter=encounter.name
		)

		api.complete_derma_session(encounter=encounter.name, patient=self.patient)

		self.assertEqual(frappe.db.count("Medication Request", {"order_group": encounter.name}), 2)

	def test_prescription_rows_go_back_to_draft(self):
		encounter = self._make_encounter(self.patient)
		api.set_derma_prescriptions(payload=json.dumps([self._row()]), encounter=encounter.name)
		api.complete_derma_session(encounter=encounter.name, patient=self.patient)

		api.reopen_derma_session(encounter.name, "Change the dose")

		rows = frappe.get_all(
			"Drug Prescription",
			filters={"parent": encounter.name, "parenttype": "Patient Encounter"},
			pluck="docstatus",
		)
		self.assertEqual(rows, [0])

	def _therapy_plan(self):
		"""The row healthcare's Therapy Plan creation leaves, written directly: a real plan needs a Therapy Type and its Item."""
		if not frappe.db.exists("DocType", "Therapy Plan"):
			self.skipTest("healthcare's Therapy Plan is not installed.")
		plan = frappe.get_doc(
			{
				"doctype": "Therapy Plan",
				"patient": self.patient,
				"start_date": frappe.utils.nowdate(),
				"company": self.encounter.company or frappe.db.get_value("Company", {}, "name"),
				"practitioner": self.encounter.practitioner,
				"source_doc": "Patient Encounter",
				"order_group": self.encounter.name,
			}
		)
		plan.db_insert()
		return plan.name

	def test_an_encounter_with_a_therapy_plan_is_refused(self):
		plan = self._therapy_plan()
		with self.assertRaises(frappe.ValidationError) as caught:
			self._reopen()
		self.assertIn(plan, str(caught.exception))
		self.assertEqual(frappe.db.get_value("Patient Encounter", self.encounter.name, "docstatus"), 1)


class TestReopenProcedure(PrescriptionHelpers, IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		self.patient = self._make_patient()
		self.encounter = self._make_encounter(self.patient)
		self.procedure = self._make_clinical_procedure(self.patient)
		self.procedure.db_set(api._get_clinical_procedure_encounter_field(), self.encounter.name)
		api.complete_derma_session(encounter=self.encounter.name, patient=self.patient)

	def test_is_refused_while_the_encounter_is_completed(self):
		with self.assertRaises(frappe.ValidationError):
			api.reopen_derma_procedure(self.procedure.name, "Wrong dose")

	def test_puts_the_procedure_back_to_draft(self):
		api.reopen_derma_session(self.encounter.name, "Fix a procedure")
		api.reopen_derma_procedure(self.procedure.name, "Wrong dose")
		values = frappe.db.get_value(
			"Clinical Procedure", self.procedure.name, ["docstatus", "status"], as_dict=True
		)
		self.assertEqual((values.docstatus, values.status), (0, "In Progress"))

	def test_an_invoiced_procedure_is_refused(self):
		api.reopen_derma_session(self.encounter.name, "Fix a procedure")
		with patch.object(
			reopen, "get_submitted_invoices", return_value={self.procedure.name: "ACC-SINV-TEST"}
		):
			with self.assertRaises(frappe.ValidationError) as caught:
				api.reopen_derma_procedure(self.procedure.name, "Wrong dose")
		self.assertIn("ACC-SINV-TEST", str(caught.exception))

	def test_permission_is_checked_before_the_invoice_is_named(self):
		api.reopen_derma_session(self.encounter.name, "Fix a procedure")
		frappe.set_user(self._make_user_with_role("Nursing User"))
		with patch.object(
			reopen, "get_submitted_invoices", return_value={self.procedure.name: "ACC-SINV-TEST"}
		):
			with self.assertRaises(frappe.PermissionError) as caught:
				api.reopen_derma_procedure(self.procedure.name, "Wrong dose")
		self.assertNotIn("ACC-SINV-TEST", str(caught.exception))

	def test_invoice_lookup_runs_against_the_real_schema(self):
		self.assertEqual(reopen.get_submitted_invoices([self.procedure.name]), {})

	def test_a_procedure_outside_a_visit_is_refused(self):
		procedure = self._make_clinical_procedure(self.patient)
		with self.assertRaises(frappe.ValidationError) as caught:
			api.reopen_derma_procedure(procedure.name, "Wrong dose")
		self.assertIn("not linked to a visit", str(caught.exception))

	def _invoice(self, return_against=None):
		"""A submitted invoice billing this procedure, or a credit note against one."""
		company = frappe.get_cached_doc("Company", self.procedure.company)
		invoice = frappe.get_doc(
			{
				"doctype": "Sales Invoice",
				"company": company.name,
				"customer": frappe.db.get_value("Patient", self.patient, "customer")
				or frappe.db.get_value("Customer", {}, "name"),
				"currency": company.default_currency,
				"debit_to": company.default_receivable_account,
				"is_pos": 0,
				"is_return": int(bool(return_against)),
				"return_against": return_against,
				"items": [
					{
						"item_code": frappe.db.get_value(
							"Item", {"disabled": 0, "is_sales_item": 1, "is_stock_item": 0}, "name"
						),
						"qty": -1 if return_against else 1,
						"rate": 10,
						"income_account": company.default_income_account,
						"cost_center": company.cost_center,
						"reference_dt": "Clinical Procedure",
						"reference_dn": self.procedure.name,
					}
				],
			}
		).insert(ignore_permissions=True)
		# do_health only lets its Billing Controller submit healthcare invoices.
		invoice.flags.legacy_patient_invoice_submit_token = billing_hooks._LEGACY_PATIENT_INVOICE_SUBMIT_TOKEN
		invoice.submit()
		return invoice

	def test_a_submitted_invoice_is_found(self):
		invoice = self._invoice()
		self.assertEqual(
			reopen.get_submitted_invoices([self.procedure.name]), {self.procedure.name: invoice.name}
		)

	def test_the_original_invoice_is_named_not_its_credit_note(self):
		invoice = self._invoice()
		self._invoice(return_against=invoice.name)
		self.assertEqual(
			reopen.get_submitted_invoices([self.procedure.name]), {self.procedure.name: invoice.name}
		)

	def test_a_cancelled_invoice_does_not_block(self):
		self._invoice().cancel()
		self.assertEqual(reopen.get_submitted_invoices([self.procedure.name]), {})

	def _mark(self):
		return frappe.get_doc(
			{
				"doctype": "Derma Chart Mark",
				"patient": self.patient,
				"encounter": self.encounter.name,
				"clinical_procedure": self.procedure.name,
				"x_percent": 40,
				"y_percent": 60,
			}
		).insert(ignore_permissions=True)

	def _mark_writes(self, mark):
		payload = {
			"patient": self.patient,
			"encounter": self.encounter.name,
			"x_percent": 41,
			"y_percent": 61,
		}
		return (
			lambda: api.save_consumables("Derma Chart Mark", mark.name, []),
			lambda: api.save_chart_mark(json.dumps({"name": mark.name, **payload})),
			lambda: api.save_chart_mark(json.dumps({"clinical_procedure": self.procedure.name, **payload})),
		)

	def test_marks_of_a_completed_procedure_stay_locked_in_a_reopened_visit(self):
		mark = self._mark()
		api.reopen_derma_session(self.encounter.name, "Fix the note")
		for write in self._mark_writes(mark):
			with self.assertRaises(frappe.ValidationError) as caught:
				write()
			self.assertIn("Reopen it to make changes", str(caught.exception))

	def test_marks_of_a_reopened_procedure_take_writes(self):
		mark = self._mark()
		api.reopen_derma_session(self.encounter.name, "Fix a procedure")
		api.reopen_derma_procedure(self.procedure.name, "Wrong dose")
		for write in self._mark_writes(mark):
			write()

	def test_completing_again_resubmits_it(self):
		api.reopen_derma_session(self.encounter.name, "Fix a procedure")
		api.reopen_derma_procedure(self.procedure.name, "Wrong dose")
		api.complete_derma_session(encounter=self.encounter.name, patient=self.patient)
		self.assertEqual(frappe.db.get_value("Clinical Procedure", self.procedure.name, "docstatus"), 1)


class TestResubmitSideEffects(PrescriptionHelpers, IntegrationTestCase):
	"""Submitting a reopened procedure again must not bill or request its checklist twice."""

	def _billable_template(self, pre_op_checklist=None):
		token = frappe.generate_hash(length=8)
		doc = frappe.get_doc(
			{
				"doctype": "Clinical Procedure Template",
				"template": f"DermaBill{token}",
				"item_code": f"DermaBill{token}",
				"description": "Resubmit fixture.",
				"item_group": frappe.db.get_value("Item Group", {}, "name"),
				"pre_op_nursing_checklist_template": pre_op_checklist,
			}
		)
		doc.set("is_billable", 1)
		return doc.insert(ignore_permissions=True).name

	def _checklist(self):
		token = frappe.generate_hash(length=8)
		activity = frappe.get_doc(
			{"doctype": "Healthcare Activity", "activity": f"Derma prep {token}"}
		).insert(ignore_permissions=True)
		return (
			frappe.get_doc(
				{
					"doctype": "Nursing Checklist Template",
					"title": f"Derma {token}",
					"tasks": [{"activity": activity.name}],
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def _round_trip(self, template):
		patient = self._make_patient()
		appointment = frappe.get_doc(
			{
				"doctype": "Patient Appointment",
				"patient": patient,
				"appointment_type": self._get_or_create_appointment_type(),
				"practitioner": self._get_or_create_practitioner(),
				"appointment_date": frappe.utils.nowdate(),
				"appointment_time": frappe.utils.nowtime(),
				"company": frappe.db.get_value("Company", {}, "name"),
			}
		).insert(ignore_permissions=True)
		encounter = self._make_encounter(patient)
		encounter.db_set("appointment", appointment.name)
		procedure = self._make_clinical_procedure(patient)
		procedure.db_set({"procedure_template": template, "appointment": appointment.name})
		procedure.db_set(api._get_clinical_procedure_encounter_field(), encounter.name)
		with patch("do_health.api.methods.create_invoice_for_visit", return_value=None):
			api.complete_derma_session(encounter=encounter.name, patient=patient)
			api.reopen_derma_session(encounter.name, "Fix")
			api.reopen_derma_procedure(procedure.name, "Fix")
			api.complete_derma_session(encounter=encounter.name, patient=patient)
		return procedure.name

	def test_billing_is_not_added_twice(self):
		if not frappe.db.exists("DocType", "Appointment Billing Items"):
			self.skipTest("do_health billing is not installed.")
		procedure = self._round_trip(self._billable_template())
		self.assertEqual(
			frappe.db.count(
				"Appointment Billing Items",
				{"clinical_procedure": procedure, "parenttype": "Patient Appointment"},
			),
			1,
		)

	def test_nursing_tasks_are_not_requested_twice(self):
		procedure = self._round_trip(self._billable_template(pre_op_checklist=self._checklist()))
		self.assertEqual(
			frappe.db.count(
				"Nursing Task", {"reference_doctype": "Clinical Procedure", "reference_name": procedure}
			),
			1,
		)


class TestChartReopenPayload(PrescriptionHelpers, IntegrationTestCase):
	def test_reports_reopen_rights_and_invoices(self):
		patient = self._make_patient()
		encounter = self._make_encounter(patient)
		procedure = self._make_clinical_procedure(patient)
		procedure.db_set(api._get_clinical_procedure_encounter_field(), encounter.name)

		with patch.object(reopen, "get_submitted_invoices", return_value={procedure.name: "ACC-SINV-TEST"}):
			chart = api.get_patient_derma_chart(patient_id=patient, encounter=encounter.name)

		self.assertEqual(chart["permissions"], {"can_reopen_encounter": True, "can_reopen_procedure": True})
		rows = [
			row
			for row in chart["procedures"]
			if (row.get("clinical_procedure") or row.get("name")) == procedure.name
		]
		self.assertEqual(rows[0]["submitted_invoice"], "ACC-SINV-TEST")

	def test_procedure_rows_carry_docstatus_for_reopening(self):
		patient = self._make_patient()
		encounter = self._make_encounter(patient)
		draft = self._make_clinical_procedure(patient)
		draft.db_set(api._get_clinical_procedure_encounter_field(), encounter.name)
		submitted = self._make_clinical_procedure(patient)
		submitted.db_set(api._get_clinical_procedure_encounter_field(), encounter.name)
		submitted.db_set("docstatus", 1)

		chart = api.get_patient_derma_chart(patient_id=patient, encounter=encounter.name)

		rows = {(row.get("clinical_procedure") or row.get("name")): row for row in chart["procedures"]}
		self.assertEqual(rows[draft.name]["docstatus"], 0)
		self.assertEqual(rows[submitted.name]["docstatus"], 1)
