from __future__ import annotations

import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate, nowtime

import do_derma.api as api
from do_derma.tests.test_api import DermaTestHelpers


class PrescriptionHelpers(DermaTestHelpers):
	"""Rows the Rx tab would send, with healthcare's two mandatory fields filled."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")

	def _row(self, **extra):
		"""The two fields healthcare marks mandatory, plus whatever the test cares about."""
		return {
			"medication": self._get_or_create_medication(),
			"period": self._get_or_create_prescription_duration(),
			**extra,
		}

	def _get_or_create_medication(self):
		existing = frappe.db.get_value("Medication", {}, "name")
		if existing:
			return existing
		token = frappe.generate_hash(length=8)
		return (
			frappe.get_doc(
				{
					"doctype": "Medication",
					"generic_name": f"Derma{token}",
					"dosage_form": frappe.db.get_value("Dosage Form", {}, "name"),
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def _get_or_create_prescription_duration(self):
		existing = frappe.db.get_value("Prescription Duration", {}, "name")
		if existing:
			return existing
		return (
			frappe.get_doc({"doctype": "Prescription Duration", "number": 5, "period": "Day"})
			.insert(ignore_permissions=True)
			.name
		)


class TestDermaPrescriptions(PrescriptionHelpers, IntegrationTestCase):
	"""The Rx tab writes and reads Patient Encounter's drug_prescription table."""

	def test_a_saved_row_reads_back(self):
		encounter = self._make_encounter(self._make_patient())
		saved = api.set_derma_prescriptions(
			payload=json.dumps([self._row(drug_name="Hydrocortisone 1%", comment="Thin layer at night")]),
			encounter=encounter.name,
		)
		self.assertEqual(len(saved["drug_prescription"]), 1)
		self.assertEqual(saved["drug_prescription"][0]["drug_name"], "Hydrocortisone 1%")

		reloaded = api.get_derma_prescriptions(encounter=encounter.name)
		self.assertEqual(len(reloaded["drug_prescription"]), 1)
		self.assertEqual(reloaded["drug_prescription"][0]["comment"], "Thin layer at night")

	def test_rows_replace_the_previous_set(self):
		encounter = self._make_encounter(self._make_patient())
		api.set_derma_prescriptions(
			payload=json.dumps([self._row(drug_name="First"), self._row(drug_name="Second")]),
			encounter=encounter.name,
		)
		saved = api.set_derma_prescriptions(
			payload=json.dumps([self._row(drug_name="Only")]), encounter=encounter.name
		)
		self.assertEqual([row["drug_name"] for row in saved["drug_prescription"]], ["Only"])

	def test_an_absurd_repeat_count_is_refused(self):
		encounter = self._make_encounter(self._make_patient())
		with self.assertRaises(frappe.ValidationError):
			api.set_derma_prescriptions(
				payload=json.dumps([self._row(number_of_repeats_allowed=500)]),
				encounter=encounter.name,
			)
		self.assertEqual(api.get_derma_prescriptions(encounter=encounter.name)["drug_prescription"], [])

	def test_is_gated(self):
		frappe.set_user(self._make_limited_user())
		with self.assertRaises(frappe.PermissionError):
			api.get_derma_prescriptions(encounter="does-not-matter")

	def test_the_chart_counts_prescriptions_without_loading_them(self):
		"""The Prescription tab's badge reads this; the rows themselves load on demand."""
		patient = self._make_patient()
		encounter = self._make_encounter(patient)
		chart = api.get_patient_derma_chart(patient_id=patient, encounter=encounter.name)
		self.assertEqual(chart["prescription_count"], 0)

		api.set_derma_prescriptions(
			payload=json.dumps([self._row(drug_name="First"), self._row(drug_name="Second")]),
			encounter=encounter.name,
		)
		chart = api.get_patient_derma_chart(patient_id=patient, encounter=encounter.name)
		self.assertEqual(chart["prescription_count"], 2)

	def test_the_count_is_zero_without_an_encounter(self):
		patient = self._make_patient()
		chart = api.get_patient_derma_chart(patient_id=patient)
		self.assertEqual(chart["prescription_count"], 0)


class TestOrderedPrescriptions(PrescriptionHelpers, IntegrationTestCase):
	"""A row turned into a Medication Request is kept verbatim, so resubmitting orders nothing twice."""

	def _ordered_encounter(self):
		encounter = self._make_encounter(self._make_patient())
		api.set_derma_prescriptions(payload=json.dumps([self._row(drug_name="Ordered")]), encounter=encounter.name)
		row = frappe.get_doc("Patient Encounter", encounter.name).drug_prescription[0]
		frappe.db.set_value(row.doctype, row.name, "medication_request", "MR-DERMA-TEST")
		return encounter

	def test_an_ordered_row_survives_a_save_that_omits_it(self):
		encounter = self._ordered_encounter()
		saved = api.set_derma_prescriptions(payload=json.dumps([self._row(drug_name="New")]), encounter=encounter.name)
		self.assertEqual(
			[(row["drug_name"], row.get("medication_request")) for row in saved["drug_prescription"]],
			[("Ordered", "MR-DERMA-TEST"), ("New", None)],
		)

	def test_an_edited_ordered_row_keeps_its_stored_values(self):
		"""A client resending a known request with changed fields does not overwrite what was ordered."""
		encounter = self._ordered_encounter()
		stored = api.get_derma_prescriptions(encounter=encounter.name)["drug_prescription"][0]
		saved = api.set_derma_prescriptions(
			payload=json.dumps(
				[
					self._row(
						drug_name="Edited",
						medication_request=stored["medication_request"],
						comment="Edited after ordering",
					)
				]
			),
			encounter=encounter.name,
		)
		self.assertEqual(len(saved["drug_prescription"]), 1)
		self.assertEqual(saved["drug_prescription"][0]["drug_name"], stored["drug_name"])
		self.assertEqual(saved["drug_prescription"][0]["comment"], stored["comment"])

	def test_an_unknown_request_is_refused(self):
		encounter = self._ordered_encounter()
		with self.assertRaises(frappe.ValidationError):
			api.set_derma_prescriptions(
				payload=json.dumps([self._row(drug_name="Forged", medication_request="MR-OTHER")]),
				encounter=encounter.name,
			)


class TestConsentPreview(DermaTestHelpers, IntegrationTestCase):
	"""A template the health app cannot render must name itself, not explode."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")

	def test_a_template_that_cannot_render_returns_an_error_naming_it(self):
		doctype = "Encounter Consent" if api._has_doctype("Encounter Consent") else "Consent Form"
		if not api._has_doctype(doctype):
			self.skipTest("No consent doctype installed.")
		controller = type(frappe.new_doc(doctype))
		if not hasattr(controller, "render_template"):
			self.skipTest("Consent controller has no render_template.")

		blow_up = AttributeError("'ConsentFormTemplate' object has no attribute 'procedure_template'")
		with patch.object(controller, "render_template", side_effect=blow_up):
			result = api.render_derma_consent_preview(
				payload=json.dumps({"consent_form_template": "Broken Template"})
			)
		self.assertFalse(result.get("rendered_html"))
		self.assertIn("Broken Template", result.get("error") or "")

	def test_is_gated(self):
		frappe.set_user(self._make_limited_user())
		with self.assertRaises(frappe.PermissionError):
			api.render_derma_consent_preview(payload="{}")


class TestCompletionMessages(DermaTestHelpers, IntegrationTestCase):
	"""Completion must report on this visit only - the health app's invoice call
	msgprints about apps the clinic does not run."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")
		frappe.clear_messages()

	def _make_billable_encounter(self):
		"""An encounter with an appointment - the only shape that reaches the invoice call."""
		patient = self._make_patient()
		appointment = frappe.get_doc(
			{
				"doctype": "Patient Appointment",
				"patient": patient,
				"appointment_type": self._get_or_create_appointment_type(),
				"practitioner": self._get_or_create_practitioner(),
				"appointment_date": nowdate(),
				"appointment_time": nowtime(),
				"company": frappe.db.get_value("Company", {}, "name"),
			}
		).insert(ignore_permissions=True)
		encounter = self._make_encounter(patient)
		encounter.db_set("appointment", appointment.name)
		return encounter

	def test_foreign_invoice_messages_do_not_reach_the_response(self):
		encounter = self._make_billable_encounter()

		def noisy_invoice(**kwargs):
			frappe.msgprint("App do_dental is not installed")
			return {"invoice": "SI-TEST"}

		with patch("do_health.api.methods.create_invoice_for_visit", noisy_invoice):
			api.complete_derma_session(encounter=encounter.name)

		messages = [str(message) for message in (frappe.local.message_log or [])]
		self.assertFalse([message for message in messages if "do_dental" in message], messages)

	def test_a_genuine_invoice_failure_is_still_reported(self):
		encounter = self._make_billable_encounter()

		def failing_invoice(**kwargs):
			raise frappe.ValidationError("No item price found")

		with patch("do_health.api.methods.create_invoice_for_visit", failing_invoice):
			result = api.complete_derma_session(encounter=encounter.name)

		self.assertIsNone(result["invoice"])
		self.assertIn("No item price found", result["invoice_error"] or "")
