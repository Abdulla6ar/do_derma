from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from do_derma import documents, voice
from do_derma.assessment import SOAP_FIELDS
from do_derma.printing import letterhead
from do_derma.schema import ensure_derma_schema
from do_derma.tests.test_api import DermaTestHelpers

def blank_pdf() -> bytes:
	from io import BytesIO

	from pypdf import PdfWriter

	writer = PdfWriter()
	writer.add_blank_page(width=595, height=842)
	buffer = BytesIO()
	writer.write(buffer)
	return buffer.getvalue()


REPORT = "## Patient Demographic Data\nName: Test\n## Medications\n- None documented\nDr. Abdulla Sadeq\nConsultant"


class TestAiDocuments(DermaTestHelpers, IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_derma_schema()
		documents.ensure_document_templates()

	def setUp(self):
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("Administrator")
		self.encounter = self._make_encounter(self._make_patient())
		frappe.db.set_value("Patient Encounter", self.encounter.name, SOAP_FIELDS[2], "Irritant contact dermatitis")
		self.encounter.reload()

	def _enabled(self):
		return patch.object(voice, "_setting", side_effect=lambda name, default=None: {"enable_voice_scribe": 1, "llm_api_key": "k"}.get(name, default))

	def _llm(self, text):
		fake = MagicMock(status_code=200)
		fake.json.return_value = {"choices": [{"message": {"content": json.dumps({"text": text})}}]}
		return patch.object(voice.requests, "post", return_value=fake)

	def test_templates_are_seeded_once(self):
		self.assertEqual(documents.ensure_document_templates(), [])
		for spec in documents.KINDS.values():
			self.assertTrue(frappe.db.exists("Patient Print Template", {"title": documents.TEMPLATE_PREFIX + spec["title"]}))

	def test_every_kind_is_an_allowed_document_type(self):
		options = frappe.get_meta("Patient Official Document").get_field("document_type").options.split("\n")
		for spec in documents.KINDS.values():
			self.assertIn(spec["document_type"], options)
		self.assertIn("Medical Certificate", options)

	def test_prompt_carries_note_diagnosis_and_addressee(self):
		context = documents.build_document_context(self.encounter, addressee="Dr. Salman")
		self.assertEqual(context["diagnosis"], "Irritant contact dermatitis")
		self.assertIn("Assessment: Irritant contact dermatitis", context["note"])
		prompt = documents.build_document_prompt("referral", context)
		self.assertIn("ADDRESSEE (referral recipient): Dr. Salman", prompt)
		self.assertIn("DOCUMENT REQUESTED: Referral Letter", prompt)

	def test_sign_off_uses_the_encounter_doctors_own_specialty(self):
		practitioner = self.encounter.practitioner
		original = frappe.db.get_value("Healthcare Practitioner", practitioner, ["custom_specialty", "designation"], as_dict=True)
		self.addCleanup(frappe.db.set_value, "Healthcare Practitioner", practitioner, original)
		frappe.db.set_value("Healthcare Practitioner", practitioner, {"custom_specialty": "Consultant", "designation": None})
		self.assertEqual(documents.build_document_context(self.encounter)["doctor_title"], "Consultant")
		frappe.db.set_value("Healthcare Practitioner", practitioner, "custom_specialty", None)
		self.assertEqual(documents.build_document_context(self.encounter)["doctor_title"], "")

	def _doctor(self, specialty):
		return (
			frappe.get_doc({"doctype": "Healthcare Practitioner", "first_name": f"Doc{frappe.generate_hash(length=6)}", "status": "Active", "custom_specialty": specialty})
			.insert(ignore_permissions=True)
			.name
		)

	def _letter_for(self, practitioner):
		encounter = self._make_encounter(self._make_patient())
		frappe.db.set_value("Patient Encounter", encounter.name, "practitioner", practitioner)
		fake = MagicMock(status_code=200)
		fake.json.return_value = {"choices": [{"message": {"content": json.dumps({"text": "English body", "text_ar": "نص عربي"})}}]}
		with self._enabled(), patch.object(voice.requests, "post", return_value=fake):
			return frappe.get_doc("Patient Official Document", documents.generate_document("explainer", encounter.name)["name"])

	def test_every_doctor_signs_their_own_letter_in_english_by_default(self):
		for specialty in ("Consultant Dermatologist", "Consultant Vascular & Transplant Surgeon"):
			letter = self._letter_for(self._doctor(specialty))
			html = letter.get_form_preview()["html"]
			self.assertIn(frappe.db.get_value("Healthcare Practitioner", letter.practitioner, "practitioner_name"), html)
			self.assertIn(specialty, html)
			self.assertIn("English body", html)
			self.assertNotIn('dir="rtl"', html)

	def test_letter_language_option_selects_the_pages(self):
		letter = self._letter_for(self._doctor("Consultant"))
		values = letter.get_values()
		for language, has_english, has_arabic in (("Arabic", False, True), ("Both", True, True), ("English", True, False)):
			letter.values_json = json.dumps({**values, "language": language})
			html = letter.get_form_preview()["html"]
			self.assertEqual("English body" in html, has_english, language)
			self.assertEqual("نص عربي" in html, has_arabic, language)
			self.assertEqual("رسالة توضيحية للمريض" in html, has_arabic, language)

	def test_one_sign_off_from_the_record_even_when_the_body_signs(self):
		letter = self._letter_for(self._doctor("Consultant"))
		name = frappe.db.get_value("Healthcare Practitioner", letter.practitioner, "practitioner_name")
		values = letter.get_values()
		self.assertEqual(letter.get_form_preview()["html"].count(name), 2)
		letter.values_json = json.dumps({**values, "body": f"Warm regards,\n{name}\nConsultant"})
		self.assertEqual(letter.get_form_preview()["html"].count(name), 2)

	def test_generate_creates_a_draft_official_document(self):
		with self._enabled(), self._llm(REPORT) as post:
			out = documents.generate_document("report", self.encounter.name)
		system = post.call_args.kwargs["json"]["messages"][0]["content"]
		self.assertNotIn("{clinic}", system)
		self.assertIn("Medical Report", system)
		doc = frappe.get_doc("Patient Official Document", out["name"])
		self.assertEqual(doc.document_type, "Medical Report")
		self.assertEqual(doc.status, "Draft")
		self.assertEqual(doc.encounter, self.encounter.name)
		self.assertEqual(doc.get_values()["body"], REPORT)
		self.assertEqual(out["title"], "Medical Report")
		listed = documents.list_documents(self.encounter.name)
		self.assertEqual([row["name"] for row in listed], [out["name"]])

	def test_issue_renders_body_and_attaches_pdf(self):
		with self._enabled(), self._llm(REPORT):
			out = documents.generate_document("education", self.encounter.name)
		with patch("do_health.do_health.doctype.patient_official_document.patient_official_document.get_pdf", return_value=blank_pdf()) as get_pdf:
			issued = documents.issue_document(out["name"])
		self.assertIn(letterhead.LOGO_SRC, get_pdf.call_args.args[0])  # embedded, so the PDF never fetches it
		self.assertEqual(issued["status"], "Issued")
		self.assertTrue(issued["pdf_url"])
		html = frappe.db.get_value("Patient Official Document", out["name"], "rendered_html_snapshot")
		self.assertIn("<h2", html)
		self.assertIn("Patient Demographic Data", html)
		self.assertIn("&bull; None documented", html)
		self.assertIn("CR No. 100506-1", html)

	def test_unknown_kind_and_disabled_are_refused(self):
		with self._enabled():
			with self.assertRaises(frappe.ValidationError):
				documents.generate_document("poem", self.encounter.name)
		with patch.object(voice, "_setting", return_value=0):
			with self.assertRaises(frappe.ValidationError):
				documents.generate_document("report", self.encounter.name)


class TestLetterSignoff(IntegrationTestCase):
	def test_signoff_name_is_flagged_in_both_languages(self):
		english = "Thank you.\n\nYours sincerely,\nDr. Sadiq Abdulla\nConsultant Vascular Surgeon"
		arabic = "شكرا.\n\nمع خالص التحية،\nد. صادق عبد الله\nاستشاري جراحة الأوعية"  # noqa: RUF001
		self.assertEqual(documents.derma_letter_lines(english, "Dr. Sadiq Abdulla"), ["Thank you."])
		self.assertEqual(documents.derma_letter_lines(arabic, "Dr. Sadiq Abdulla", english), ["شكرا."])

	def test_name_and_clinic_lines_without_a_closing_line_are_dropped(self):
		body = "Follow-up in six weeks.\n\nDr. Sadiq Abdulla\n\nSOULVD Demo Clinic"
		self.assertEqual(documents.derma_letter_lines(body, "Dr. Sadiq Abdulla"), ["Follow-up in six weeks."])

	def test_no_signoff_when_the_body_does_not_sign(self):
		lines = documents.derma_letter_lines("## Heading\n- point\n\nText.", "Dr. Sadiq Abdulla")
		self.assertEqual(lines, ["## Heading", "- point", "Text."])
