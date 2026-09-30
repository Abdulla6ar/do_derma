from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

import do_derma.api as api
from do_derma import assessment
from do_derma.printing import inject, letterhead, note, render
from do_derma.schema import ensure_derma_schema
from do_derma.tests.test_api import DermaTestHelpers
from do_derma.tests.test_config_workspace import ConfigTemplateHelpers
from do_derma.tests.test_consumables import ConsumableHelpers

LEGACY_BLOCK = """
<!-- do_derma:assessment -->
{% if doc.custom_derma_assessment_mode == "SOAP" %}
<div class="derma-soap"><h5>Assessment (SOAP)</h5></div>
{% endif %}

<!-- do_derma:assessment-structured -->
{% set derma_structured_values = [doc.custom_symptoms_notes] | select | list %}
{% if derma_structured_values %}
<div class="derma-structured"><h5>Assessment</h5></div>
{% endif %}
"""


class PrintingTestBase(DermaTestHelpers, IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_derma_schema()

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")

	def _soap_encounter(self, **narratives):
		encounter = self._make_encounter(self._make_patient())
		encounter.set(assessment.MODE_FIELD, assessment.SOAP)
		for fieldname, value in narratives.items():
			encounter.set(fieldname, value)
		encounter.save(ignore_permissions=True)
		return encounter


class TestAssessmentPrintBlock(PrintingTestBase):
	def setUp(self):
		super().setUp()
		if not assessment.soap_is_supported():
			self.skipTest("SOAP custom fields are not installed on this site")

	def test_renders_soap_narratives_when_mode_is_soap(self):
		encounter = self._soap_encounter(
			custom_derma_soap_subjective="Itchy rash for three days",
			custom_derma_soap_objective="Erythematous plaques on both forearms",
			custom_derma_soap_assessment="Contact dermatitis",
			custom_derma_soap_plan="Topical steroid twice daily",
		)

		html = render.derma_assessment_html(encounter)

		self.assertIn("Assessment (SOAP)", html)
		self.assertIn("Itchy rash for three days", html)
		self.assertIn("Erythematous plaques on both forearms", html)
		self.assertIn("Contact dermatitis", html)
		self.assertIn("Topical steroid twice daily", html)

	def test_omits_structured_values_when_mode_is_soap(self):
		encounter = self._soap_encounter(
			custom_derma_soap_subjective="Itchy rash",
			custom_physical_examination="STRUCTURED-ONLY-MARKER",
		)

		html = render.derma_assessment_html(encounter)

		self.assertIn("Itchy rash", html)
		self.assertNotIn("STRUCTURED-ONLY-MARKER", html)

	def test_omits_empty_narratives(self):
		encounter = self._soap_encounter(custom_derma_soap_subjective="Only the subjective")

		html = render.derma_assessment_html(encounter)

		self.assertIn("Subjective", html)
		self.assertNotIn("Objective", html)
		self.assertNotIn("Plan", html)

	def test_escapes_html_in_narrative_fields(self):
		# Frappe's _sanitize_content already strips script tags on save, so the renderer is
		# asserted directly: it must escape whatever it is handed, sanitised or not.
		row = {"fieldtype": "Small Text", "fieldname": "custom_derma_soap_subjective"}

		escaped = render.format_field(row, "<script>alert('x')</script><b>bold</b>")

		self.assertNotIn("<script>", escaped)
		self.assertNotIn("<b>bold</b>", escaped)
		self.assertIn("&lt;script&gt;", escaped)

	def test_a_script_tag_never_reaches_the_printed_block(self):
		encounter = self._soap_encounter(
			custom_derma_soap_subjective="<script>alert('x')</script><b>bold</b>"
		)

		html = render.derma_assessment_html(encounter)

		self.assertNotIn("<script>", html)
		self.assertNotIn("<b>bold</b>", html)

	def test_newlines_become_line_breaks(self):
		encounter = self._soap_encounter(custom_derma_soap_plan="Line one\nLine two")

		html = render.derma_assessment_html(encounter)

		self.assertIn("Line one<br>Line two", html)

	def test_returns_empty_when_no_assessment_content(self):
		encounter = self._make_encounter(self._make_patient())

		self.assertEqual(render.derma_assessment_html(encounter), "")

	def test_renders_only_the_document_passed_in(self):
		other = self._soap_encounter(custom_derma_soap_subjective="OTHER-PATIENT-NARRATIVE")
		encounter = self._soap_encounter(custom_derma_soap_subjective="Mine alone")

		html = render.derma_assessment_html(encounter)

		self.assertIn("Mine alone", html)
		self.assertNotIn("OTHER-PATIENT-NARRATIVE", html)
		self.assertNotIn(other.name, html)

	def test_logs_and_returns_empty_on_failure(self):
		class Exploding:
			def get(self, *args, **kwargs):
				raise RuntimeError("boom")

		self.assertEqual(render.derma_assessment_html(Exploding()), "")


class TestOnePrintPerReportType(PrintingTestBase):
	"""Each report type prints on its own; a mode-pinned block never mixes formats."""

	def setUp(self):
		super().setUp()
		if not (assessment.soap_is_supported() and assessment.has_field("Patient Encounter", "custom_derma_hp_chief_complaint")):
			self.skipTest("SOAP/H&P custom fields are not installed on this site")

	def test_pinned_mode_prints_only_that_format(self):
		encounter = self._soap_encounter(
			custom_derma_soap_subjective="Itchy rash for three days",
			custom_derma_hp_chief_complaint="Itchy rash",
		)
		soap = render.derma_assessment_html(encounter, assessment.SOAP)
		hp = render.derma_assessment_html(encounter, assessment.HP)
		self.assertIn("Itchy rash for three days", soap)
		self.assertNotIn("Chief Complaint", soap)
		self.assertIn("Chief Complaint", hp)
		self.assertNotIn("Itchy rash for three days", hp)
		self.assertEqual(render.derma_assessment_html(encounter, "Nope"), "")

	def test_seeds_one_print_format_per_report_type(self):
		note.ensure_assessment_print_format()
		for mode, name in note.PRINT_FORMATS.items():
			html = frappe.db.get_value("Print Format", name, "html") or ""
			self.assertIn(f'derma_assessment_html(doc, "{mode}")', html, name)
		self.assertIn("derma_assessment_html(doc)", frappe.db.get_value("Print Format", note.PRINT_FORMAT, "html") or "")
		# the injector leaves these formats alone: they already render the block themselves
		inject.ensure_derma_blocks_in_print_formats()
		for name in note.PRINT_FORMATS.values():
			self.assertNotIn(inject.ASSESSMENT.start, frappe.db.get_value("Print Format", name, "html") or "")


class TestStructuredPrintBlock(PrintingTestBase):
	def test_renders_structured_when_mode_is_structured(self):
		encounter = self._make_encounter(self._make_patient())
		encounter.set(assessment.MODE_FIELD, assessment.STRUCTURED)
		encounter.set("custom_physical_examination", "Plaques on both forearms")
		encounter.save(ignore_permissions=True)

		html = render.derma_assessment_html(encounter)

		self.assertIn("Plaques on both forearms", html)
		self.assertNotIn("(SOAP)", html)

	def test_renders_child_table_rows_comma_joined(self):
		diagnoses = [self._get_or_create_diagnosis(f"Derma Print {index}") for index in range(2)]
		encounter = self._make_encounter(self._make_patient())
		encounter.set(assessment.MODE_FIELD, assessment.STRUCTURED)
		encounter.set("custom_differential_diagnosis", [{"diagnosis": name} for name in diagnoses])
		encounter.save(ignore_permissions=True)

		html = render.derma_assessment_html(encounter)

		self.assertIn(f"{diagnoses[0]}, {diagnoses[1]}", html)

	def test_falls_back_to_other_mode_when_resolved_mode_is_empty(self):
		if not assessment.soap_is_supported():
			self.skipTest("SOAP custom fields are not installed on this site")
		encounter = self._make_encounter(self._make_patient())
		self._set_practitioner_default(encounter.practitioner, assessment.SOAP)
		encounter.set("custom_physical_examination", "Legacy structured content")
		encounter.save(ignore_permissions=True)

		self.assertEqual(assessment.get_assessment_mode(encounter), assessment.SOAP)
		self.assertIn("Legacy structured content", render.derma_assessment_html(encounter))

	def _set_practitioner_default(self, practitioner, mode):
		original = frappe.db.get_value(
			"Healthcare Practitioner", practitioner, assessment.PRACTITIONER_DEFAULT_FIELD
		)
		self.addCleanup(
			frappe.db.set_value,
			"Healthcare Practitioner",
			practitioner,
			assessment.PRACTITIONER_DEFAULT_FIELD,
			original,
		)
		frappe.db.set_value(
			"Healthcare Practitioner", practitioner, assessment.PRACTITIONER_DEFAULT_FIELD, mode
		)

	def _get_or_create_diagnosis(self, label):
		name = f"{label} {frappe.generate_hash(length=6)}"
		doc = frappe.get_doc({"doctype": "Diagnosis", "diagnosis": name}).insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "Diagnosis", doc.name, True)
		return doc.name


class TestPrintFormatInjection(PrintingTestBase):
	def _make_print_format(self, html="<div>Body</div>", **overrides):
		values = {
			"doctype": "Print Format",
			"name": f"Derma Test Format {frappe.generate_hash(length=8)}",
			"doc_type": "Patient Encounter",
			"module": "Do Derma",
			"standard": "No",
			"custom_format": 1,
			"print_format_type": "Jinja",
			"html": html,
		}
		values.update(overrides)
		doc = frappe.get_doc(values).insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "Print Format", doc.name, True)
		return doc.name

	def _html_of(self, name):
		return frappe.db.get_value("Print Format", name, "html") or ""

	def test_injects_block_once(self):
		name = self._make_print_format()

		inject.ensure_derma_blocks_in_print_formats()

		html = self._html_of(name)
		self.assertEqual(html.count(inject.ASSESSMENT.start), 1)
		self.assertEqual(html.count(inject.ASSESSMENT.end), 1)
		self.assertIn("derma_assessment_html(doc)", html)
		self.assertIn("<div>Body</div>", html)

	def test_injects_the_consumables_block_once_beside_the_assessment_block(self):
		name = self._make_print_format()

		inject.ensure_derma_blocks_in_print_formats()

		html = self._html_of(name)
		self.assertEqual(html.count(inject.CONSUMABLES.start), 1)
		self.assertEqual(html.count(inject.CONSUMABLES.end), 1)
		self.assertIn("derma_consumables_html(doc)", html)
		self.assertEqual(html.count(inject.ASSESSMENT.start), 1)

	def test_a_reverted_format_is_repaired_on_the_next_run(self):
		name = self._make_print_format()
		inject.ensure_derma_blocks_in_print_formats()
		repaired = self._html_of(name)
		frappe.db.set_value("Print Format", name, "html", "<div>Body</div>")

		inject.ensure_derma_blocks_in_print_formats()

		self.assertEqual(self._html_of(name), repaired)

	def test_second_run_is_a_no_op(self):
		name = self._make_print_format()
		inject.ensure_derma_blocks_in_print_formats()
		first_html = self._html_of(name)
		first_modified = frappe.db.get_value("Print Format", name, "modified")

		result = inject.ensure_derma_blocks_in_print_formats()

		self.assertIn(name, result["unchanged"])
		self.assertEqual(self._html_of(name), first_html)
		self.assertEqual(frappe.db.get_value("Print Format", name, "modified"), first_modified)

	def test_replaces_legacy_hand_injected_block(self):
		name = self._make_print_format(html=f"<div>Body</div>{LEGACY_BLOCK}")

		inject.ensure_derma_blocks_in_print_formats()

		html = self._html_of(name)
		self.assertNotIn("derma_structured_values", html)
		self.assertNotIn('class="derma-soap"', html)
		self.assertEqual(html.count(inject.ASSESSMENT.start), 1)
		self.assertIn("<div>Body</div>", html)

	def test_skips_print_format_builder_formats(self):
		name = self._make_print_format(print_format_builder=1)

		result = inject.ensure_derma_blocks_in_print_formats()

		self.assertNotIn(inject.ASSESSMENT.start, self._html_of(name))
		self.assertNotIn(name, result["updated"])

	def test_skips_disabled_formats(self):
		name = self._make_print_format(disabled=1)

		inject.ensure_derma_blocks_in_print_formats()

		self.assertNotIn(inject.ASSESSMENT.start, self._html_of(name))

	def test_skips_format_with_foreign_trailing_comment(self):
		name = self._make_print_format(
			html=f"<div>Body</div>{LEGACY_BLOCK}\n<!-- clinic footer -->\n<div>Footer</div>"
		)

		result = inject.ensure_derma_blocks_in_print_formats()

		self.assertIn(name, result["skipped"])
		self.assertIn("<!-- clinic footer -->", self._html_of(name))
		self.assertNotIn(inject.ASSESSMENT.start, self._html_of(name))

	def test_leaves_other_doctypes_alone(self):
		name = self._make_print_format(doc_type="Clinical Procedure")

		inject.ensure_derma_blocks_in_print_formats()

		self.assertNotIn(inject.ASSESSMENT.start, self._html_of(name))


class TestProcedureVariablesPrintBlock(ConfigTemplateHelpers, PrintingTestBase):
	"""Clinical settings on a document handed to the patient, so nothing prints unless the
	template asked for it."""

	def setUp(self):
		super().setUp()
		self.patient = self._make_patient()
		self.encounter = self._make_encounter(self.patient)

	def _procedure(self, template):
		doc = frappe.get_doc(
			{
				"doctype": "Clinical Procedure",
				"patient": self.patient,
				"procedure_template": template,
				"practitioner": self._get_or_create_practitioner(),
				"company": frappe.db.get_value("Company", {}, "name"),
				"status": "Draft",
			}
		).insert(ignore_permissions=True)
		field = api._get_clinical_procedure_encounter_field()
		if field:
			frappe.db.set_value("Clinical Procedure", doc.name, field, self.encounter.name)
		return doc

	def test_prints_the_values_when_the_template_opted_in(self):
		template = self._make_derma_template(
			custom_derma_variables_per_procedure=1, custom_derma_print_procedure_variables=1
		)
		procedure = self._procedure(template)
		api.save_procedure_variables(procedure.name, template, {"fluence": "8 J"})

		html = render.derma_procedure_variables_html(self.encounter)

		self.assertIn("Procedure Details", html)
		self.assertIn("8 J", html)
		self.assertIn(frappe.db.get_value("Clinical Procedure Template", template, "template"), html)

	def test_prints_nothing_when_the_template_did_not_opt_in(self):
		"""The default. Suppressed even though the values exist and the chart shows them."""
		template = self._make_derma_template(
			custom_derma_variables_per_procedure=1, custom_derma_print_procedure_variables=0
		)
		procedure = self._procedure(template)
		api.save_procedure_variables(procedure.name, template, {"fluence": "8 J"})

		self.assertEqual(render.derma_procedure_variables_html(self.encounter), "")

	def test_prints_nothing_when_the_procedure_recorded_nothing(self):
		template = self._make_derma_template(
			custom_derma_variables_per_procedure=1, custom_derma_print_procedure_variables=1
		)
		self._procedure(template)

		self.assertEqual(render.derma_procedure_variables_html(self.encounter), "")

	def test_a_blank_value_is_not_printed_as_an_empty_line(self):
		template = self._make_derma_template(
			custom_derma_variables_per_procedure=1, custom_derma_print_procedure_variables=1
		)
		procedure = self._procedure(template)
		api.save_procedure_variables(procedure.name, template, {"fluence": "8 J", "passes": ""})

		html = render.derma_procedure_variables_html(self.encounter)

		self.assertIn("Fluence", html)
		self.assertNotIn("Passes", html)

	def test_escapes_whatever_it_is_handed(self):
		html = render.render_procedure_variables(
			[{"procedure": "<b>Laser</b>", "rows": [{"label": "Device", "value": "<script>x</script>"}]}]
		)

		self.assertNotIn("<script>", html)
		self.assertNotIn("<b>Laser</b>", html)
		self.assertIn("&lt;script&gt;", html)

	def test_logs_and_returns_empty_on_failure(self):
		class Exploding:
			def get(self, *args, **kwargs):
				raise RuntimeError("boom")

		self.assertEqual(render.derma_procedure_variables_html(Exploding()), "")


class TestConsumablesPrintBlock(ConsumableHelpers, ConfigTemplateHelpers, PrintingTestBase):
	"""The paper record says what each procedure consumed, or says nothing at all."""

	def setUp(self):
		super().setUp()
		self.patient = self._make_patient()
		self.encounter = self._make_encounter(self.patient)

	def test_lists_item_quantity_unit_and_batch_grouped_by_procedure(self):
		item = self._make_stock_item(has_batch_no=1)
		batch = self._make_batch(item)
		template = self._make_consuming_template([])
		mark = self._make_mark(procedure_template=template, encounter=self.encounter.name)
		api.save_consumables(
			"Derma Chart Mark", mark.name, [{"item_code": item, "qty": 3, "uom": "Nos", "batch_no": batch}]
		)

		html = render.derma_consumables_html(self.encounter)

		self.assertIn("Consumables", html)
		self.assertIn(frappe.db.get_value("Clinical Procedure Template", template, "template"), html)
		self.assertIn("3 Nos", html)
		self.assertIn(batch, html)

	def test_renders_nothing_when_the_session_consumed_nothing(self):
		self.assertEqual(render.derma_consumables_html(self.encounter), "")

	def test_escapes_whatever_it_is_handed(self):
		lines = render.consumable_lines([{"item_name": "<b>Gauze</b>", "qty": 1, "uom": "Nos"}])

		self.assertNotIn("<b>", lines[0])
		self.assertIn("&lt;b&gt;", lines[0])

	def test_logs_and_returns_empty_on_failure(self):
		class Exploding:
			def get(self, *args, **kwargs):
				raise RuntimeError("boom")

		self.assertEqual(render.derma_consumables_html(Exploding()), "")


class TestPrintedEncounter(PrintingTestBase):
	def setUp(self):
		super().setUp()
		if not assessment.soap_is_supported():
			self.skipTest("SOAP custom fields are not installed on this site")

	def test_soap_note_reaches_the_rendered_print_format(self):
		name = f"Derma Test Format {frappe.generate_hash(length=8)}"
		doc = frappe.get_doc(
			{
				"doctype": "Print Format",
				"name": name,
				"doc_type": "Patient Encounter",
				"module": "Do Derma",
				"standard": "No",
				"custom_format": 1,
				"print_format_type": "Jinja",
				"html": "<div>Body</div>",
			}
		).insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "Print Format", doc.name, True)
		inject.ensure_derma_blocks_in_print_formats()
		encounter = self._soap_encounter(custom_derma_soap_plan="Topical steroid twice daily")

		printed = frappe.get_print("Patient Encounter", encounter.name, print_format=name)

		self.assertIn("Topical steroid twice daily", printed)
		self.assertIn("Assessment (SOAP)", printed)

	def test_patient_advice_prints_only_when_opted_in(self):
		note.ensure_assessment_print_format()
		encounter = self._soap_encounter(
			custom_derma_soap_plan="Topical steroid twice daily",
			custom_derma_patient_advice="Apply the cream <b>twice</b> a day.",
			custom_derma_patient_advice_ar="ضعي الكريم مرتين يومياً.",
		)
		fmt = note.PRINT_FORMATS[assessment.SOAP]

		printed = frappe.get_print("Patient Encounter", encounter.name, print_format=fmt)
		self.assertNotIn("Patient Advice", printed)

		frappe.db.set_value("Patient Encounter", encounter.name, "custom_derma_print_patient_advice", 1)
		printed = frappe.get_print("Patient Encounter", encounter.name, print_format=fmt)
		self.assertIn("Patient Advice", printed)
		self.assertIn("Apply the cream &lt;b&gt;twice&lt;/b&gt; a day.", printed)  # Auto + English report = English only
		self.assertNotIn("ضعي الكريم مرتين يومياً.", printed)
		self.assertIn("Topical steroid twice daily", printed)

	def test_diagnosis_and_icd10_print_with_the_note(self):
		note.ensure_assessment_print_format()
		encounter = self._soap_encounter(
			custom_derma_soap_plan="Topical steroid twice daily",
			custom_derma_ai_diagnosis="Irritant contact dermatitis",
			custom_derma_icd10="L24.0",
		)
		printed = frappe.get_print("Patient Encounter", encounter.name, print_format=note.PRINT_FORMATS[assessment.SOAP])
		self.assertIn("Irritant contact dermatitis", printed)
		self.assertIn("L24.0", printed)
		self.assertLess(printed.index("L24.0"), printed.index("Topical steroid twice daily"))

	def test_note_prints_on_the_clinic_letterhead(self):
		note.ensure_assessment_print_format()
		encounter = self._soap_encounter(custom_derma_soap_plan="Topical steroid twice daily")
		printed = frappe.get_print("Patient Encounter", encounter.name, print_format=note.PRINT_FORMATS[assessment.SOAP])
		self.assertIn(letterhead.LOGO_SRC, printed)
		self.assertIn('id="footer-html"', printed)
		self.assertIn("CR No. 100506-1", printed)
		self.assertLess(printed.index(letterhead.LOGO_SRC), printed.index("Topical steroid twice daily"))
		# Frappe's `table td div { page-break-inside: avoid }` would push the whole body to page 2.
		self.assertIn(".derma-letterhead td div { page-break-inside: auto !important; }", printed)
		self.assertIn('class="derma-signature"', printed)

	def test_doctor_with_own_logo_prints_it_instead_of_the_clinic_logo(self):
		note.ensure_assessment_print_format()
		encounter = self._soap_encounter(custom_derma_soap_plan="Compression stockings")
		doctor = frappe.db.get_value("Healthcare Practitioner", encounter.practitioner, "practitioner_name")
		key = " ".join(word for word in doctor.lower().replace("_", " ").split() if word.isalpha())
		with patch.dict(letterhead.PRACTITIONER_LOGO_FILES, {key: "dr-sadiq-abdulla-logo.png"}):
			printed = frappe.get_print("Patient Encounter", encounter.name, print_format=note.PRINT_FORMATS[assessment.SOAP])
		self.assertIn(letterhead.PRACTITIONER_LOGO_SRCS["dr-sadiq-abdulla-logo.png"], printed)
		self.assertNotIn(letterhead.LOGO_SRC, printed)
		self.assertIn("CR No. 100506-1", printed)

	def test_arabic_vowel_marks_are_dropped_so_every_word_prints_in_one_font(self):
		note.ensure_assessment_print_format()
		encounter = self._soap_encounter(
			custom_derma_soap_plan="كريم موضعي مرتين يومياً",
			custom_derma_patient_advice_ar="يرجى العودة بعد ستة أسابيع. اعتنِ بنفسك!",
			custom_derma_patient_advice_language="Arabic",
			custom_derma_print_patient_advice=1,
		)
		printed = frappe.get_print("Patient Encounter", encounter.name, print_format=note.PRINT_FORMATS[assessment.SOAP])
		self.assertIn("اعتن بنفسك!", printed)
		self.assertIn("مرتين يوميا", printed)
		self.assertNotIn("ِ", printed)

	def test_logo_file_matches_the_doctor_name_loosely(self):
		for name in ("Dr Sadiq Abdulla", "Dr. Sadiq  Abdulla", "DR. SADIQ ABDULLA"):
			self.assertEqual(letterhead.get_logo_file(name), "dr-sadiq-abdulla-logo.png")
		for name in ("Dr Nedhal Khalifa", "", None):
			self.assertEqual(letterhead.get_logo_file(name), letterhead.LOGO_FILE)

	def test_advice_language_follows_the_report_or_the_doctor(self):
		encounter = self._soap_encounter(
			custom_derma_soap_plan="كريم موضعي مرتين يومياً",
			custom_derma_patient_advice="Apply the cream twice a day.",
			custom_derma_patient_advice_ar="ضعي الكريم مرتين يوميا.",
			custom_derma_print_patient_advice=1,
		)
		fmt = note.PRINT_FORMATS[assessment.SOAP]

		self.assertEqual(note.advice_languages(encounter), ["Arabic"])  # Auto: the report is Arabic
		printed = frappe.get_print("Patient Encounter", encounter.name, print_format=fmt)
		self.assertIn("ضعي الكريم مرتين يوميا.", printed)
		self.assertNotIn("Apply the cream twice a day.", printed)

		for choice, present, absent in (
			("English", "Apply the cream twice a day.", "ضعي الكريم مرتين يوميا."),
			("Arabic", "ضعي الكريم مرتين يوميا.", "Apply the cream twice a day."),
		):
			frappe.db.set_value("Patient Encounter", encounter.name, "custom_derma_patient_advice_language", choice)
			printed = frappe.get_print("Patient Encounter", encounter.name, print_format=fmt)
			self.assertIn(present, printed, choice)
			self.assertNotIn(absent, printed, choice)

		frappe.db.set_value("Patient Encounter", encounter.name, "custom_derma_patient_advice_language", "Both")
		printed = frappe.get_print("Patient Encounter", encounter.name, print_format=fmt)
		self.assertIn("Apply the cream twice a day.", printed)
		self.assertIn("ضعي الكريم مرتين يوميا.", printed)

	def test_blank_chosen_version_falls_back_to_the_other(self):
		encounter = self._soap_encounter(
			custom_derma_soap_plan="Topical steroid twice daily",
			custom_derma_patient_advice_ar="ضعي الكريم مرتين يوميا.",
			custom_derma_print_patient_advice=1,
			custom_derma_patient_advice_language="English",
		)
		printed = frappe.get_print("Patient Encounter", encounter.name, print_format=note.PRINT_FORMATS[assessment.SOAP])
		self.assertIn("ضعي الكريم مرتين يوميا.", printed)
