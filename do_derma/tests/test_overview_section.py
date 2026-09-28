from __future__ import annotations

import json

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, nowdate

import do_derma.api as api
from do_derma.overview import section as overview_section
from do_derma.overview.text import short_date
from do_derma.tests.test_api import DermaTestHelpers


class OverviewHelpers(DermaTestHelpers):
	def _visit(self, days_ago=0, submit=False, **values):
		"""An encounter dated `days_ago`, created now - so creation order says nothing about it."""
		doc = frappe.get_doc(
			{
				"doctype": "Patient Encounter",
				"patient": self.patient,
				"appointment_type": self._get_or_create_appointment_type(),
				"practitioner": self._get_or_create_practitioner(),
				"encounter_date": add_days(nowdate(), -days_ago),
				"encounter_time": "10:30:00",
				"status": "Open",
				**values,
			}
		).insert(ignore_permissions=True)
		if submit:
			doc.submit()
		return doc.name

	def _category(self, title):
		if not frappe.db.exists("Derma Procedure Category", title):
			frappe.get_doc(
				{
					"doctype": "Derma Procedure Category",
					"title": title,
					"workflow": "Aesthetic",
					"marker_behavior": "numbered_dot",
				}
			).insert(ignore_permissions=True)
		return title

	def _template(self, label, category=None):
		"""A template of this test's own; with a category it is a derma one."""
		token = frappe.generate_hash(length=6)
		doc = frappe.get_doc(
			{
				"doctype": "Clinical Procedure Template",
				"template": f"{label} {token}",
				"item_code": f"{label} {token}",
				"description": label,
				"item_group": frappe.db.get_value("Item Group", {}, "name"),
			}
		)
		doc.set("is_billable", 0)
		doc.insert(ignore_permissions=True)
		if category:
			frappe.db.set_value(
				"Clinical Procedure Template",
				doc.name,
				{"custom_derma_category": self._category(category), "custom_derma_marker_behavior": "numbered_dot"},
			)
		return doc.name

	def _procedure(self, encounter, template, status="Completed", start_days_ago=0):
		doc = frappe.get_doc(
			{
				"doctype": "Clinical Procedure",
				"patient": self.patient,
				"procedure_template": template,
				"practitioner": self._get_or_create_practitioner(),
				"company": frappe.defaults.get_defaults().get("company")
				or frappe.db.get_value("Company", {}, "name"),
				"status": "Draft",
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value(
			"Clinical Procedure",
			doc.name,
			{
				api._get_clinical_procedure_encounter_field(): encounter,
				"status": status,
				"start_date": add_days(nowdate(), -start_days_ago),
			},
		)
		return doc.name

	def _mark(self, encounter, **values):
		category = values.get("category")
		if category:
			self._category(category)
		return (
			frappe.get_doc(
				{
					"doctype": "Derma Chart Mark",
					"patient": self.patient,
					"encounter": encounter,
					"x_percent": 10,
					"y_percent": 20,
					**values,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def _procedure_variable(self, procedure, template, fieldname, label, value):
		frappe.get_doc(
			{
				"doctype": "Derma Procedure Variable",
				"parent": procedure,
				"parenttype": "Clinical Procedure",
				"parentfield": api.PROCEDURE_VARIABLES_FIELD,
				"idx": frappe.db.count("Derma Procedure Variable", {"parent": procedure}) + 1,
				"procedure_template": template,
				"fieldname": fieldname,
				"label": label,
				"value": value,
			}
		).db_insert()

	def _photo_set(self, encounter, photos, **values):
		return (
			frappe.get_doc(
				{
					"doctype": "Derma Photo Set",
					"patient": self.patient,
					"encounter": encounter,
					"photos": photos,
					**values,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def _prescribe(self, encounter, *drugs):
		"""Rows as the chart's Prescription tab leaves them; the encounter's own validation would
		also want an Item behind every drug, which is beside the point here."""
		for idx, drug in enumerate(drugs, start=1):
			frappe.get_doc(
				{
					"doctype": "Drug Prescription",
					"parent": encounter,
					"parenttype": "Patient Encounter",
					"parentfield": "drug_prescription",
					"idx": idx,
					**drug,
				}
			).db_insert()

	def _diagnosis(self, title):
		title = f"{title} {frappe.generate_hash(length=4)}"
		frappe.get_doc({"doctype": "Diagnosis", "diagnosis": title}).insert(ignore_permissions=True)
		return title

	def _section(self, **kwargs):
		return overview_section.get_patient_overview_section(self.patient, **kwargs)

	def _callout(self, section, label):
		return next((callout for callout in section["callouts"] if callout["label"] == label), None)


class TestOverviewVisitSelection(OverviewHelpers, IntegrationTestCase):
	"""Which encounter the card is about. Structured fields and procedures are shared with
	dental and GP, so only what the derma chart writes may make a visit a derma one."""

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")
		self.patient = self._make_patient()

	def test_a_patient_without_derma_visits_has_no_card(self):
		self._visit(days_ago=3, custom_physical_examination="Throat clear.", custom_symptoms_notes="Sore throat")

		self.assertIsNone(self._section())

	def test_a_later_dental_or_gp_visit_is_not_the_last_derma_visit(self):
		derma = self._visit(days_ago=20, custom_derma_soap_assessment="Acne vulgaris")
		other = self._visit(days_ago=2, custom_physical_examination="Caries LL6.")
		self._procedure(other, self._template("Composite Filling"))

		section = self._section()

		self.assertEqual(section["visit"]["encounter"], derma)
		self.assertEqual(section["procedures"], [])

	def test_the_latest_visit_is_chosen_by_encounter_date_not_creation(self):
		"""Imported history is created long after it happened, so the newest row can be the oldest visit."""
		newer = self._visit(days_ago=5, custom_derma_soap_assessment="Rosacea")
		self._visit(days_ago=40, custom_derma_soap_assessment="Acne vulgaris")

		section = self._section()

		self.assertEqual(section["visit"]["encounter"], newer)
		self.assertEqual(section["visit"]["date"], str(getdate(add_days(nowdate(), -5))))
		self.assertEqual(section["visit"]["time"], "10:30:00")

	def test_a_chart_mark_alone_makes_a_derma_visit(self):
		visit = self._visit(days_ago=4)
		self._mark(visit, category="Acne", diagnosis="Acne vulgaris", marker_behavior="finding_dot")

		self.assertEqual(self._section()["visit"]["encounter"], visit)

	def test_the_excluded_encounter_is_skipped(self):
		"""Today's visit is being documented; the card must show the one before it."""
		previous = self._visit(days_ago=30, submit=True, custom_derma_soap_assessment="Melasma")
		today = self._visit(custom_derma_soap_assessment="Melasma review")
		self.assertEqual(self._section()["visit"]["encounter"], today)

		section = self._section(exclude_encounter=today)

		self.assertEqual(section["visit"]["encounter"], previous)
		self.assertIsNone(section["visit"]["warning"])

	def test_an_unfinished_session_from_an_earlier_day_is_flagged(self):
		self._visit(days_ago=7, custom_derma_soap_assessment="Tinea corporis")

		self.assertEqual(self._section()["visit"]["warning"], "Session not completed")

	def test_an_archived_mark_is_not_a_derma_visit(self):
		visit = self._visit(days_ago=3)
		self._mark(visit, category="Lesion", status="Archived", diagnosis="Naevus")

		self.assertIsNone(self._section())

	def test_archived_marks_are_left_off_the_card(self):
		visit = self._visit(days_ago=10, custom_derma_soap_assessment="Solar lentigines")
		self._mark(
			visit, category="Filler", status="Archived", product_name="HA Filler", dose=1, dose_unit="ml", diagnosis="Naevus"
		)

		section = self._section()

		self.assertEqual(section["visit"]["encounter"], visit)
		self.assertEqual(section["procedures"], [])
		self.assertEqual(section["facts"], [])
		self.assertNotIn("Naevus", json.dumps(section))

	def test_a_user_without_clinical_roles_gets_no_card(self):
		self._visit(days_ago=3, custom_derma_soap_assessment="Acne vulgaris")
		self.assertIsNotNone(self._section())

		frappe.set_user(self._make_limited_user())

		self.assertIsNone(self._section())


class TestOverviewContent(OverviewHelpers, IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")
		self.patient = self._make_patient()

	def test_the_summary_follows_the_documented_mode_and_is_plain_text(self):
		differential = self._diagnosis("Psoriasis")
		self._visit(
			days_ago=3,
			custom_derma_assessment_mode="HP",
			custom_derma_hp_chief_complaint="<p>Itchy rash on&nbsp;both   forearms</p>",
			custom_derma_soap_subjective="Written in the other mode.",
			custom_derma_hp_examination="Lichenified plaques, both antecubital fossae.",
			custom_derma_hp_assessment="Atopic dermatitis\nFlare after winter.",
			custom_derma_hp_plan="Emollients and a mid-potency topical steroid.",
			custom_derma_patient_advice="Moisturise twice daily.",
			custom_differential_diagnosis=[{"diagnosis": differential}],
		)

		section = self._section()
		summary = {item["label"]: item for item in section["summary"]}

		self.assertEqual(list(summary), ["Complaint", "Diagnosis", "Examination", "Plan"])
		self.assertEqual(summary["Complaint"]["value"], "Itchy rash on both forearms")
		self.assertEqual(summary["Diagnosis"]["value"], "Atopic dermatitis")
		self.assertEqual(summary["Diagnosis"]["secondary"], f"Ddx: {differential}")
		self.assertEqual(summary["Plan"]["value"], "Emollients and a mid-potency topical steroid.")
		advice = self._callout(section, "Advice given")
		self.assertEqual((advice["value"], advice["tone"]), ("Moisturise twice daily.", "info"))

	def test_a_soap_complaint_is_the_first_sentence_of_the_subjective(self):
		self._visit(
			days_ago=3,
			custom_derma_soap_subjective="Returns for review of glabellar lines. Happy with the result.",
		)

		summary = self._section()["summary"]

		self.assertEqual(summary[0], {"label": "Complaint", "value": "Returns for review of glabellar lines."})

	def test_procedure_tags_carry_the_dose_per_area(self):
		template = self._template("Botox Glabella", "Botox")
		visit = self._visit(days_ago=10)
		procedure = self._procedure(visit, template, start_days_ago=10)
		body_template = self._make_body_template()
		forehead = self._make_body_template_part(body_template, "Forehead")
		glabella = self._make_body_template_part(body_template, "Glabella")
		common = {
			"category": "Botox",
			"clinical_procedure": procedure,
			"procedure_template": template,
			"body_template": body_template,
			"product_name": "Botulinum A",
			"dose_unit": "Units",
			"lot_no": "LOT-7",
			"expiry_date": "2027-03-01",
		}
		self._mark(visit, body_template_part=forehead, dose=20, sequence=1, **common)
		self._mark(visit, body_template_part=glabella, dose=8, sequence=2, **common)
		self._mark(visit, body_template_part=glabella, dose=4, sequence=3, **common)

		section = self._section()
		row = section["procedures"][0]

		self.assertEqual(section["procedure_total"], 1)
		self.assertEqual(row["tags"], ["Forehead · 20 U", "Glabella · 12 U"])
		self.assertEqual(row["label"], template)
		self.assertEqual((row["status"], row["doctype"], row["name"]), ("Completed", "Clinical Procedure", procedure))
		self.assertEqual(row["detail"], "Botulinum A · 32 U · Lot LOT-7 (exp 2027-03-01)")

	def test_an_area_without_a_template_part_is_named_by_its_region_and_side(self):
		"""Units typed into a template's per-area variable count as that area's dose."""
		visit = self._visit(days_ago=10)
		self._mark(
			visit,
			category="Botox",
			product_name="Botulinum A",
			region_label="Crow's feet",
			side="Left",
			area_variables=[{"fieldname": "units", "label": "Units", "value": "6", "source": "Area"}],
		)

		row = self._section()["procedures"][0]

		self.assertEqual(row["tags"], ["Left Crow's feet · 6 U"])
		self.assertIsNone(row["status"])
		self.assertEqual(row["label"], "Botox")

	def test_session_parameters_come_from_the_procedure(self):
		template = self._template("Fractional Laser", "Laser")
		visit = self._visit(days_ago=7)
		procedure = self._procedure(visit, template, start_days_ago=7)
		self._procedure_variable(procedure, template, "fluence", "Fluence", "12")
		self._procedure_variable(procedure, template, "spot_size", "Spot Size", "6")
		self._procedure_variable(procedure, template, "device", "Device", "shown from the mark instead")
		self._mark(
			visit, category="Laser", clinical_procedure=procedure, procedure_template=template, device="CO2RE", passes=3
		)

		row = self._section()["procedures"][0]

		self.assertEqual(row["detail"], "CO2RE · 3 passes · Fluence: 12 · Spot Size: 6")

	def test_a_derma_procedure_without_marks_is_listed_and_a_dental_one_is_not(self):
		visit = self._visit(days_ago=5, custom_derma_soap_assessment="Acne scarring")
		derma = self._procedure(visit, self._template("Chemical Peel", "Acne"), status="Draft")
		self._procedure(visit, self._template("Scaling"))

		section = self._section()

		self.assertEqual([row["name"] for row in section["procedures"]], [derma])
		self.assertEqual(section["procedures"][0]["status"], "Draft")

	def test_a_procedure_entered_in_error_is_left_off(self):
		if not api._has_field("Clinical Procedure", "custom_entered_in_error"):
			self.skipTest("do_dental is not installed")
		visit = self._visit(days_ago=5, custom_derma_soap_assessment="Acne scarring")
		kept = self._procedure(visit, self._template("Chemical Peel", "Acne"))
		wrong = self._procedure(visit, self._template("Microneedling", "Acne"))
		frappe.db.set_value("Clinical Procedure", wrong, "custom_entered_in_error", 1)

		self.assertEqual([row["name"] for row in self._section()["procedures"]], [kept])

	def test_next_session_is_due_from_the_session_date(self):
		"""followup.build counts from today; a visit 100 days old must count from its own date."""
		template = self._template("Botox Forehead", "Botox")
		visit = self._visit(days_ago=100, submit=True)
		procedure = self._procedure(visit, template, start_days_ago=100)
		self._mark(visit, category="Botox", clinical_procedure=procedure, dose=20, dose_unit="Units")

		callout = self._callout(self._section(), "Next session due")

		due = add_days(nowdate(), -10)
		self.assertEqual(callout["tone"], "warning")
		self.assertEqual(callout["value"], f"Botox · due {short_date(due)} · overdue 10d")

	def test_a_session_not_yet_due_is_information(self):
		visit = self._visit(days_ago=7, submit=True)
		self._mark(visit, category="Laser", device="Nd:YAG", region_label="Upper lip")

		callout = self._callout(self._section(), "Next session due")

		self.assertEqual(callout["tone"], "info")
		self.assertEqual(callout["value"], f"Laser · due {short_date(add_days(nowdate(), 21))}")

	def test_a_biopsied_lesion_stays_a_danger_callout_until_it_is_charted_again(self):
		biopsy = self._visit(days_ago=40, submit=True)
		self._mark(
			biopsy,
			category="Biopsy",
			status="Biopsied",
			diagnosis="Seborrhoeic keratosis",
			region_label="Right Cheek",
			lesion_id="L1",
			marker_behavior="finding_dot",
		)
		last = self._visit(days_ago=5, custom_derma_soap_assessment="Acne vulgaris")

		callout = self._callout(self._section(), "Biopsied — pathology pending")

		self.assertEqual(callout["tone"], "danger")
		self.assertIn(
			f"Seborrhoeic keratosis · Right Cheek ({short_date(add_days(nowdate(), -40))})", callout["value"]
		)
		self.assertIn("overdue 37d", callout["value"])

		self._mark(last, category="Biopsy", status="Resolved", lesion_id="L1", marker_behavior="finding_dot")

		self.assertIsNone(self._callout(self._section(), "Biopsied — pathology pending"))

	def test_findings_of_that_visit_are_summarised_by_diagnosis(self):
		visit = self._visit(days_ago=2)
		for region in ("Forehead", "Chin"):
			self._mark(
				visit,
				category="Acne",
				diagnosis="Acne vulgaris",
				severity="Moderate",
				status="Active",
				region_label=region,
				marker_behavior="finding_dot",
			)

		summary = {item["label"]: item["value"] for item in self._section()["summary"]}

		self.assertEqual(summary["Findings"], "Acne vulgaris (Moderate) — Forehead, Chin")
		self.assertEqual(summary["Diagnosis"], "Acne vulgaris")

	def test_a_course_of_treatment_is_totalled_over_twelve_months(self):
		for days_ago, dose in ((400, 30), (200, 20), (100, 24), (10, 20)):
			visit = self._visit(days_ago=days_ago, submit=True)
			self._mark(visit, category="Botox", product_name="Botulinum A", dose=dose, dose_unit="Units")

		facts = self._section()["facts"]

		self.assertEqual(facts, [{"label": "Botox", "value": "3 sessions · 64 U in 12 months"}])

	def test_prescriptions_of_that_visit_are_a_fact(self):
		visit = self._visit(days_ago=2, custom_derma_soap_plan="Start doxycycline.")
		self._prescribe(
			visit,
			{"drug_name": "Doxycycline 100 mg", "dosage": "1-0-1", "period": "6 Week"},
			{"drug_code": "ADAP-GEL", "period": "3 Month"},
		)

		facts = self._section()["facts"]

		self.assertEqual(
			facts, [{"label": "Prescribed", "value": "Doxycycline 100 mg · 1-0-1 · 6 Week; ADAP-GEL · 3 Month"}]
		)


class TestOverviewPhotos(OverviewHelpers, IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")
		self.patient = self._make_patient()

	def test_before_and_after_photos_of_that_visit_lead(self):
		visit = self._visit(days_ago=3)
		self._photo_set(
			visit,
			[
				{"image": "/private/files/derma-visit.jpg", "photo_type": "Visit", "body_region": "Face"},
				{"image": "/private/files/derma-before.jpg", "photo_type": "Before", "view": "Face Front"},
			],
			set_type="Before/After",
		)

		photos = self._section()["photos"]

		self.assertEqual(
			photos,
			[
				{"url": "/private/files/derma-before.jpg", "caption": "Before · Face Front"},
				{"url": "/private/files/derma-visit.jpg", "caption": "Visit · Face"},
			],
		)

	def test_without_photos_that_visit_falls_back_to_the_latest_set(self):
		earlier = self._visit(days_ago=60, submit=True)
		self._photo_set(earlier, [{"image": "/private/files/derma-old.jpg", "photo_type": "Visit", "view": "Back"}])
		self._visit(days_ago=3, custom_derma_soap_assessment="Acne vulgaris")

		photos = self._section()["photos"]

		self.assertEqual(len(photos), 1)
		self.assertEqual(photos[0]["url"], "/private/files/derma-old.jpg")
		# Dated, so an older photo is never read as one from the visit on the card.
		self.assertTrue(photos[0]["caption"].startswith("Visit · Back · "))

	def test_a_clinical_role_that_cannot_read_photo_sets_gets_the_card_without_them(self):
		visit = self._visit(days_ago=3, custom_derma_soap_assessment="Acne vulgaris")
		self._photo_set(visit, [{"image": "/private/files/derma-private.jpg", "photo_type": "Visit"}])
		frappe.set_user(self._make_user_with_role("Physician"))

		section = self._section()

		self.assertEqual(section["visit"]["encounter"], visit)
		self.assertEqual(section["photos"], [])


class TestOverviewCost(OverviewHelpers, IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")
		self.patient = self._make_patient()

	def test_a_full_visit_stays_within_the_query_budget(self):
		template = self._template("Botox Glabella", "Botox")
		earlier = self._visit(days_ago=90, submit=True)
		self._mark(earlier, category="Botox", product_name="Botulinum A", dose=20, dose_unit="Units")
		visit = self._visit(
			days_ago=4,
			custom_derma_assessment_mode="Structured",
			custom_symptoms_notes="Frown lines",
		)
		self._prescribe(visit, {"drug_name": "Arnica gel"})
		procedure = self._procedure(visit, template, start_days_ago=4)
		self._procedure_variable(procedure, template, "dilution", "Dilution", "2.5 ml")
		for region, dose in (("Forehead", 10), ("Glabella", 20)):
			self._mark(
				visit,
				category="Botox",
				clinical_procedure=procedure,
				procedure_template=template,
				product_name="Botulinum A",
				dose=dose,
				dose_unit="Units",
				region_label=region,
				area_variables=[{"fieldname": "severity", "label": "Severity", "value": "Mild", "source": "Area"}],
			)
		self._mark(visit, category="Lesion", status="Monitoring", diagnosis="Naevus", marker_behavior="finding_dot")
		self._photo_set(visit, [{"image": "/private/files/derma-cost.jpg", "photo_type": "Before"}])
		self._section()  # warm the meta and permission caches

		with self.assertQueryCount(14):
			section = self._section()

		self.assertEqual(len(section["procedures"]), 1)
		self.assertTrue(section["photos"])
		self.assertTrue(self._callout(section, "Under monitoring"))
