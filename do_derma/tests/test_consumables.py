from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate

import do_derma.api as api
from do_derma.consumables import defaults, snapshot
from do_derma.consumables import encounter as consumable_encounter
from do_derma.readiness import inventory
from do_derma.tests.test_api import DermaTestHelpers
from do_derma.tests.test_config_workspace import ConfigTemplateHelpers


class ConsumableHelpers:
	def _make_stock_item(self, has_batch_no=0, stock_uom="Nos"):
		token = frappe.generate_hash(length=8)
		return (
			frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": f"DermaStock{token}",
					"item_name": f"Derma Stock {token}",
					"item_group": frappe.db.get_value("Item Group", {}, "name"),
					"stock_uom": stock_uom,
					"is_stock_item": 1,
					"has_batch_no": has_batch_no,
					"create_new_batch": has_batch_no,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def _make_convertible_item(self, uom, conversion_factor, **custom):
		"""A stock item that also converts from one other unit, so a unit change has somewhere to go."""
		item_code = self._make_stock_item(**custom)
		doc = frappe.get_doc("Item", item_code)
		doc.append("uoms", {"uom": uom, "conversion_factor": conversion_factor})
		doc.save(ignore_permissions=True)
		return item_code

	def _make_batch(self, item_code, expiry_date=None):
		return (
			frappe.get_doc(
				{
					"doctype": "Batch",
					"batch_id": f"DermaBatch{frappe.generate_hash(length=8)}",
					"item": item_code,
					"expiry_date": expiry_date,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def _make_consuming_template(self, rows, **custom):
		"""A Clinical Procedure Template that consumes stock, carrying the given rows."""
		template = self._make_derma_template(**custom)
		doc = frappe.get_doc("Clinical Procedure Template", template)
		doc.consume_stock = 1
		for row in rows:
			doc.append("items", row)
		doc.save(ignore_permissions=True)
		return template

	def _consumable_row(self, item_code, qty=1, **extra):
		return {"item_code": item_code, "qty": qty, "uom": "Nos", "stock_uom": "Nos", **extra}

	def _make_mark(self, **values):
		return frappe.get_doc(
			{
				"doctype": "Derma Chart Mark",
				"patient": self.patient,
				"x_percent": 10,
				"y_percent": 20,
				**values,
			}
		).insert(ignore_permissions=True)


class TestConsumableDefaults(ConsumableHelpers, ConfigTemplateHelpers, DermaTestHelpers, IntegrationTestCase):
	"""A mark takes the template's list once and then owns it."""

	def setUp(self):
		self.patient = self._make_patient()

	def test_a_template_with_items_gives_the_mark_those_items(self):
		item = self._make_stock_item()
		template = self._make_consuming_template([self._consumable_row(item, qty=3)])

		mark = self._make_mark(procedure_template=template)

		self.assertEqual([row.item_code for row in mark.consumables], [item])
		self.assertEqual(mark.consumables[0].qty, 3)

	def test_a_template_that_does_not_consume_stock_gives_the_mark_no_items(self):
		item = self._make_stock_item()
		template = self._make_consuming_template([self._consumable_row(item)])
		frappe.db.set_value("Clinical Procedure Template", template, "consume_stock", 0)

		mark = self._make_mark(procedure_template=template)

		self.assertEqual(mark.consumables, [])
		self.assertEqual(snapshot.load(mark.default_consumables_json), [])

	def test_a_mark_with_no_template_carries_nothing(self):
		mark = self._make_mark()

		self.assertEqual(mark.consumables, [])
		self.assertFalse(mark.default_consumables_json)

	def test_rows_added_by_hand_survive_a_save(self):
		item = self._make_stock_item()
		mark = self._make_mark()
		mark.append("consumables", self._consumable_row(item, qty=2))
		mark.save(ignore_permissions=True)

		mark.reload()
		mark.note = "Second save."
		mark.save(ignore_permissions=True)

		self.assertEqual([row.item_code for row in mark.consumables], [item])

	def test_rows_added_by_hand_join_the_templates_defaults_on_insert(self):
		default_item = self._make_stock_item()
		hand_item = self._make_stock_item()
		template = self._make_consuming_template([self._consumable_row(default_item)])

		mark = self._make_mark(
			procedure_template=template, consumables=[self._consumable_row(hand_item, qty=2)]
		)

		self.assertEqual(sorted(row.item_code for row in mark.consumables), sorted([default_item, hand_item]))

	def test_hand_rows_survive_a_template_that_tracks_no_stock(self):
		hand_item = self._make_stock_item()
		template = self._make_derma_template()

		mark = self._make_mark(
			procedure_template=template, consumables=[self._consumable_row(hand_item, qty=2)]
		)

		self.assertEqual([row.item_code for row in mark.consumables], [hand_item])
		self.assertEqual(snapshot.load(mark.default_consumables_json), [])

	def test_editing_the_template_leaves_an_existing_mark_alone(self):
		item = self._make_stock_item()
		other_item = self._make_stock_item()
		template = self._make_consuming_template([self._consumable_row(item, qty=1)])
		mark = self._make_mark(procedure_template=template)

		template_doc = frappe.get_doc("Clinical Procedure Template", template)
		template_doc.append("items", self._consumable_row(other_item, qty=5))
		template_doc.save(ignore_permissions=True)
		mark.reload()
		mark.note = "Charted after the template changed."
		mark.save(ignore_permissions=True)

		self.assertEqual([row.item_code for row in mark.consumables], [item])
		self.assertEqual([row["item_code"] for row in snapshot.load(mark.default_consumables_json)], [item])

	def test_changing_the_marks_template_replaces_rows_and_snapshot(self):
		first_item = self._make_stock_item()
		second_item = self._make_stock_item()
		first = self._make_consuming_template([self._consumable_row(first_item)])
		second = self._make_consuming_template([self._consumable_row(second_item, qty=4)])
		mark = self._make_mark(procedure_template=first)

		mark.procedure_template = second
		mark.save(ignore_permissions=True)

		self.assertEqual([row.item_code for row in mark.consumables], [second_item])
		self.assertEqual(
			[row["item_code"] for row in snapshot.load(mark.default_consumables_json)], [second_item]
		)

	def test_defaults_are_empty_without_the_healthcare_doctypes(self):
		with patch.object(api, "_has_doctype", return_value=False):
			self.assertEqual(defaults.get_template_consumables("Anything"), [])

	def test_a_template_unit_the_item_cannot_convert_reaches_the_mark_with_no_factor(self):
		item = self._make_stock_item(stock_uom="Box")
		template = self._make_consuming_template(
			[self._consumable_row(item, uom="Nos", stock_uom="Box", conversion_factor=1)]
		)

		mark = self._make_mark(procedure_template=template)

		self.assertEqual(mark.consumables[0].conversion_factor, 0)

	def test_a_template_unit_the_item_converts_carries_the_items_own_factor(self):
		item = self._make_convertible_item("Box", 10)
		template = self._make_consuming_template([self._consumable_row(item, uom="Box", conversion_factor=1)])

		mark = self._make_mark(procedure_template=template)

		self.assertEqual(mark.consumables[0].conversion_factor, 10)

	def test_a_row_in_the_stock_unit_needs_no_factor_recorded_on_the_template(self):
		item = self._make_stock_item()
		template = self._make_consuming_template([self._consumable_row(item, conversion_factor=0)])

		mark = self._make_mark(procedure_template=template)

		self.assertEqual(mark.consumables[0].conversion_factor, 1)

	def test_a_template_row_naming_the_wrong_stock_unit_is_judged_by_the_item(self):
		item = self._make_stock_item(stock_uom="Box")
		template = self._make_consuming_template(
			[self._consumable_row(item, uom="Nos", stock_uom="Nos", conversion_factor=1)]
		)

		mark = self._make_mark(procedure_template=template)

		self.assertEqual(mark.consumables[0].stock_uom, "Box")
		self.assertEqual(mark.consumables[0].conversion_factor, 0)

	def test_the_frozen_defaults_keep_the_factor_they_were_written_with(self):
		item = self._make_convertible_item("Box", 10)
		template = self._make_consuming_template([self._consumable_row(item, uom="Box")])
		mark = self._make_mark(procedure_template=template)

		doc = frappe.get_doc("Item", item)
		next(row for row in doc.uoms if row.uom == "Box").conversion_factor = 5
		doc.save(ignore_permissions=True)

		mark.reload()
		self.assertEqual(snapshot.load(mark.default_consumables_json)[0]["conversion_factor"], 10)


class TestConsumablesApi(ConsumableHelpers, ConfigTemplateHelpers, DermaTestHelpers, IntegrationTestCase):
	"""What the chart reads and what it may write back."""

	def setUp(self):
		self.patient = self._make_patient()
		self.item = self._make_stock_item()
		self.template = self._make_consuming_template([self._consumable_row(self.item, qty=2)])
		self.mark = self._make_mark(procedure_template=self.template)
		self.addCleanup(frappe.set_user, "Administrator")

	def _payload_mark(self):
		marks = api._get_marks(self.patient)
		return next(row for row in marks if row["name"] == self.mark.name)

	def test_a_marks_consumables_arrive_in_the_chart_payload(self):
		mark = self._payload_mark()

		self.assertEqual([row["item_code"] for row in mark["consumables"]], [self.item])
		self.assertEqual(mark["consumables"][0]["qty"], 2)

	def test_an_untouched_row_arrives_as_default_and_a_changed_one_as_overridden(self):
		self.assertFalse(self._payload_mark()["consumables"][0]["is_overridden"])

		api.save_consumables("Derma Chart Mark", self.mark.name, [{"item_code": self.item, "qty": 5}])

		self.assertTrue(self._payload_mark()["consumables"][0]["is_overridden"])

	def test_a_removed_default_arrives_named_so_it_can_be_restored(self):
		api.save_consumables("Derma Chart Mark", self.mark.name, [])

		mark = self._payload_mark()
		self.assertEqual(mark["consumables"], [])
		self.assertEqual([row["item_code"] for row in mark["removed_consumables"]], [self.item])
		self.assertEqual([row["item_code"] for row in mark["default_consumables"]], [self.item])

	def test_previous_visit_marks_carry_their_consumables_too(self):
		rows = api._get_previous_marks(self.patient, current_encounter="DOES-NOT-EXIST")

		mark = next(row for row in rows if row["name"] == self.mark.name)
		self.assertEqual([row["item_code"] for row in mark["consumables"]], [self.item])

	def test_saving_replaces_the_whole_list(self):
		other = self._make_stock_item()

		result = api.save_consumables(
			"Derma Chart Mark", self.mark.name, [{"item_code": other, "qty": 1, "uom": "Nos"}]
		)

		self.assertEqual([row["item_code"] for row in result["consumables"]], [other])
		self.assertEqual([row["item_code"] for row in self._payload_mark()["consumables"]], [other])

	def test_saving_never_alters_the_frozen_snapshot(self):
		api.save_consumables("Derma Chart Mark", self.mark.name, [{"item_code": self.item, "qty": 9}])

		self.mark.reload()
		self.assertEqual(
			[row["item_code"] for row in snapshot.load(self.mark.default_consumables_json)], [self.item]
		)
		self.assertEqual(snapshot.load(self.mark.default_consumables_json)[0]["qty"], 2)

	def test_a_save_without_clinical_access_is_refused(self):
		frappe.set_user(self._make_limited_user())

		with self.assertRaises(frappe.PermissionError):
			api.save_consumables("Derma Chart Mark", self.mark.name, [])

	def test_a_save_against_a_closed_encounter_is_refused(self):
		encounter = self._make_encounter(self.patient, docstatus=1)
		mark = self._make_mark(procedure_template=self.template, encounter=encounter.name)

		with self.assertRaises(frappe.ValidationError) as caught:
			api.save_consumables("Derma Chart Mark", mark.name, [])

		self.assertIn("Reopen it to make changes", str(caught.exception))

	def test_an_unknown_item_is_refused_by_name(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			api.save_consumables(
				"Derma Chart Mark", self.mark.name, [{"item_code": "NO-SUCH-ITEM", "qty": 1}]
			)

		self.assertIn("NO-SUCH-ITEM", str(caught.exception))

	def test_a_quantity_of_zero_or_less_is_refused_by_name(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			api.save_consumables("Derma Chart Mark", self.mark.name, [{"item_code": self.item, "qty": 0}])

		self.assertIn(self.item, str(caught.exception))

	def test_a_batch_belonging_to_another_item_is_refused(self):
		other = self._make_stock_item(has_batch_no=1)
		batch = self._make_batch(other)

		with self.assertRaises(frappe.ValidationError) as caught:
			api.save_consumables(
				"Derma Chart Mark", self.mark.name, [{"item_code": self.item, "qty": 1, "batch_no": batch}]
			)

		self.assertIn(batch, str(caught.exception))

	def test_a_batched_item_without_a_batch_is_saved_so_the_line_is_not_lost(self):
		batched = self._make_stock_item(has_batch_no=1)

		result = api.save_consumables("Derma Chart Mark", self.mark.name, [{"item_code": batched, "qty": 1}])

		self.assertEqual([row["item_code"] for row in result["consumables"]], [batched])
		self.assertIsNone(result["consumables"][0]["batch_no"])

	def test_a_unit_the_item_does_not_convert_is_refused_at_the_save(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			api.save_consumables(
				"Derma Chart Mark", self.mark.name, [{"item_code": self.item, "qty": 1, "uom": "Box"}]
			)

		self.assertIn("Box", str(caught.exception))

	def test_a_unit_change_on_a_multi_unit_item_can_be_made_and_undone(self):
		item = self._make_convertible_item("Box", 10)

		changed = api.save_consumables(
			"Derma Chart Mark", self.mark.name, [{"item_code": item, "qty": 1, "uom": "Box"}]
		)
		undone = api.save_consumables(
			"Derma Chart Mark", self.mark.name, [{"item_code": item, "qty": 1, "uom": "Nos"}]
		)

		self.assertEqual(
			(changed["consumables"][0]["uom"], changed["consumables"][0]["conversion_factor"]), ("Box", 10)
		)
		self.assertEqual(
			(undone["consumables"][0]["uom"], undone["consumables"][0]["conversion_factor"]), ("Nos", 1)
		)

	def test_the_only_batch_on_the_list_becomes_the_marks_lot_and_expiry(self):
		batched = self._make_stock_item(has_batch_no=1)
		expiry = add_days(nowdate(), 30)
		batch = self._make_batch(batched, expiry_date=expiry)

		api.save_consumables(
			"Derma Chart Mark", self.mark.name, [{"item_code": batched, "qty": 1, "batch_no": batch}]
		)

		self.mark.reload()
		self.assertEqual(self.mark.lot_no, batch)
		self.assertEqual(str(self.mark.expiry_date), expiry)

	def test_two_batches_leave_the_marks_lot_alone_rather_than_naming_one(self):
		batched = self._make_stock_item(has_batch_no=1)
		first = self._make_batch(batched, expiry_date=add_days(nowdate(), 30))
		second = self._make_batch(batched, expiry_date=add_days(nowdate(), 60))

		api.save_consumables(
			"Derma Chart Mark",
			self.mark.name,
			[
				{"item_code": batched, "qty": 1, "batch_no": first},
				{"item_code": batched, "qty": 1, "batch_no": second},
			],
		)

		self.mark.reload()
		self.assertFalse(self.mark.lot_no)

	def test_a_lot_the_clinician_typed_is_never_overwritten_by_a_batch(self):
		batched = self._make_stock_item(has_batch_no=1)
		batch = self._make_batch(batched)
		self.mark.lot_no = "TYPED-BY-HAND"
		self.mark.save(ignore_permissions=True)

		api.save_consumables(
			"Derma Chart Mark", self.mark.name, [{"item_code": batched, "qty": 1, "batch_no": batch}]
		)

		self.mark.reload()
		self.assertEqual(self.mark.lot_no, "TYPED-BY-HAND")

	def test_an_owner_that_cannot_hold_materials_is_refused_by_name(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			api.save_consumables("Patient", self.patient, [])

		self.assertIn("Patient", str(caught.exception))

	def test_a_chosen_batch_is_stored_and_read_back(self):
		batched = self._make_stock_item(has_batch_no=1)
		batch = self._make_batch(batched)

		api.save_consumables(
			"Derma Chart Mark", self.mark.name, [{"item_code": batched, "qty": 1, "batch_no": batch}]
		)

		self.assertEqual(self._payload_mark()["consumables"][0]["batch_no"], batch)

	def test_the_payload_omits_consumables_when_the_doctypes_are_absent(self):
		with patch.object(api, "_has_doctype", side_effect=lambda name: name != "Clinical Procedure Item"):
			mark = self._payload_mark()

		self.assertNotIn("consumables", mark)


class TestConsumablesOnClinicalProcedure(
	ConsumableHelpers, ConfigTemplateHelpers, DermaTestHelpers, IntegrationTestCase
):
	"""What the clinician recorded is what the procedure consumes."""

	def setUp(self):
		self.patient = self._make_patient()
		self.encounter = self._make_encounter(self.patient).name
		self.item = self._make_stock_item()
		self.template = self._make_consuming_template([self._consumable_row(self.item, qty=2)])

	def _create_procedure(self, mark=None, procedure_template=None):
		response = api.create_derma_chart_procedure(
			{
				"patient": self.patient,
				"encounter": self.encounter,
				"procedure_template": procedure_template or self.template,
				"mark": mark,
			}
		)
		return frappe.get_doc("Clinical Procedure", response["clinical_procedure"]["name"])

	def test_a_marks_consumables_become_the_procedures_consumables(self):
		mark = self._make_mark(procedure_template=self.template, encounter=self.encounter)

		procedure = self._create_procedure(mark=mark.name)

		self.assertEqual([row.item_code for row in procedure.items], [self.item])
		self.assertEqual(procedure.items[0].qty, 2)
		self.assertTrue(procedure.consume_stock)

	def test_a_material_added_at_the_chart_appears_and_a_removed_one_does_not(self):
		added = self._make_stock_item()
		mark = self._make_mark(procedure_template=self.template, encounter=self.encounter)
		api.save_consumables("Derma Chart Mark", mark.name, [{"item_code": added, "qty": 4}])

		procedure = self._create_procedure(mark=mark.name)

		self.assertEqual([row.item_code for row in procedure.items], [added])

	def test_a_batch_chosen_at_the_chart_survives_onto_the_procedure(self):
		batched = self._make_stock_item(has_batch_no=1)
		batch = self._make_batch(batched)
		mark = self._make_mark(procedure_template=self.template, encounter=self.encounter)
		api.save_consumables(
			"Derma Chart Mark", mark.name, [{"item_code": batched, "qty": 1, "batch_no": batch}]
		)

		procedure = self._create_procedure(mark=mark.name)

		self.assertEqual(procedure.items[0].batch_no, batch)

	def test_the_quantities_healthcare_writes_during_stock_movement_are_left_empty(self):
		mark = self._make_mark(procedure_template=self.template, encounter=self.encounter)

		procedure = self._create_procedure(mark=mark.name)

		self.assertFalse(procedure.items[0].actual_qty)
		self.assertFalse(procedure.items[0].transfer_qty)

	def test_a_mark_with_no_consumables_leaves_the_procedure_as_it_was(self):
		plain = self._make_derma_template()
		mark = self._make_mark(procedure_template=plain, encounter=self.encounter)

		procedure = self._create_procedure(mark=mark.name, procedure_template=plain)

		self.assertEqual(procedure.items, [])
		self.assertFalse(procedure.consume_stock)

	def test_a_call_without_a_mark_behaves_exactly_as_before(self):
		procedure = self._create_procedure()

		self.assertEqual(procedure.items, [])
		self.assertFalse(procedure.consume_stock)

	def test_two_marks_on_one_template_produce_two_procedures_with_their_own_lists(self):
		other_item = self._make_stock_item()
		first = self._make_mark(procedure_template=self.template, encounter=self.encounter)
		second = self._make_mark(procedure_template=self.template, encounter=self.encounter)
		api.save_consumables("Derma Chart Mark", second.name, [{"item_code": other_item, "qty": 7}])

		first_procedure = self._create_procedure(mark=first.name)
		second_procedure = self._create_procedure(mark=second.name)

		self.assertNotEqual(first_procedure.name, second_procedure.name)
		self.assertEqual([row.item_code for row in first_procedure.items], [self.item])
		self.assertEqual([row.item_code for row in second_procedure.items], [other_item])

	def test_a_template_unit_the_item_cannot_convert_stops_the_procedure_by_name(self):
		item = self._make_stock_item(stock_uom="Box")
		template = self._make_consuming_template(
			[self._consumable_row(item, uom="Nos", stock_uom="Box", conversion_factor=1)]
		)
		mark = self._make_mark(procedure_template=template, encounter=self.encounter)

		with self.assertRaises(frappe.ValidationError) as caught:
			self._create_procedure(mark=mark.name, procedure_template=template)

		self.assertIn(item, str(caught.exception))
		self.assertIn("Nos", str(caught.exception))

	def test_the_procedure_stays_a_draft_and_posts_no_stock(self):
		mark = self._make_mark(procedure_template=self.template, encounter=self.encounter)
		stock_entries = frappe.db.count("Stock Entry")

		procedure = self._create_procedure(mark=mark.name)

		self.assertEqual(procedure.docstatus, 0)
		self.assertEqual(frappe.db.count("Stock Entry"), stock_entries)


class TestConsumablesInReadiness(
	ConsumableHelpers, ConfigTemplateHelpers, DermaTestHelpers, IntegrationTestCase
):
	"""One inventory picture for the session, whatever field the demand came from."""

	def setUp(self):
		self.patient = self._make_patient()
		self.item = self._make_stock_item()

	def _mark_with(self, consumables, **values):
		return {
			"name": f"MARK-{frappe.generate_hash(length=6)}",
			"consumables": consumables,
			**values,
		}

	def _line(self, item_code, qty=1, uom="Nos", conversion_factor=1, **extra):
		return {
			"item_code": item_code,
			"item_name": item_code,
			"qty": qty,
			"uom": uom,
			"conversion_factor": conversion_factor,
			"stock_uom": "Nos",
			"batch_no": None,
			**extra,
		}

	def test_a_consumable_alone_raises_a_readiness_row(self):
		rows = inventory.build([self._mark_with([self._line(self.item, qty=2)])])

		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["product_item"], self.item)
		self.assertEqual(rows[0]["dose"], 2)

	def test_a_consumable_and_a_dose_for_one_item_and_batch_add_up(self):
		batched = self._make_stock_item(has_batch_no=1)
		expiry = add_days(nowdate(), 30)
		batch = self._make_batch(batched, expiry_date=expiry)

		rows = inventory.build(
			[
				self._mark_with(
					[self._line(batched, qty=2, batch_no=batch)],
					product_item=batched,
					dose=1,
					dose_unit="Nos",
					lot_no=batch,
					expiry_date=expiry,
				)
			]
		)

		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["dose"], 3)
		self.assertEqual(sorted(rows[0]["contributors"]), ["consumable", "dose"])

	def test_the_same_item_on_two_batches_produces_two_rows(self):
		batched = self._make_stock_item(has_batch_no=1)
		first = self._make_batch(batched, expiry_date=add_days(nowdate(), 30))
		second = self._make_batch(batched, expiry_date=add_days(nowdate(), 60))

		rows = inventory.build(
			[
				self._mark_with(
					[
						self._line(batched, batch_no=first),
						self._line(batched, batch_no=second),
					]
				)
			]
		)

		self.assertEqual(len(rows), 2)

	def test_an_expired_batch_blocks_and_reads_its_expiry_from_the_batch(self):
		batched = self._make_stock_item(has_batch_no=1)
		expiry = add_days(nowdate(), -1)
		batch = self._make_batch(batched, expiry_date=expiry)

		rows = inventory.build([self._mark_with([self._line(batched, batch_no=batch)])])

		self.assertTrue(rows[0]["blocking"])
		self.assertIn("expired", rows[0]["message"].lower())
		self.assertEqual(str(rows[0]["expiry_date"]), expiry)

	def test_a_batched_item_without_a_batch_blocks(self):
		batched = self._make_stock_item(has_batch_no=1)

		rows = inventory.build([self._mark_with([self._line(batched)])])

		self.assertTrue(rows[0]["blocking"])
		self.assertIn("Batch", rows[0]["message"])

	def test_a_quantity_greater_than_the_available_balance_blocks(self):
		with patch.object(inventory, "_stock_available_qty", return_value=1):
			rows = inventory.build([self._mark_with([self._line(self.item, qty=4)])])

		self.assertTrue(rows[0]["blocking"])
		self.assertIn("Insufficient", rows[0]["message"])

	def test_a_unit_the_item_does_not_convert_is_uncheckable_rather_than_blocking(self):
		with patch.object(inventory, "_stock_available_qty", return_value=1):
			rows = inventory.build(
				[self._mark_with([self._line(self.item, qty=4, uom="Box", conversion_factor=0)])]
			)

		self.assertFalse(rows[0]["blocking"])
		self.assertIn("not available", rows[0]["message"])

	def test_every_row_names_the_contributors_it_came_from(self):
		rows = inventory.build([self._mark_with([self._line(self.item)])])

		self.assertEqual(rows[0]["contributors"], ["consumable"])

	def test_nothing_raises_when_the_stock_doctypes_are_absent(self):
		stock_doctypes = {"Batch", "Bin", "Item"}
		with patch.object(api, "_has_doctype", side_effect=lambda name: name not in stock_doctypes):
			rows = inventory.build([self._mark_with([self._line(self.item)])])

		self.assertEqual(len(rows), 1)
		self.assertIn("not available", rows[0]["message"])

	def test_the_session_readiness_counts_the_marks_consumables(self):
		template = self._make_consuming_template([self._consumable_row(self.item, qty=3)])
		encounter = self._make_encounter(self.patient).name
		self._make_mark(procedure_template=template, encounter=encounter)

		readiness = api.get_session_readiness(self.patient, encounter=encounter)

		item_rows = [row for row in readiness["items"] if row.get("product_item") == self.item]
		self.assertEqual(len(item_rows), 1)
		self.assertEqual(readiness["blockers"], [row for row in readiness["items"] if row.get("blocking")])


class TestConsumableSnapshot(IntegrationTestCase):
	"""Overridden is a comparison against the frozen list, never a stored flag."""

	def _row(self, item_code, qty=1, **extra):
		return {"item_code": item_code, "qty": qty, "uom": "Nos", "batch_no": None, **extra}

	def test_an_untouched_row_is_not_overridden(self):
		frozen = [self._row("ITEM-A", qty=2)]

		result = snapshot.compare([self._row("ITEM-A", qty=2)], frozen)

		self.assertFalse(result["consumables"][0]["is_overridden"])
		self.assertEqual(result["removed"], [])

	def test_a_changed_quantity_reads_as_overridden(self):
		result = snapshot.compare([self._row("ITEM-A", qty=3)], [self._row("ITEM-A", qty=2)])

		self.assertTrue(result["consumables"][0]["is_overridden"])

	def test_a_changed_unit_or_batch_reads_as_overridden(self):
		frozen = [self._row("ITEM-A")]

		by_unit = snapshot.compare([self._row("ITEM-A", uom="Box")], frozen)
		by_batch = snapshot.compare([self._row("ITEM-A", batch_no="BATCH-1")], frozen)

		self.assertTrue(by_unit["consumables"][0]["is_overridden"])
		self.assertTrue(by_batch["consumables"][0]["is_overridden"])

	def test_an_added_row_reads_as_overridden(self):
		result = snapshot.compare([self._row("ITEM-B")], [self._row("ITEM-A")])

		self.assertTrue(result["consumables"][0]["is_overridden"])

	def test_a_removed_default_is_reported(self):
		result = snapshot.compare([], [self._row("ITEM-A", qty=2)])

		self.assertEqual([row["item_code"] for row in result["removed"]], ["ITEM-A"])

	def test_two_live_rows_of_one_item_cannot_both_claim_one_default(self):
		result = snapshot.compare(
			[self._row("ITEM-A", qty=2), self._row("ITEM-A", qty=2)], [self._row("ITEM-A", qty=2)]
		)

		self.assertEqual(
			[row["is_overridden"] for row in result["consumables"]],
			[False, True],
		)

	def test_a_mark_with_no_snapshot_reads_as_an_empty_list(self):
		self.assertEqual(snapshot.load(None), [])
		self.assertEqual(snapshot.load("[]"), [])

	def test_an_unreadable_snapshot_raises_rather_than_reading_as_no_defaults(self):
		for stored in ["not json", '{"item_code": "A"}', "[1, 2]"]:
			with self.assertRaises(frappe.ValidationError):
				snapshot.load(stored)


class TestProcedureOwnedConsumables(
	ConsumableHelpers, ConfigTemplateHelpers, DermaTestHelpers, IntegrationTestCase
):
	"""A procedure no annotation covers records its materials on itself."""

	def setUp(self):
		self.patient = self._make_patient()
		self.encounter = self._make_encounter(self.patient).name
		self.item = self._make_stock_item()
		self.template = self._make_consuming_template([self._consumable_row(self.item, qty=2)])
		self.procedure = self._make_procedure()
		self.addCleanup(frappe.set_user, "Administrator")

	def _make_procedure(self, mark=None):
		response = api.create_derma_chart_procedure(
			{
				"patient": self.patient,
				"encounter": self.encounter,
				"procedure_template": self.template,
				"mark": mark,
			}
		)
		return frappe.get_doc("Clinical Procedure", response["clinical_procedure"]["name"])

	def _payload_procedure(self, name=None):
		rows = api._get_derma_procedures(self.patient, encounter=self.encounter)
		return next(row for row in rows if row["name"] == (name or self.procedure.name))

	def test_the_payload_offers_the_template_as_defaults_to_a_procedure_with_no_marks(self):
		row = self._payload_procedure()

		self.assertEqual(row["consumables"], [])
		self.assertEqual([line["item_code"] for line in row["default_consumables"]], [self.item])
		self.assertEqual([line["item_code"] for line in row["removed_consumables"]], [self.item])

	def test_saving_records_the_rows_on_the_procedure_itself(self):
		result = api.save_consumables(
			"Clinical Procedure", self.procedure.name, [{"item_code": self.item, "qty": 4}]
		)

		self.procedure.reload()
		self.assertEqual([row.item_code for row in self.procedure.items], [self.item])
		self.assertEqual(self.procedure.items[0].qty, 4)
		self.assertTrue(self.procedure.consume_stock)
		self.assertEqual([row["item_code"] for row in result["consumables"]], [self.item])

	def test_emptying_the_list_stops_the_procedure_consuming_stock(self):
		api.save_consumables("Clinical Procedure", self.procedure.name, [{"item_code": self.item, "qty": 4}])

		api.save_consumables("Clinical Procedure", self.procedure.name, [])

		self.procedure.reload()
		self.assertEqual(self.procedure.items, [])
		self.assertFalse(self.procedure.consume_stock)

	def test_a_saved_row_arrives_in_the_chart_payload(self):
		api.save_consumables("Clinical Procedure", self.procedure.name, [{"item_code": self.item, "qty": 4}])

		row = self._payload_procedure()
		self.assertEqual([line["item_code"] for line in row["consumables"]], [self.item])
		self.assertTrue(row["consumables"][0]["is_overridden"])

	def test_a_procedure_with_annotations_records_nothing_of_its_own(self):
		mark = self._make_mark(procedure_template=self.template, encounter=self.encounter)
		procedure = self._make_procedure(mark=mark.name)

		with self.assertRaises(frappe.ValidationError) as caught:
			api.save_consumables("Clinical Procedure", procedure.name, [{"item_code": self.item, "qty": 1}])

		self.assertIn("annotations", str(caught.exception))

	def test_a_procedure_with_annotations_is_left_to_its_marks_in_the_payload(self):
		mark = self._make_mark(procedure_template=self.template, encounter=self.encounter)
		procedure = self._make_procedure(mark=mark.name)

		row = self._payload_procedure(procedure.name)

		self.assertNotIn("consumables", row)
		self.assertEqual([entry["name"] for entry in row["derma_marks"]], [mark.name])

	def test_a_mark_carries_what_a_materials_heading_needs(self):
		"""The materials group used to be headed by the mark's autoname (DCM-2545362), which
		names nothing a practitioner can find on the drawing."""
		mark = self._make_mark(procedure_template=self.template, encounter=self.encounter)
		procedure = self._make_procedure(mark=mark.name)

		row = self._payload_procedure(procedure.name)

		entry = row["derma_marks"][0]
		self.assertEqual(entry["sequence"], mark.sequence)
		self.assertEqual(entry["procedure_template"], self.template)

	def test_a_completed_procedure_refuses_further_edits(self):
		frappe.db.set_value("Clinical Procedure", self.procedure.name, "docstatus", 1)

		with self.assertRaises(frappe.ValidationError) as caught:
			api.save_consumables("Clinical Procedure", self.procedure.name, [])

		self.assertIn("completed", str(caught.exception))

	def test_a_save_against_a_closed_encounter_is_refused(self):
		frappe.db.set_value("Patient Encounter", self.encounter, "docstatus", 1)

		with self.assertRaises(frappe.ValidationError) as caught:
			api.save_consumables("Clinical Procedure", self.procedure.name, [])

		self.assertIn("Reopen it to make changes", str(caught.exception))

	def test_a_save_without_clinical_access_is_refused(self):
		frappe.set_user(self._make_limited_user())

		with self.assertRaises(frappe.PermissionError):
			api.save_consumables("Clinical Procedure", self.procedure.name, [])

	def test_the_session_readiness_counts_a_procedures_own_materials(self):
		api.save_consumables("Clinical Procedure", self.procedure.name, [{"item_code": self.item, "qty": 3}])

		readiness = api.get_session_readiness(self.patient, encounter=self.encounter)

		rows = [row for row in readiness["items"] if row.get("product_item") == self.item]
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["dose"], 3)
		self.assertEqual(rows[0]["procedures"], [self.procedure.name])

	def test_a_material_without_a_batch_cannot_be_completed_with_a_reason(self):
		batched = self._make_stock_item(has_batch_no=1)
		api.save_consumables("Clinical Procedure", self.procedure.name, [{"item_code": batched, "qty": 1}])
		readiness = api.get_session_readiness(self.patient, encounter=self.encounter)

		with self.assertRaises(frappe.ValidationError) as caught:
			api._gate_session_completion(readiness, self.encounter, "The clinic accepts the risk.")

		self.assertIn("batch", str(caught.exception).lower())

	def test_a_material_with_its_batch_leaves_completion_to_the_usual_gate(self):
		batched = self._make_stock_item(has_batch_no=1)
		batch = self._make_batch(batched, expiry_date=add_days(nowdate(), 30))
		api.save_consumables(
			"Clinical Procedure", self.procedure.name, [{"item_code": batched, "qty": 1, "batch_no": batch}]
		)
		readiness = api.get_session_readiness(self.patient, encounter=self.encounter)

		self.assertEqual([item for item in readiness["items"] if item.get("is_hard_blocking")], [])

	def test_the_printed_list_carries_a_procedure_that_has_no_annotations(self):
		api.save_consumables("Clinical Procedure", self.procedure.name, [{"item_code": self.item, "qty": 5}])

		groups = consumable_encounter.get_encounter_consumables(self.encounter)

		self.assertEqual(len(groups), 1)
		self.assertEqual([row["item_code"] for row in groups[0]["rows"]], [self.item])

	def test_the_printed_list_names_a_procedure_once_even_with_annotations(self):
		mark = self._make_mark(procedure_template=self.template, encounter=self.encounter)
		self._make_procedure(mark=mark.name)

		groups = consumable_encounter.get_encounter_consumables(self.encounter)

		self.assertEqual(len(groups), 1)
		self.assertEqual([row["item_code"] for row in groups[0]["rows"]], [self.item])


class TestConsumableItemOptions(
	ConsumableHelpers, ConfigTemplateHelpers, DermaTestHelpers, IntegrationTestCase
):
	"""The add row is told what an item allows before it can record a bad line."""

	def setUp(self):
		self.patient = self._make_patient()
		self.item = self._make_stock_item()
		self.addCleanup(frappe.set_user, "Administrator")

	def test_the_units_offered_are_the_stock_unit_and_what_it_converts_from(self):
		doc = frappe.get_doc("Item", self.item)
		doc.append("uoms", {"uom": "Box", "conversion_factor": 10})
		doc.save(ignore_permissions=True)

		options = api.get_consumable_item_options(self.item)

		self.assertEqual(options["uoms"], ["Nos", "Box"])
		self.assertFalse(options["has_batch_no"])

	def test_a_unit_the_item_never_converts_is_not_offered(self):
		options = api.get_consumable_item_options(self.item)

		self.assertEqual(options["uoms"], ["Nos"])

	def test_an_item_that_is_not_tracked_offers_no_batches(self):
		options = api.get_consumable_item_options(self.item)

		self.assertEqual(options["batches"], [])

	def test_a_batch_with_no_stock_left_is_not_offered(self):
		batched = self._make_stock_item(has_batch_no=1)
		self._make_batch(batched, expiry_date=add_days(nowdate(), 30))

		options = api.get_consumable_item_options(batched)

		self.assertTrue(options["has_batch_no"])
		self.assertEqual(options["batches"], [])

	def test_an_unknown_item_is_refused_by_name(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			api.get_consumable_item_options("NO-SUCH-ITEM")

		self.assertIn("NO-SUCH-ITEM", str(caught.exception))

	def test_options_without_clinical_access_are_refused(self):
		frappe.set_user(self._make_limited_user())

		with self.assertRaises(frappe.PermissionError):
			api.get_consumable_item_options(self.item)
