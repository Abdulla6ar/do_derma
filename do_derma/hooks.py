app_name = "do_derma"
app_title = "Do Derma"
app_publisher = "Sayed Mohamed"
app_description = "Dermatology charting app for healthcare clinics"
app_email = "sayed10998@gmail.com"
app_license = "mit"

# Runtime expectations: healthcare and do_health should be installed before this
# app is used. This bench's installer treats local required_apps names as remote
# tags, so the dependency is intentionally documented instead of enforced through
# the Frappe hook. do_derma does not depend on the separate `annotation` app - it
# reads/writes Health Annotation (do_health) directly.

app_include_js = [
	"/assets/do_derma/js/derma_sidebar.js",
	"/assets/do_derma/js/annotations_button.js",
]

app_include_css = [
	"/assets/do_derma/css/annotations_button.css",
]

# Surfaces this app's drawings on the two documents that can hold them. The shims are thin on
# purpose - the shared implementation lives in annotations_button.js.
doctype_js = {
	"Patient Encounter": "public/js/doctype/patient_encounter.js",
	"Clinical Procedure": "public/js/doctype/clinical_procedure.js",
}

# A procedure's marks, treatment entries, findings and drawings are this app's records,
# so deleting the procedure deletes them instead of being refused by Frappe's link check.
doc_events = {
	"Clinical Procedure": {
		"on_trash": "do_derma.teardown.procedure.delete_derma_records",
	}
}

fixtures = [
	{"dt": "Custom Field", "filters": {"module": "Do Derma"}},
	{"dt": "Property Setter", "filters": {"module": "Do Derma"}},
]

# Lets a Patient Encounter print format render the assessment and the session's consumables
# without carrying a copy of either list. Injected into the site's formats by printing/inject.py.
jinja = {
	"methods": [
		"do_derma.printing.render.derma_assessment_html",
		"do_derma.printing.render.derma_print_text",
		"do_derma.documents.derma_letter_lines",
		"do_derma.printing.note.derma_patient_advice_html",
		"do_derma.printing.note.derma_diagnosis_html",
		"do_derma.printing.render.derma_consumables_html",
		"do_derma.printing.render.derma_procedure_variables_html",
		"do_derma.printing.letterhead.derma_letterhead_logo",
	]
}

# Contributes the "Last derma visit" card to do_health's Patient Overview drawer. do_health
# renders it; the provider only reads and returns plain data.
do_health_patient_overview_sections = ["do_derma.overview.section.get_patient_overview_section"]

# Runs on every migrate, bypassing Patch Log, so a site whose patches are recorded
# as applied but whose fields are missing converges anyway. Idempotent.
after_migrate = "do_derma.install.after_migrate"

scheduler_events = {
	"daily": ["do_derma.recordings.purge_old_audio"],
}
