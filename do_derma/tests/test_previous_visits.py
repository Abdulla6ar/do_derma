from __future__ import annotations

import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate

import do_derma.api as api
from do_derma import assessment, previous_visits
from do_derma.tests.test_api import PIXEL_PNG, DermaTestHelpers
from do_derma.tests.test_encounter_tabs import PrescriptionHelpers

TEXT_TYPES = {"Small Text", "Text", "Long Text", "Text Editor"}


class TestPreviousVisits(DermaTestHelpers, IntegrationTestCase):
	def setUp(self):
		self.patient = self._make_patient()

	def _text_field(self, encounter):
		for row in assessment.get_layout(assessment.get_assessment_mode(encounter)):
			if row.get("is_value_field") and row.get("fieldtype") in TEXT_TYPES:
				return row["fieldname"]
		self.skipTest("This site's assessment layout has no text field.")

	def _with_assessment(self, text="Mild erythema on the left cheek."):
		encounter = self._make_encounter(self.patient)
		encounter.db_set(self._text_field(encounter), text)
		return encounter

	def _with_drawing(self):
		encounter = self._make_encounter(self.patient)
		api.save_derma_annotation(
			{
				"doctype": "Patient Encounter",
				"docname": encounter.name,
				"file_data": PIXEL_PNG,
				"json_text": json.dumps({"elements": []}),
			}
		)
		return encounter

	def _page(self, **kwargs):
		return api.get_previous_visits(self.patient, **kwargs)

	def test_lists_visits_with_a_drawing_or_an_assessment(self):
		drawn = self._with_drawing()
		assessed = self._with_assessment()
		self._make_encounter(self.patient)

		names = [visit["encounter"] for visit in self._page()["visits"]]

		self.assertCountEqual(names, [drawn.name, assessed.name])

	def test_excludes_the_current_and_cancelled_visits(self):
		current = self._with_assessment()
		cancelled = self._with_assessment()
		cancelled.submit()
		cancelled.cancel()
		cancelled.db_set("encounter_date", add_days(nowdate(), -1))

		self.assertEqual(self._page(current_encounter=current.name)["visits"], [])

	def test_preview_is_label_and_plain_text(self):
		self._with_assessment("<p>Mild erythema.</p>")
		preview = self._page()["visits"][0]["assessment"]
		self.assertIn("Mild erythema.", [row["value"] for row in preview])

	def test_editor_markup_alone_is_not_an_assessment(self):
		self._with_assessment("<p><br></p>")
		self.assertEqual(self._page()["visits"], [])

	def test_editor_entity_alone_is_not_an_assessment(self):
		self._with_assessment("<p>&nbsp;</p>")
		self.assertEqual(self._page()["visits"], [])

	def test_pages_by_five_and_says_when_more_remain(self):
		for _ in range(6):
			self._with_assessment()
		self._make_encounter(self.patient)

		first = self._page()
		second = self._page(start=first["next_start"])

		self.assertEqual((len(first["visits"]), first["has_more"]), (5, True))
		self.assertEqual((len(second["visits"]), second["has_more"]), (1, False))
		self.assertFalse(
			{v["encounter"] for v in first["visits"]} & {v["encounter"] for v in second["visits"]}
		)

	def test_is_gated(self):
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user(self._make_limited_user())
		with self.assertRaises(frappe.PermissionError):
			self._page()

	def test_negative_page_length_is_clamped_instead_of_scanning_everything(self):
		for _ in range(3):
			self._with_assessment()

		page = self._page(page_length=-1)

		self.assertEqual((len(page["visits"]), page["has_more"]), (1, True))

	def test_negative_start_is_refused(self):
		self._with_assessment()
		with self.assertRaises(frappe.ValidationError):
			self._page(start=-1)

	def test_lists_only_visits_before_the_open_one(self):
		oldest, middle, newest = (self._with_assessment() for _ in range(3))
		for days_ago, encounter in ((2, oldest), (1, middle), (0, newest)):
			encounter.db_set("encounter_date", add_days(nowdate(), -days_ago))

		names = [visit["encounter"] for visit in self._page(current_encounter=middle.name)["visits"]]

		self.assertEqual(names, [oldest.name])

	def test_a_capped_scan_hands_on_to_load_more(self):
		assessed = self._with_assessment()
		assessed.db_set("encounter_date", add_days(nowdate(), -1))
		for _ in range(3):
			self._make_encounter(self.patient)

		with patch.object(previous_visits, "SCAN_LIMIT", 2):
			first = self._page()
			second = self._page(start=first["next_start"])
			third = self._page(start=second["next_start"])

		self.assertEqual((first["visits"], first["has_more"], first["next_start"]), ([], True, 2))
		self.assertEqual(
			[visit["encounter"] for visit in second["visits"] + third["visits"]], [assessed.name]
		)

	def test_a_cancelled_procedures_drawing_is_left_out(self):
		encounter = self._make_encounter(self.patient)
		procedure = self._make_clinical_procedure(self.patient)
		procedure.db_set(api._get_clinical_procedure_encounter_field(), encounter.name)
		api.save_derma_annotation(
			{
				"clinical_procedure": procedure.name,
				"file_data": PIXEL_PNG,
				"json_text": json.dumps({"elements": []}),
			}
		)
		procedure.db_set("docstatus", 2)

		self.assertEqual(api._load_visit_drawings(encounter.name), [])


class TestVisitSummary(PrescriptionHelpers, IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self.patient = self._make_patient()
		self.encounter = self._make_encounter(self.patient)

	def _layout_field(self, fieldtypes):
		for row in assessment.get_layout(assessment.get_assessment_mode(self.encounter)):
			if row.get("is_value_field") and row.get("fieldtype") in fieldtypes:
				return row
		self.skipTest(f"This site's assessment layout has no {'/'.join(sorted(fieldtypes))} field.")

	def _link_child_row(self, table_field):
		for field in table_field.get("fields") or []:
			if field.get("fieldtype") == "Link" and frappe.db.exists(field["options"], {}):
				value = frappe.db.get_value(field["options"], {}, "name")
				label = field.get("label") or field["fieldname"]
				return {field["fieldname"]: value}, {"label": label, "value": value}
		self.skipTest(f"{table_field['fieldname']} has no Link child field with data on this site.")

	def _summary(self):
		return api.get_visit_summary(self.encounter.name)

	def test_lists_a_text_field_and_a_table_fields_real_rows(self):
		text_field = self._layout_field(TEXT_TYPES)
		table_field = self._layout_field(assessment.TABLE_FIELD_TYPES)
		child, expected_pair = self._link_child_row(table_field)
		self.encounter.set(text_field["fieldname"], "<p>Dry &amp; flaky.</p>")
		self.encounter.set(table_field["fieldname"], [child])
		self.encounter.save(ignore_permissions=True)

		fields = {field["label"]: field for field in self._summary()["assessment"]}

		self.assertEqual(fields[text_field["label"]]["value"], "Dry & flaky.")
		self.assertEqual(fields[table_field["label"]]["rows"], [[expected_pair]])

	def test_names_the_documented_mode(self):
		self.assertEqual(self._summary()["mode_label"], "Structured Assessment")

	def test_a_cancelled_procedure_is_left_out(self):
		field = api._get_clinical_procedure_encounter_field()
		if not field:
			self.skipTest("Clinical Procedure has no encounter link on this site.")
		kept, cancelled = (self._make_clinical_procedure(self.patient) for _ in range(2))
		for procedure in (kept, cancelled):
			procedure.db_set(field, self.encounter.name)
		cancelled.db_set("docstatus", 2)

		names = [procedure["name"] for procedure in self._summary()["procedures"]]

		self.assertEqual(names, [kept.name])

	def test_lists_a_prescription_row(self):
		row = self._row(drug_name="Hydrocortisone 1%", comment="Thin layer at night")
		api.set_derma_prescriptions(payload=json.dumps([row]), encounter=self.encounter.name)

		prescription = self._summary()["prescriptions"][0]

		self.assertEqual(
			(prescription["drug"], prescription["period"], prescription["comment"]),
			("Hydrocortisone 1%", row["period"], "Thin layer at night"),
		)

	def test_an_unknown_encounter_is_refused(self):
		with self.assertRaises(frappe.DoesNotExistError):
			api.get_visit_summary("HLC-ENC-does-not-exist")

	def test_a_cancelled_encounter_is_refused(self):
		self.encounter.submit()
		self.encounter.cancel()
		with self.assertRaises(frappe.ValidationError):
			self._summary()

	def test_is_gated(self):
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user(self._make_limited_user())
		with self.assertRaises(frappe.PermissionError):
			self._summary()


class TestDrawingsSection(DermaTestHelpers, IntegrationTestCase):
	def test_a_visit_without_drawings_borrows_none_from_another_visit(self):
		patient = self._make_patient()
		drawn = self._make_encounter(patient)
		api.save_derma_annotation(
			{
				"doctype": "Patient Encounter",
				"docname": drawn.name,
				"file_data": PIXEL_PNG,
				"json_text": json.dumps({"elements": []}),
			}
		)
		empty = self._make_encounter(patient)

		context = api.get_patient_derma_chart(patient_id=patient, encounter=empty.name)

		self.assertEqual((context["annotations"], context["latest_annotation"]), ([], None))
