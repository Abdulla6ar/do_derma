from __future__ import annotations

import json

import frappe
from frappe.tests import IntegrationTestCase

import do_derma.api as api
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
