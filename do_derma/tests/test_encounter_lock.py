from __future__ import annotations

import json

import frappe
from frappe.tests import IntegrationTestCase

import do_derma.api as api
from do_derma import assessment
from do_derma.tests.test_api import PIXEL_PNG, DermaTestHelpers

LOCKED = "Reopen it to make changes"


class TestEncounterLock(DermaTestHelpers, IntegrationTestCase):
	"""A completed encounter is read-only for every chart write, whatever the browser shows."""

	def setUp(self):
		self.patient = self._make_patient()
		self.encounter = self._make_encounter(self.patient, docstatus=1)

	def assertLocked(self, call):
		with self.assertRaises(frappe.ValidationError) as caught:
			call()
		self.assertIn(LOCKED, str(caught.exception))

	def _submitted_procedure(self):
		procedure = self._make_clinical_procedure(self.patient)
		field = api._get_clinical_procedure_encounter_field()
		procedure.db_set(field, self.encounter.name)
		procedure.submit()
		return procedure

	def test_new_procedure_is_refused(self):
		self.assertLocked(
			lambda: api.create_derma_chart_procedure(
				{
					"patient": self.patient,
					"encounter": self.encounter.name,
					"procedure_template": self._get_or_create_procedure_template(),
				}
			)
		)

	def test_new_mark_is_refused(self):
		self.assertLocked(lambda: self._save_mark(self.patient, encounter=self.encounter.name))

	def test_carry_forward_is_refused(self):
		self.assertLocked(
			lambda: api.carry_forward_marks(["any"], patient=self.patient, encounter=self.encounter.name)
		)

	def test_assessment_format_change_is_refused(self):
		self.assertLocked(lambda: api.set_derma_assessment_mode("SOAP", encounter=self.encounter.name))

	def test_prescriptions_are_refused(self):
		self.assertLocked(lambda: api.set_derma_prescriptions(payload="[]", encounter=self.encounter.name))

	def test_consultation_drawing_is_refused(self):
		self.assertLocked(
			lambda: api.save_derma_annotation(
				{"doctype": "Patient Encounter", "docname": self.encounter.name, "file_data": PIXEL_PNG}
			)
		)

	def test_procedure_drawing_is_refused_on_a_submitted_procedure(self):
		procedure = self._submitted_procedure()
		self.assertLocked(
			lambda: api.save_derma_annotation({"clinical_procedure": procedure.name, "file_data": PIXEL_PNG})
		)

	def test_procedure_variables_are_refused(self):
		procedure = self._submitted_procedure()
		self.assertLocked(
			lambda: api.save_procedure_variables(procedure.name, procedure.procedure_template, "{}")
		)

	def test_photo_set_is_refused(self):
		self.assertLocked(
			lambda: api.create_photo_set(
				json.dumps({"patient": self.patient, "encounter": self.encounter.name})
			)
		)

	def test_consent_is_refused(self):
		self.assertLocked(
			lambda: api.create_derma_consent(
				json.dumps({"patient": self.patient, "encounter": self.encounter.name})
			)
		)

	def test_completing_again_is_refused(self):
		self.assertLocked(
			lambda: api.complete_derma_session(encounter=self.encounter.name, patient=self.patient)
		)

	def test_marks_from_a_completed_visit_are_not_discarded(self):
		draft = self._make_encounter(self.patient)
		mark = self._save_mark(self.patient, encounter=draft.name)
		draft.submit()
		self.assertLocked(lambda: api.discard_chart_marks([mark["name"]]))
		self.assertLocked(lambda: api.prune_chart_marks([mark["name"]]))
		self.assertTrue(frappe.db.exists("Derma Chart Mark", mark["name"]))

	def test_owning_encounter_of_a_procedure(self):
		procedure = self._submitted_procedure()
		self.assertEqual(api._get_owning_encounter("Clinical Procedure", procedure.name), self.encounter.name)

	def test_an_open_encounter_still_takes_writes(self):
		draft = self._make_encounter(self.patient)
		mark = self._save_mark(self.patient, encounter=draft.name)
		self.assertTrue(mark["name"])


class TestEditsAfterSubmit(DermaTestHelpers, IntegrationTestCase):
	"""Fields the clinic allows on submit stay editable; everything else is locked."""

	def _first_field(self, layout, allow_on_submit):
		for row in layout:
			if (
				row.get("is_value_field")
				and row.get("fieldtype") in {"Data", "Small Text", "Text", "Long Text", "Text Editor"}
				and bool(row.get("allow_on_submit")) == allow_on_submit
			):
				return row["fieldname"]
		self.skipTest(f"This site's assessment layout has no text field with allow_on_submit={allow_on_submit}.")

	def test_a_locked_assessment_field_is_refused(self):
		encounter = self._make_encounter(self._make_patient(), docstatus=1)
		mode = assessment.get_assessment_mode(encounter)
		field = self._first_field(assessment.get_layout(mode), allow_on_submit=False)
		with self.assertRaises(frappe.ValidationError):
			api.set_derma_assessment(payload=json.dumps({field: "changed"}), mode=mode, encounter=encounter.name)

	def test_an_unchanged_assessment_payload_saves(self):
		encounter = self._make_encounter(self._make_patient(), docstatus=1)
		mode = assessment.get_assessment_mode(encounter)
		values = assessment.serialize_values(encounter, assessment.get_layout(mode))
		api.set_derma_assessment(payload=json.dumps(values, default=str), mode=mode, encounter=encounter.name)

	def test_procedure_notes_stay_editable_after_submit(self):
		patient = self._make_patient()
		procedure = self._make_clinical_procedure(patient)
		procedure.submit()
		saved = api.update_clinical_procedure_fields(procedure.name, json.dumps({"notes": "Healed well."}))
		self.assertEqual(saved["notes"], "Healed well.")

	def test_a_locked_procedure_field_is_refused(self):
		patient = self._make_patient()
		procedure = self._make_clinical_procedure(patient)
		procedure.submit()
		with self.assertRaises(frappe.ValidationError):
			api.update_clinical_procedure_fields(procedure.name, json.dumps({"start_date": "2020-01-01"}))
