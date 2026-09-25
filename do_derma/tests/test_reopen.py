from __future__ import annotations

import json
from unittest.mock import patch

import frappe
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


class TestReopenProcedure(PrescriptionHelpers, IntegrationTestCase):
	def setUp(self):
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

	def test_invoice_lookup_runs_against_the_real_schema(self):
		self.assertEqual(reopen.get_submitted_invoices([self.procedure.name]), {})

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
