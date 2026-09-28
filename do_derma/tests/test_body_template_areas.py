from __future__ import annotations

import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate, nowtime

import do_derma.api as api
from do_derma.patches.backfill_derma_mark_template_part import execute as backfill_template_part
from do_derma.tests.test_api import DermaTestHelpers


class TestMarkAreaVariables(DermaTestHelpers, IntegrationTestCase):
	"""Area variable values typed on a body-map region are clinical data on the mark,
	not a rendered badge string."""

	def _rows(self, mark):
		return frappe.get_all(
			"Derma Mark Variable",
			filters={"parent": mark, "parenttype": "Derma Chart Mark"},
			fields=["fieldname", "label", "value", "source", "idx"],
			order_by="idx asc",
		)

	def test_stores_one_row_per_declared_variable_including_blanks(self):
		patient = self._make_patient()
		mark = self._save_mark(
			patient,
			area_variables=[
				{"label": "Plane", "value": "Subdermal"},
				{"label": "Units", "value": 2.5},
				{"label": "Notes", "value": ""},
			],
		)

		rows = self._rows(mark["name"])
		self.assertEqual([row.fieldname for row in rows], ["plane", "units", "notes"])
		self.assertEqual([row.value for row in rows], ["Subdermal", "2.5", ""])
		self.assertEqual([row.label for row in rows], ["Plane", "Units", "Notes"])
		self.assertEqual({row.source for row in rows}, {"Area"})

	def test_check_values_are_stored_as_zero_or_one(self):
		patient = self._make_patient()
		mark = self._save_mark(
			patient,
			area_variables=[
				{"label": "Treated", "value": True},
				{"label": "Numbed", "value": False},
			],
		)

		self.assertEqual([row.value for row in self._rows(mark["name"])], ["1", "0"])

	def test_omitting_the_key_leaves_existing_rows_alone(self):
		patient = self._make_patient()
		mark = self._save_mark(patient, area_variables=[{"label": "Plane", "value": "Subdermal"}])

		api.save_chart_mark(json.dumps({"name": mark["name"], "patient": patient, "note": "later edit"}))

		self.assertEqual([row.value for row in self._rows(mark["name"])], ["Subdermal"])

	def test_a_variables_only_save_leaves_the_mark_where_it_was_placed(self):
		"""The studio writes area values back by name alone. Before this, the absent
		x/y clamped to 0 and moved every re-saved mark to the top-left corner."""
		patient = self._make_patient()
		mark = self._save_mark(patient, x_percent=41.5, y_percent=62.25)

		api.save_chart_mark(
			json.dumps(
				{
					"name": mark["name"],
					"patient": patient,
					"area_variables": [{"label": "Plane", "value": "Deep"}],
				}
			)
		)

		moved = frappe.db.get_value(
			"Derma Chart Mark", mark["name"], ["x_percent", "y_percent"], as_dict=True
		)
		self.assertEqual((moved.x_percent, moved.y_percent), (41.5, 62.25))

	def test_a_position_outside_the_template_is_clamped(self):
		patient = self._make_patient()
		mark = self._save_mark(patient, x_percent=140, y_percent=-12)

		self.assertEqual((mark["x_percent"], mark["y_percent"]), (100, 0))

	def test_an_empty_list_clears_the_rows(self):
		patient = self._make_patient()
		mark = self._save_mark(patient, area_variables=[{"label": "Plane", "value": "Subdermal"}])

		api.save_chart_mark(json.dumps({"name": mark["name"], "patient": patient, "area_variables": []}))

		self.assertEqual(self._rows(mark["name"]), [])

	def test_rows_arrive_json_encoded_from_the_browser(self):
		patient = self._make_patient()
		mark = self._save_mark(patient, area_variables=json.dumps([{"label": "Plane", "value": "Deep"}]))

		self.assertEqual([row.value for row in self._rows(mark["name"])], ["Deep"])

	def test_unnamed_rows_are_skipped(self):
		patient = self._make_patient()
		mark = self._save_mark(
			patient,
			area_variables=[{"label": "  ", "value": "orphan"}, {"label": "Plane", "value": "Deep"}],
		)

		self.assertEqual([row.fieldname for row in self._rows(mark["name"])], ["plane"])

	def test_marks_are_read_back_with_their_area_variables(self):
		patient = self._make_patient()
		self._save_mark(patient, area_variables=[{"label": "Plane", "value": "Subdermal"}])

		marks = api._get_marks(patient)

		self.assertEqual(len(marks), 1)
		self.assertEqual(
			marks[0]["area_variables"],
			[{"fieldname": "plane", "label": "Plane", "value": "Subdermal", "source": "Area"}],
		)

	def test_marks_without_rows_read_back_an_empty_list(self):
		patient = self._make_patient()
		self._save_mark(patient)

		self.assertEqual(api._get_marks(patient)[0]["area_variables"], [])

	def test_a_site_without_the_child_doctype_still_reads_marks(self):
		patient = self._make_patient()
		self._save_mark(patient)

		with patch.object(api, "_has_doctype", side_effect=lambda dt: dt == "Derma Chart Mark"):
			marks = api._get_marks(patient)

		self.assertEqual(len(marks), 1)
		self.assertNotIn("area_variables", marks[0])

	def test_a_site_without_the_field_ignores_the_key(self):
		patient = self._make_patient()
		real_has_field = api._has_field

		with patch.object(
			api,
			"_has_field",
			side_effect=lambda dt, fn: (
				False if (dt, fn) == ("Derma Chart Mark", "area_variables") else real_has_field(dt, fn)
			),
		):
			mark = self._save_mark(patient, area_variables=[{"label": "Plane", "value": "Subdermal"}])

		self.assertEqual(self._rows(mark["name"]), [])


class TestMarkProcedureVariables(DermaTestHelpers, IntegrationTestCase):
	"""A procedure variable that maps to no mark field is clinical data too: it must be
	stored on the mark, not silently dropped while the legend still prints it."""

	def _rows(self, mark):
		return frappe.get_all(
			"Derma Mark Variable",
			filters={"parent": mark, "parenttype": "Derma Chart Mark", "source": "Procedure"},
			fields=["fieldname", "label", "value", "source"],
			order_by="idx asc",
		)

	def test_unmapped_payload_keys_are_stored_as_procedure_rows(self):
		patient = self._make_patient()
		mark = self._save_mark(patient, units=20, product="Juvederm")

		rows = self._rows(mark["name"])
		self.assertEqual({row.fieldname: row.value for row in rows}, {"units": "20", "product": "Juvederm"})
		self.assertEqual({row.source for row in rows}, {"Procedure"})

	def test_an_explicit_dict_replaces_the_rows_including_blanks(self):
		patient = self._make_patient()
		mark = self._save_mark(patient, units=20)

		api.save_chart_mark(
			json.dumps(
				{
					"name": mark["name"],
					"patient": patient,
					"procedure_variables": {"units": "", "product": "Restylane"},
				}
			)
		)

		rows = self._rows(mark["name"])
		self.assertEqual({row.fieldname: row.value for row in rows}, {"units": "", "product": "Restylane"})

	def test_a_save_without_variable_keys_leaves_the_rows_alone(self):
		patient = self._make_patient()
		mark = self._save_mark(patient, units=15)

		api.save_chart_mark(json.dumps({"name": mark["name"], "patient": patient, "marker_size": 1.5}))

		self.assertEqual([row.value for row in self._rows(mark["name"])], ["15"])

	def test_mapped_fields_stay_on_the_mark_and_out_of_the_rows(self):
		patient = self._make_patient()
		mark = self._save_mark(patient, dose=2.5, units=20)

		self.assertEqual([row.fieldname for row in self._rows(mark["name"])], ["units"])
		self.assertEqual(frappe.db.get_value("Derma Chart Mark", mark["name"], "dose"), 2.5)

	def test_replacing_area_rows_keeps_the_procedure_rows(self):
		patient = self._make_patient()
		mark = self._save_mark(patient, units=20)

		api.save_chart_mark(
			json.dumps(
				{
					"name": mark["name"],
					"patient": patient,
					"area_variables": [{"label": "Plane", "value": "Subdermal"}],
				}
			)
		)

		self.assertEqual([row.value for row in self._rows(mark["name"])], ["20"])

	def test_replacing_procedure_rows_keeps_the_area_rows(self):
		patient = self._make_patient()
		mark = self._save_mark(patient, area_variables=[{"label": "Plane", "value": "Subdermal"}])

		api.save_chart_mark(
			json.dumps({"name": mark["name"], "patient": patient, "procedure_variables": {"units": 20}})
		)

		area_rows = frappe.get_all(
			"Derma Mark Variable",
			filters={"parent": mark["name"], "parenttype": "Derma Chart Mark", "source": "Area"},
			fields=["value"],
		)
		self.assertEqual([row.value for row in area_rows], ["Subdermal"])

	def test_marks_read_back_with_their_procedure_variables(self):
		patient = self._make_patient()
		self._save_mark(patient, units=20, area_variables=[{"label": "Plane", "value": "Deep"}])

		mark = api._get_marks(patient)[0]

		self.assertEqual(mark["procedure_variables"], {"units": "20"})
		self.assertEqual([row["source"] for row in mark["area_variables"]], ["Area"])


class TestProcedureLevelVariables(DermaTestHelpers, IntegrationTestCase):
	"""Some templates ask for one set of values for the whole procedure. The mark still wins
	where it has an answer of its own, and what it borrowed stays labelled as borrowed."""

	def _flagged_template(self):
		template = self._get_or_create_procedure_template()
		frappe.db.set_value(
			"Clinical Procedure Template", template, "custom_derma_variables_per_procedure", 1
		)
		return template

	def _procedure_mark(self, patient, procedure, template, **extra):
		return self._save_mark(patient, clinical_procedure=procedure, procedure_template=template, **extra)

	def test_stores_one_set_against_the_procedure(self):
		patient = self._make_patient()
		procedure = self._make_clinical_procedure(patient)
		template = self._flagged_template()

		stored = api.save_procedure_variables(procedure.name, template, {"fluence": 12, "passes": 2})

		self.assertEqual(stored, {"fluence": "12", "passes": "2"})

	def test_another_templates_answers_are_left_alone(self):
		patient = self._make_patient()
		procedure = self._make_clinical_procedure(patient)
		first = self._flagged_template()
		second = self._make_procedure_template_with_category(f"Derma Cat {frappe.generate_hash(length=6)}")
		api.save_procedure_variables(procedure.name, first, {"fluence": "12"})

		api.save_procedure_variables(procedure.name, second, {"fluence": "99"})

		self.assertEqual(api._procedure_level_variables(procedure.name, first), {"fluence": "12"})
		self.assertEqual(api._procedure_level_variables(procedure.name, second), {"fluence": "99"})

	def test_a_mark_borrows_what_it_does_not_carry(self):
		patient = self._make_patient()
		procedure = self._make_clinical_procedure(patient)
		template = self._flagged_template()
		api.save_procedure_variables(procedure.name, template, {"fluence": "12"})
		self._procedure_mark(patient, procedure.name, template)

		mark = api._get_marks(patient)[0]

		self.assertEqual(mark["procedure_variables"], {"fluence": "12"})
		self.assertEqual(mark["inherited_variables"], {"fluence": "12"})

	def test_the_marks_own_answer_wins(self):
		patient = self._make_patient()
		procedure = self._make_clinical_procedure(patient)
		template = self._flagged_template()
		api.save_procedure_variables(procedure.name, template, {"fluence": "12"})
		self._procedure_mark(patient, procedure.name, template, fluence="30")

		mark = api._get_marks(patient)[0]

		self.assertEqual(mark["procedure_variables"]["fluence"], "30")
		# Nothing was borrowed, so nothing may be labelled as borrowed - the copy-forward path
		# strips what is listed here, and stripping a typed value would lose it.
		self.assertEqual(mark["inherited_variables"], {})

	def test_an_override_kept_on_the_marks_own_field_still_wins(self):
		"""save_chart_mark files a fieldname the mark owns on the field itself, not as a row.
		Reading only the rows would let the shared value mask what the practitioner typed."""
		patient = self._make_patient()
		procedure = self._make_clinical_procedure(patient)
		template = self._flagged_template()
		api.save_procedure_variables(procedure.name, template, {"dose": "2"})
		self._procedure_mark(patient, procedure.name, template, dose=7)

		mark = api._get_marks(patient)[0]

		self.assertEqual(mark["dose"], 7)
		self.assertEqual(mark["inherited_variables"], {})
		self.assertNotIn("dose", mark["procedure_variables"])

	def test_an_untouched_numeric_field_still_borrows(self):
		"""A Float reads 0 when nothing was entered, which is not an answer."""
		patient = self._make_patient()
		procedure = self._make_clinical_procedure(patient)
		template = self._flagged_template()
		api.save_procedure_variables(procedure.name, template, {"dose": "2"})
		self._procedure_mark(patient, procedure.name, template)

		mark = api._get_marks(patient)[0]

		self.assertEqual(mark["inherited_variables"], {"dose": "2"})

	def test_an_unflagged_template_borrows_nothing(self):
		patient = self._make_patient()
		procedure = self._make_clinical_procedure(patient)
		template = self._get_or_create_procedure_template()
		frappe.db.set_value(
			"Clinical Procedure Template", template, "custom_derma_variables_per_procedure", 0
		)
		api.save_procedure_variables(procedure.name, template, {"fluence": "12"})
		self._procedure_mark(patient, procedure.name, template)

		mark = api._get_marks(patient)[0]

		self.assertEqual(mark["procedure_variables"], {})
		self.assertEqual(mark["inherited_variables"], {})

	def test_refuses_an_unreadable_payload_rather_than_clearing_the_set(self):
		patient = self._make_patient()
		procedure = self._make_clinical_procedure(patient)
		template = self._flagged_template()
		api.save_procedure_variables(procedure.name, template, {"fluence": "12"})

		with self.assertRaises(frappe.ValidationError):
			api.save_procedure_variables(procedure.name, template, "{not json")

		self.assertEqual(api._procedure_level_variables(procedure.name, template), {"fluence": "12"})

	def test_promoting_a_mark_carries_the_shared_values_to_the_new_procedure(self):
		"""create_procedure_from_mark re-anchors the mark onto a procedure it has just made.
		Without the carry-across the mark lands on a procedure holding none of the answers the
		practitioner typed, and the studio reopens showing blanks."""
		patient = self._make_patient()
		# Promotion refuses an encounter with no appointment, so the fixture needs both.
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
		anchor = self._make_clinical_procedure(patient)
		template = self._flagged_template()
		api.save_procedure_variables(anchor.name, template, {"fluence": "12"})
		mark = self._procedure_mark(patient, anchor.name, template, encounter=encounter.name)

		created = api.create_procedure_from_mark(mark["name"], template)

		self.assertEqual(
			api._procedure_level_variables(created["clinical_procedure"]["name"], template),
			{"fluence": "12"},
		)
		# The anchor keeps its own copy: other marks may still be pointing at it.
		self.assertEqual(api._procedure_level_variables(anchor.name, template), {"fluence": "12"})

	def test_the_studio_reads_every_templates_set_in_one_call(self):
		"""The studio seeds from this rather than from the marks, because a procedure that
		captures once may have placed none."""
		patient = self._make_patient()
		procedure = self._make_clinical_procedure(patient)
		first = self._flagged_template()
		second = self._make_procedure_template_with_category(f"Derma Cat {frappe.generate_hash(length=6)}")
		api.save_procedure_variables(procedure.name, first, {"fluence": "12"})
		api.save_procedure_variables(procedure.name, second, {"passes": "3"})

		self.assertEqual(
			api.get_procedure_variables(procedure.name),
			{first: {"fluence": "12"}, second: {"passes": "3"}},
		)

	def test_the_procedure_row_carries_its_values_and_the_flag(self):
		"""The chart row is where a procedure with no drawing is reached, so it has to know both
		what was recorded and that this template records anything at all."""
		patient = self._make_patient()
		encounter = self._make_encounter(patient)
		procedure = self._make_clinical_procedure(patient)
		template = self._flagged_template()
		frappe.db.set_value("Clinical Procedure", procedure.name, "procedure_template", template)
		self._link_encounter(procedure.name, encounter.name)
		api.save_procedure_variables(procedure.name, template, {"fluence": "8 J"})

		row = self._procedure_row(patient, encounter.name, procedure.name)

		self.assertEqual(row["derma_captures_variables_per_procedure"], 1)
		self.assertEqual(row["derma_procedure_variables_text"], "Fluence: 8 J")
		self.assertEqual([value["fieldname"] for value in row["derma_procedure_variables"]], ["fluence"])

	def test_a_flagged_procedure_with_nothing_recorded_still_offers_the_editor(self):
		"""Without the flag reaching the row there is no way in: the studio only persists these
		alongside a drawing, so a procedure that needs no drawing could never record them."""
		patient = self._make_patient()
		encounter = self._make_encounter(patient)
		procedure = self._make_clinical_procedure(patient)
		template = self._flagged_template()
		frappe.db.set_value("Clinical Procedure", procedure.name, "procedure_template", template)
		self._link_encounter(procedure.name, encounter.name)

		row = self._procedure_row(patient, encounter.name, procedure.name)

		self.assertEqual(row["derma_captures_variables_per_procedure"], 1)
		self.assertEqual(row["derma_procedure_variables_text"], "")

	def test_an_unflagged_procedure_row_offers_nothing(self):
		patient = self._make_patient()
		encounter = self._make_encounter(patient)
		procedure = self._make_clinical_procedure(patient)
		template = self._get_or_create_procedure_template()
		frappe.db.set_value(
			"Clinical Procedure Template", template, "custom_derma_variables_per_procedure", 0
		)
		frappe.db.set_value("Clinical Procedure", procedure.name, "procedure_template", template)
		self._link_encounter(procedure.name, encounter.name)

		row = self._procedure_row(patient, encounter.name, procedure.name)

		self.assertEqual(row["derma_captures_variables_per_procedure"], 0)
		self.assertEqual(row["derma_procedure_variables_text"], "")

	def _link_encounter(self, procedure, encounter):
		"""Through the app's own resolver: which field holds the encounter differs by site."""
		field = api._get_clinical_procedure_encounter_field()
		if field:
			frappe.db.set_value("Clinical Procedure", procedure, field, encounter)

	def _procedure_row(self, patient, encounter, procedure):
		rows = api._get_derma_procedures(patient, encounter=encounter)
		return next(row for row in rows if row["name"] == procedure)

	def test_a_submitted_procedure_refuses_them(self):
		"""A completed procedure is read-only; it must be reopened before the studio can write to it."""
		patient = self._make_patient()
		procedure = self._make_clinical_procedure(patient)
		template = self._flagged_template()
		frappe.db.set_value("Clinical Procedure", procedure.name, "docstatus", 1)

		with self.assertRaises(frappe.ValidationError) as caught:
			api.save_procedure_variables(procedure.name, template, {"fluence": "12"})
		self.assertIn("Reopen it to make changes", str(caught.exception))

		self.assertEqual(api._procedure_level_variables(procedure.name, template), {})


class TestBodyTemplatePartSave(DermaTestHelpers, IntegrationTestCase):
	"""Saving a body map never destroys an area a mark may be standing on."""

	def _payload_part(self, part_name, **extra):
		return {
			"part_name": part_name,
			"shape_json": [[0, 0], [10, 0], [10, 10]],
			"variables": [],
			**extra,
		}

	def _variable_rows(self, part):
		return frappe.get_all(
			"Derma Template Part Variable",
			filters={"parent": part, "parenttype": "Derma Body Template Part"},
			fields=["name", "variable_name", "type", "idx"],
			order_by="idx asc",
		)

	def test_a_removed_area_is_soft_disabled_not_deleted(self):
		template = self._make_body_template()
		part = self._make_body_template_part(template, "Left Cheek")

		api.save_derma_body_template_parts(template, json.dumps([self._payload_part("Right Cheek")]))

		self.assertTrue(frappe.db.exists("Derma Body Template Part", part))
		self.assertEqual(frappe.db.get_value("Derma Body Template Part", part, "disabled"), 1)

	def test_an_empty_payload_deletes_nothing(self):
		template = self._make_body_template()
		part = self._make_body_template_part(template, "Left Cheek")

		api.save_derma_body_template_parts(template, json.dumps([]))

		self.assertTrue(frappe.db.exists("Derma Body Template Part", part))

	def test_a_mark_still_resolves_its_area_after_it_is_removed(self):
		patient = self._make_patient()
		template = self._make_body_template()
		part = self._make_body_template_part(template, "Left Cheek")
		self._save_mark(patient, body_template=template, body_template_part=part, region_label="Left Cheek")

		api.save_derma_body_template_parts(template, json.dumps([]))

		self.assertEqual(api._get_marks(patient)[0]["body_template_part"], part)

	def test_saving_without_touching_variables_leaves_the_child_rows_alone(self):
		template = self._make_body_template()
		part = self._make_body_template_part(
			template,
			"Left Cheek",
			variables=[
				{"variable_name": "Plane", "type": "Select", "options": "Deep\nSubdermal"},
				{"variable_name": "Units", "type": "Float"},
			],
		)
		before = self._variable_rows(part)

		api.save_derma_body_template_parts(
			template,
			json.dumps(
				[
					self._payload_part(
						"Left Cheek",
						name=part,
						variables=[
							{"variable_name": "Plane", "type": "Select", "options": "Deep\nSubdermal"},
							{"variable_name": "Units", "type": "Float"},
						],
					)
				]
			),
		)

		self.assertEqual(
			[(row.name, row.idx) for row in self._variable_rows(part)],
			[(row.name, row.idx) for row in before],
		)

	def test_changed_variables_are_rewritten(self):
		template = self._make_body_template()
		part = self._make_body_template_part(
			template, "Left Cheek", variables=[{"variable_name": "Plane", "type": "Data"}]
		)

		api.save_derma_body_template_parts(
			template,
			json.dumps(
				[
					self._payload_part(
						"Left Cheek",
						name=part,
						variables=[
							{"variable_name": "Plane", "type": "Data"},
							{"variable_name": "Units", "type": "Float"},
						],
					)
				]
			),
		)

		self.assertEqual([row.variable_name for row in self._variable_rows(part)], ["Plane", "Units"])

	def test_retired_areas_are_hidden_from_the_default_read(self):
		template = self._make_body_template()
		self._make_body_template_part(template, "Left Cheek", disabled=1)
		self._make_body_template_part(template, "Right Cheek")

		parts = api.get_derma_body_template_parts(template)

		self.assertEqual([part["part_name"] for part in parts], ["Right Cheek"])

	def test_retired_areas_are_returned_when_asked_for(self):
		template = self._make_body_template()
		self._make_body_template_part(template, "Left Cheek", disabled=1)
		self._make_body_template_part(template, "Right Cheek")

		parts = api.get_derma_body_template_parts(template, include_disabled=1)

		self.assertEqual(
			{part["part_name"]: part["disabled"] for part in parts},
			{"Left Cheek": 1, "Right Cheek": 0},
		)

	def test_the_save_response_carries_the_areas_it_retired(self):
		"""The designer merges the response by part_name and lists what it retired,
		so the response must name both sets."""
		template = self._make_body_template()
		self._make_body_template_part(template, "Left Cheek")

		saved = api.save_derma_body_template_parts(template, json.dumps([self._payload_part("Right Cheek")]))

		self.assertEqual(
			{part["part_name"]: part["disabled"] for part in saved},
			{"Left Cheek": 1, "Right Cheek": 0},
		)

	def test_a_retired_area_is_restored_by_saving_it_again(self):
		template = self._make_body_template()
		part = self._make_body_template_part(template, "Left Cheek", disabled=1)

		api.save_derma_body_template_parts(
			template, json.dumps([self._payload_part("Left Cheek", name=part)])
		)

		self.assertEqual(frappe.db.get_value("Derma Body Template Part", part, "disabled"), 0)

	def test_an_area_owned_by_another_body_template_is_never_re_parented(self):
		"""The payload names the areas to keep. A name belonging to another body map
		would otherwise move that area here and strip it from the map it was drawn on."""
		template = self._make_body_template()
		other_template = self._make_body_template()
		foreign_part = self._make_body_template_part(other_template, "Left Cheek")

		api.save_derma_body_template_parts(
			template, json.dumps([self._payload_part("Left Cheek", name=foreign_part)])
		)

		stored = frappe.db.get_value(
			"Derma Body Template Part", foreign_part, ["body_template", "disabled"], as_dict=True
		)
		self.assertEqual((stored.body_template, stored.disabled), (other_template, 0))
		self.assertEqual(
			[part["part_name"] for part in api.get_derma_body_template_parts(template)], ["Left Cheek"]
		)

	def test_retired_areas_never_reach_the_chart(self):
		template = self._make_body_template()
		self._make_body_template_part(template, "Left Cheek", disabled=1)
		self._make_body_template_part(template, "Right Cheek")

		charted = next(row for row in api._get_body_templates() if row["name"] == template)

		self.assertEqual([part["part_name"] for part in charted["parts"]], ["Right Cheek"])


class TestMarkTemplatePartLink(DermaTestHelpers, IntegrationTestCase):
	"""The mark names the area it sits on by Link, while body_region keeps the coarse
	15-value vocabulary every existing read depends on."""

	def test_a_mark_placed_on_an_area_links_to_that_part(self):
		patient = self._make_patient()
		template = self._make_body_template()
		part = self._make_body_template_part(template, "Left Cheek")

		mark = self._save_mark(
			patient,
			body_template=template,
			body_template_part=part,
			body_region="Left Cheek",
			region_label="Left Cheek",
		)

		stored = frappe.db.get_value(
			"Derma Chart Mark",
			mark["name"],
			["body_template_part", "body_region", "region_label"],
			as_dict=True,
		)
		self.assertEqual(stored.body_template_part, part)
		self.assertEqual(stored.body_region, "Face")
		self.assertEqual(stored.region_label, "Left Cheek")

	def test_the_part_link_is_read_back_with_the_mark(self):
		patient = self._make_patient()
		template = self._make_body_template()
		part = self._make_body_template_part(template, "Left Cheek")
		self._save_mark(patient, body_template=template, body_template_part=part, region_label="Left Cheek")

		self.assertEqual(api._get_marks(patient)[0]["body_template_part"], part)

	def test_a_mark_placed_off_any_area_keeps_a_null_link(self):
		patient = self._make_patient()

		mark = self._save_mark(patient, body_region="Face")

		self.assertIsNone(frappe.db.get_value("Derma Chart Mark", mark["name"], "body_template_part"))

	def test_the_backfill_links_an_unambiguous_region_label(self):
		patient = self._make_patient()
		template = self._make_body_template()
		part = self._make_body_template_part(template, "Left Cheek")
		mark = self._save_mark(patient, body_template=template, region_label="Left Cheek")

		backfill_template_part()

		self.assertEqual(frappe.db.get_value("Derma Chart Mark", mark["name"], "body_template_part"), part)

	def test_the_backfill_leaves_an_ambiguous_region_label_alone(self):
		patient = self._make_patient()
		template = self._make_body_template()
		self._make_body_template_part(template, "Left Cheek")
		self._make_body_template_part(template, "Left Cheek")
		mark = self._save_mark(patient, body_template=template, region_label="Left Cheek")

		backfill_template_part()

		self.assertIsNone(frappe.db.get_value("Derma Chart Mark", mark["name"], "body_template_part"))

	def test_the_backfill_ignores_parts_of_another_body_template(self):
		patient = self._make_patient()
		template = self._make_body_template()
		other_template = self._make_body_template()
		self._make_body_template_part(other_template, "Left Cheek")
		mark = self._save_mark(patient, body_template=template, region_label="Left Cheek")

		backfill_template_part()

		self.assertIsNone(frappe.db.get_value("Derma Chart Mark", mark["name"], "body_template_part"))

	def test_the_backfill_is_re_runnable_and_never_relinks(self):
		patient = self._make_patient()
		template = self._make_body_template()
		part = self._make_body_template_part(template, "Left Cheek")
		renamed = self._make_body_template_part(template, "Right Cheek")
		mark = self._save_mark(
			patient, body_template=template, body_template_part=renamed, region_label="Left Cheek"
		)

		backfill_template_part()
		backfill_template_part()

		self.assertEqual(frappe.db.get_value("Derma Chart Mark", mark["name"], "body_template_part"), renamed)
		self.assertNotEqual(renamed, part)

	def test_a_part_from_another_body_template_is_refused(self):
		"""The annotation fan-out copies client-authored values into the payload, so a part
		that belongs to a different body template must not label the mark."""
		patient = self._make_patient()
		template = self._make_body_template()
		foreign_part = self._make_body_template_part(self._make_body_template(), "Left Cheek")

		mark = self._save_mark(patient, body_template=template, body_template_part=foreign_part)

		self.assertIsNone(frappe.db.get_value("Derma Chart Mark", mark["name"], "body_template_part"))

	def test_a_part_that_no_longer_exists_is_refused(self):
		patient = self._make_patient()
		template = self._make_body_template()

		mark = self._save_mark(patient, body_template=template, body_template_part="gone-with-the-map")

		self.assertIsNone(frappe.db.get_value("Derma Chart Mark", mark["name"], "body_template_part"))

	def test_the_backfill_is_a_no_op_on_a_site_without_the_field(self):
		patient = self._make_patient()
		template = self._make_body_template()
		self._make_body_template_part(template, "Left Cheek")
		mark = self._save_mark(patient, body_template=template, region_label="Left Cheek")
		real_has_field = api._has_field
		with patch.object(
			api,
			"_has_field",
			side_effect=lambda dt, fn: (
				False if (dt, fn) == ("Derma Chart Mark", "body_template_part") else real_has_field(dt, fn)
			),
		):
			backfill_template_part()

		self.assertIsNone(frappe.db.get_value("Derma Chart Mark", mark["name"], "body_template_part"))
