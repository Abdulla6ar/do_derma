# Derma chart reopen, previous visits and sidebar entry: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Completed encounters lock the whole chart and offer Reopen Encounter; submitted procedures reopen one at a time inside a reopened encounter; the Assessment tab lists earlier visits' drawings and assessment; do_health's Visit History drawer opens a visit in the derma chart.

**Architecture:** Server write endpoints in `api.py` refuse writes into a submitted encounter through one owner-lookup helper next to the existing `_ensure_encounter_open`. Reopening lives in a new `do_derma/reopen.py` (docstatus back to 0, timeline comment, invoice lookup, nursing-task de-duplication). Previous visits live in a new `do_derma/previous_visits.py`, with the assessment preview in `assessment.py`. The chart gets a Reopen header button, a per-row procedure Reopen action and a self-loading `PreviousVisitsPanel.vue`. The sidebar button is a DOM override in `derma_sidebar.js`.

**Tech Stack:** Frappe v16 (Python, `IntegrationTestCase`), healthcare, do_health billing, Vue 3 SFCs built by `bench build --app do_derma`.

**Spec:** `.planning/specs/2026-09-24-derma-chart-reopen-and-history.md`

## Global Constraints

- Branch: `feat/derma-chart-reopen-and-history`.
- Whitelisted endpoints call `_ensure_clinical_access()` first, then reuse `api.py` helpers.
- Reopen right = `cancel` permission on the document. No hardcoded role names.
- Reason is required; whitespace-only counts as blank.
- Reopen encounter sets `docstatus = 0`, `status = "Open"`. Reopen procedure sets `docstatus = 0`, `status = "In Progress"`.
- Procedure Reopen is offered only while the encounter is open (decision recorded 2026-09-24).
- Submitted procedures, invoices, Medication Requests and Service Requests are never touched by encounter reopen.
- A procedure billed on a submitted Sales Invoice cannot be reopened.
- Previous visits page size is 5; section hidden when there are none.
- Python is tab-indented. Keep comments short; no top-of-file comments.
- Lint changed Python files only with `pipx run ruff check <files>` and `pipx run ruff format --check <files>`.
- Tests: `cd /Users/hameed/Developer/bench-v16 && bench --site dermaone.localhost run-tests --module <module>`. `--test` takes only a bare method name; a class name silently runs nothing. Filter output with `| grep -E "^(Ran |OK|FAILED)|^\s*(FAIL|ERROR)\s"`.
- Baseline: the full suite fails exactly five known tests on main (see memory `bench-v16-test-and-lint-quirks`). Judge by diffing that named set.

## Review Focus

- A stale chart tab writes after someone else completed the visit → the server refuses with "Reopen it to make changes". Task 1 tests every gated endpoint.
- Reopen clicked twice, or reopened from two tabs → the second call fails with "not completed", and the timeline gets one comment. Task 4 `test_reopening_twice_is_refused`.
- A reason of only spaces → refused. Task 4 `test_a_blank_reason_is_refused`.
- An assessment text field holding only editor markup (`<p><br></p>`) → the visit does not count as having an assessment. Task 7 `test_editor_markup_alone_is_not_an_assessment`.
- The Visit History drawer re-renders after filtering → exactly one Open Derma Chart button per visit. Task 10 browser step.

---

## File map

| File | Change |
|---|---|
| `do_derma/api.py` | Owner-lookup and after-submit helpers; lock gates on write endpoints; `reopen_derma_session`, `reopen_derma_procedure`, `get_previous_visits`; chart payload `permissions` and `submitted_invoice`; ordered-prescription merge; nursing de-dup in procedure completion |
| `do_derma/reopen.py` (new) | `reopen_document`, `get_submitted_invoices`, `get_nursing_tasks`, `drop_repeated_nursing_tasks` |
| `do_derma/previous_visits.py` (new) | `get_page` |
| `do_derma/assessment.py` | `get_preview` |
| `do_derma/tests/test_encounter_lock.py` (new) | server lock tests |
| `do_derma/tests/test_reopen.py` (new) | reopen tests |
| `do_derma/tests/test_previous_visits.py` (new) | previous visits tests |
| `do_derma/tests/test_encounter_tabs.py` | extract `PrescriptionHelpers` |
| `do_derma/tests/test_api.py` | update one completion test |
| `public/js/chart/DermaChart.vue` | reopen flows, lock gaps, mount previous visits |
| `public/js/chart/components/DermaEncounterHeader.vue` | Reopen button |
| `public/js/chart/components/ProcedurePanel.vue` | row Reopen action, annotate lock |
| `public/js/chart/components/PrescriptionPanel.vue` | ordered rows read-only |
| `public/js/chart/components/assessment/PreviousVisitsPanel.vue` (new) | previous visits UI |
| `public/js/chart/derma_chart.bundle.css` | reopen button, previous visits styles |
| `public/js/derma_sidebar.js` | Visit History button |

---

### Task 1: Server lock for chart writes into a submitted encounter

**Files:**
- Modify: `do_derma/api.py` (`_ensure_encounter_open` at ~3918, and the endpoints listed below)
- Create: `do_derma/tests/test_encounter_lock.py`
- Modify: `do_derma/tests/test_api.py:1204-1210`

**Interfaces:**
- Produces: `api._get_owning_encounter(doctype: str, name: str | None) -> str | None`; `api._ensure_owner_open(doctype: str, name: str | None) -> None`. `_ensure_encounter_open` keeps its signature. Its message becomes "This encounter is completed. Reopen it to make changes." for docstatus 1, and "This encounter is cancelled and can no longer be edited." for 2.

Gate audit (existing gates stay):

| Endpoint | Gate after this task |
|---|---|
| `update_photo_stage`, `delete_photo` | existing `_get_editable_photo_set` → `_ensure_encounter_open` |
| `save_consumables` | existing, in `consumables/marks.py:47` and `consumables/procedures.py:92` |
| `delete_clinical_procedure_entry` | existing draft-only check |
| `create_derma_chart_procedure` | new `_ensure_encounter_open(encounter)` |
| `create_derma_consent` | new `_ensure_encounter_open(encounter)` |
| `carry_forward_marks` | new `_ensure_encounter_open(target_encounter)` |
| `create_photo_set` | new `_ensure_encounter_open(payload.get("encounter"))` after the encounter is resolved |
| `sync_derma_billables` | new `_ensure_encounter_open(encounter_id)` |
| `set_derma_assessment_mode` | new `_ensure_encounter_open(encounter_doc.name)` |
| `set_derma_prescriptions` | `docstatus == 2` check replaced by `_ensure_encounter_open(encounter_doc.name)` |
| `save_chart_mark` | new: `_ensure_owner_open("Derma Chart Mark", name)` when `name`, then `_ensure_encounter_open(payload.get("encounter"))` |
| `discard_chart_marks`, `prune_chart_marks` | new `_ensure_owner_open("Derma Chart Mark", name)` per mark, before delete |
| `save_derma_annotation` | new `_ensure_owner_open(doctype, docname)` |
| `delete_derma_annotation` | new `_ensure_owner_open(doctype, docname)` |
| `save_procedure_variables` | new `_ensure_owner_open("Clinical Procedure", clinical_procedure)` |
| `complete_derma_session` | new: refuse when the encounter is not a draft |
| `set_derma_assessment`, `update_clinical_procedure_fields` | Task 2 (after-submit fields) |
| `set_derma_assessment_all` | existing submit check |
| `documents.*`, `voice.*`, `create_followup_todo` | not gated: they issue new documents or ToDos, not edits to the visit |

- [ ] **Step 1: Write the failing tests**

`do_derma/tests/test_encounter_lock.py`:

```python
from __future__ import annotations

import json

import frappe
from frappe.tests import IntegrationTestCase

import do_derma.api as api
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
			lambda: api.create_photo_set(json.dumps({"patient": self.patient, "encounter": self.encounter.name}))
		)

	def test_consent_is_refused(self):
		self.assertLocked(
			lambda: api.create_derma_consent(json.dumps({"patient": self.patient, "encounter": self.encounter.name}))
		)

	def test_completing_again_is_refused(self):
		self.assertLocked(lambda: api.complete_derma_session(encounter=self.encounter.name, patient=self.patient))

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
```

In `test_api.py`, replace `test_does_not_resubmit_an_already_submitted_encounter` with:

```python
	def test_completing_a_completed_encounter_is_refused(self):
		patient = self._make_patient()
		encounter = self._make_encounter(patient, docstatus=1)

		with self.assertRaises(frappe.ValidationError):
			api.complete_derma_session(encounter=encounter.name, patient=patient)
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd /Users/hameed/Developer/bench-v16 && bench --site dermaone.localhost run-tests --module do_derma.tests.test_encounter_lock | grep -E "^(Ran |OK|FAILED)|^\s*(FAIL|ERROR)\s"`
Expected: FAILED. `test_an_open_encounter_still_takes_writes` passes. The photo-set and marks tests may already partly pass, so check which ones fail.

- [ ] **Step 3: Implement the helpers**

In `api.py`, replace `_ensure_encounter_open` and add the two helpers directly under it:

```python
def _ensure_encounter_open(encounter: str | None) -> None:
	"""A closed encounter is read-only on the chart; a stale tab must not write past it."""
	if not encounter:
		return
	docstatus = cint(frappe.db.get_value("Patient Encounter", encounter, "docstatus"))
	if docstatus == 1:
		frappe.throw(_("This encounter is completed. Reopen it to make changes."), frappe.ValidationError)
	if docstatus == 2:
		frappe.throw(_("This encounter is cancelled and can no longer be edited."), frappe.ValidationError)


def _get_owning_encounter(doctype: str, name: str | None) -> str | None:
	if not name:
		return None
	if doctype == "Patient Encounter":
		return name
	if doctype == "Clinical Procedure":
		field = _get_clinical_procedure_encounter_field()
		return frappe.db.get_value(doctype, name, field) if field else None
	return frappe.db.get_value(doctype, name, "encounter")


def _ensure_owner_open(doctype: str, name: str | None) -> None:
	"""A procedure must be a draft itself as well as sit in an open encounter."""
	if doctype == "Clinical Procedure" and name and cint(frappe.db.get_value(doctype, name, "docstatus")):
		frappe.throw(_("This procedure is completed. Reopen it to make changes."), frappe.ValidationError)
	_ensure_encounter_open(_get_owning_encounter(doctype, name))
```

- [ ] **Step 4: Add the gates**

Insert each call right after the endpoint has the value it needs (after the existing validation that proves the record exists):

```python
# create_derma_chart_procedure, after `encounter_doc = frappe.get_doc("Patient Encounter", encounter)`
	_ensure_encounter_open(encounter)

# create_derma_consent, after the "No encounter found" throw
	_ensure_encounter_open(encounter)

# carry_forward_marks, after the "An active Patient Encounter is required." throw
	_ensure_encounter_open(target_encounter)

# create_photo_set, directly before `doc = frappe.new_doc("Derma Photo Set")`
	_ensure_encounter_open(payload.get("encounter"))

# sync_derma_billables, after `encounter_id = context["encounter_id"]`
	_ensure_encounter_open(encounter_id)

# set_derma_assessment_mode, after the "No encounter found" throw
	_ensure_encounter_open(encounter_doc.name)

# set_derma_prescriptions: replace
#	if cint(encounter_doc.docstatus) == 2:
#		frappe.throw(_("Cancelled encounters cannot be edited."))
# with
	_ensure_encounter_open(encounter_doc.name)

# save_chart_mark, after the encounter/appointment resolution block and before _normalize_position(payload)
	if name:
		_ensure_owner_open("Derma Chart Mark", name)
	_ensure_encounter_open(payload.get("encounter"))

# discard_chart_marks and prune_chart_marks, inside the loop after `mark_doc = frappe.get_doc(...)`
		_ensure_owner_open("Derma Chart Mark", name)

# save_derma_annotation, after the "Encounter is required." throw
	_ensure_owner_open(doctype, docname)

# delete_derma_annotation, after the "{0} not found." check
	_ensure_owner_open(doctype, docname)

# save_procedure_variables, after the "Clinical Procedure not found." check
	_ensure_owner_open("Clinical Procedure", clinical_procedure)
```

In `complete_derma_session`, directly after the `if not encounter_id: frappe.throw(...)`:

```python
	_ensure_encounter_open(encounter_id)
```

Then drop the now-dead branch at the end: `if encounter_doc.docstatus == 0:` stays as it is (still true for a draft). Leave `"encounter_submitted"` in the response.

- [ ] **Step 5: Run the lock tests and the modules they touch**

Run: `cd /Users/hameed/Developer/bench-v16 && for m in test_encounter_lock test_api test_encounter_tabs test_consumables; do bench --site dermaone.localhost run-tests --module do_derma.tests.$m | grep -E "^(Ran |OK|FAILED)|^\s*(FAIL|ERROR)\s"; done`
Expected: `test_encounter_lock` OK. The other modules show no failures beyond the five baseline names. If an existing test wrote into a submitted encounter on purpose, read it: if it asserts that behaviour, it now contradicts the spec, so update it to expect the refusal and say so in the commit body.

- [ ] **Step 6: Lint and commit**

```bash
cd /Users/hameed/Developer/bench-v16/apps/do_derma
pipx run ruff check do_derma/tests/test_encounter_lock.py && pipx run ruff format --check do_derma/tests/test_encounter_lock.py
git add do_derma/api.py do_derma/tests/test_encounter_lock.py do_derma/tests/test_api.py
git commit -m "fix(chart): refuse chart writes into a completed encounter"
```

---

### Task 2: After-submit edits accept only allow-on-submit fields

**Files:**
- Modify: `do_derma/api.py` (`set_derma_assessment` ~3231, `update_clinical_procedure_fields` ~3560, new helper beside `_ensure_owner_open`)
- Test: `do_derma/tests/test_encounter_lock.py`

**Interfaces:**
- Consumes: `_ensure_encounter_open`, `_get_owning_encounter` (Task 1).
- Produces: `api._ensure_changes_allowed_on_submit(doc, before: dict, after: dict) -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `test_encounter_lock.py`:

```python
from do_derma import assessment


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
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd /Users/hameed/Developer/bench-v16 && bench --site dermaone.localhost run-tests --module do_derma.tests.test_encounter_lock | grep -E "^(Ran |OK|FAILED)|^\s*(FAIL|ERROR)\s"`
Expected: FAILED on the two "is refused" tests.

- [ ] **Step 3: Implement**

Helper beside `_ensure_owner_open`:

```python
def _ensure_changes_allowed_on_submit(doc, before: dict[str, Any], after: dict[str, Any]) -> None:
	"""A submitted document may change only the fields Frappe allows on submit."""
	if not cint(doc.docstatus):
		return
	locked = [
		fieldname
		for fieldname, value in after.items()
		if value != before.get(fieldname)
		and doc.meta.has_field(fieldname)
		and not doc.meta.get_field(fieldname).allow_on_submit
	]
	if locked:
		labels = ", ".join(_(doc.meta.get_label(fieldname)) for fieldname in locked)
		frappe.throw(
			_("{0} is completed, so {1} cannot change. Reopen it to make changes.").format(_(doc.doctype), labels),
			frappe.ValidationError,
		)
```

`set_derma_assessment`: wrap the apply:

```python
	layout = assessment.get_layout(assessment.normalize_mode(mode) or assessment.get_assessment_mode(encounter_doc))
	before = assessment.serialize_values(encounter_doc, layout)
	assessment.apply_assessment(encounter_doc, values, mode=mode)
	_ensure_changes_allowed_on_submit(encounter_doc, before, assessment.serialize_values(encounter_doc, layout))
```

`update_clinical_procedure_fields`: capture `before = {fieldname: doc.get(fieldname) for fieldname in values}` before the loop. After the loop:

```python
	if cint(doc.docstatus):
		_ensure_changes_allowed_on_submit(doc, before, {fieldname: doc.get(fieldname) for fieldname in values})
	else:
		_ensure_encounter_open(_get_owning_encounter("Clinical Procedure", doc.name))
```

- [ ] **Step 4: Run tests**

Run: the Task 1 Step 5 loop.
Expected: `test_encounter_lock` OK (skips allowed). No new names in the other modules.

- [ ] **Step 5: Commit**

```bash
git add do_derma/api.py do_derma/tests/test_encounter_lock.py
git commit -m "fix(chart): accept only allow-on-submit changes on completed records"
```

---

### Task 3: Prescriptions already ordered stay as they are

**Files:**
- Modify: `do_derma/api.py` (`set_derma_prescriptions`, new `_merge_ordered_prescriptions` beside `_validate_prescription_rows` ~2227)
- Modify: `do_derma/tests/test_encounter_tabs.py` (extract helpers)
- Modify: `public/js/chart/components/PrescriptionPanel.vue`
- Test: `do_derma/tests/test_encounter_tabs.py`

**Interfaces:**
- Produces: `api._merge_ordered_prescriptions(existing: list[dict], incoming: list[dict]) -> list[dict]`; `PrescriptionHelpers` test mixin with `_row(**extra)`.

- [ ] **Step 1: Extract the helpers**

In `test_encounter_tabs.py`, move `_row`, `_get_or_create_medication` and `_get_or_create_prescription_duration` out of `TestDermaPrescriptions` into:

```python
class PrescriptionHelpers(DermaTestHelpers):
	"""Rows the Rx tab would send, with healthcare's two mandatory fields filled."""
```

and declare `class TestDermaPrescriptions(PrescriptionHelpers, IntegrationTestCase):`.

- [ ] **Step 2: Write the failing test**

Append to `test_encounter_tabs.py`:

```python
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

	def test_an_unknown_request_is_refused(self):
		encounter = self._ordered_encounter()
		with self.assertRaises(frappe.ValidationError):
			api.set_derma_prescriptions(
				payload=json.dumps([self._row(drug_name="Forged", medication_request="MR-OTHER")]),
				encounter=encounter.name,
			)
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd /Users/hameed/Developer/bench-v16 && bench --site dermaone.localhost run-tests --module do_derma.tests.test_encounter_tabs | grep -E "^(Ran |OK|FAILED)|^\s*(FAIL|ERROR)\s"`
Expected: FAILED on both new tests.

- [ ] **Step 4: Implement the merge**

```python
def _merge_ordered_prescriptions(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
	"""Rows already ordered as a Medication Request are kept as stored, ahead of the new ones."""
	ordered = [row for row in existing if row.get("medication_request")]
	known = {row["medication_request"] for row in ordered}
	fresh = []
	for row in incoming:
		request = row.get("medication_request")
		if request and request not in known:
			frappe.throw(_("Medication Request {0} does not belong to this encounter.").format(request), frappe.ValidationError)
		if not request:
			fresh.append(row)
	return [*ordered, *fresh]
```

In `set_derma_prescriptions`, after `_validate_prescription_rows(prescriptions)`:

```python
	prescriptions = _merge_ordered_prescriptions(_drug_prescription_rows(encounter_doc), prescriptions)
```

- [ ] **Step 5: Run tests**

Same command as Step 3. Expected: OK apart from baseline names.

- [ ] **Step 6: Panel shows ordered rows read-only**

In `PrescriptionPanel.vue`:

```js
const orderedRows = computed(() => (props.rows || []).filter((row) => row.medication_request))
```

`renderTable()` feeds the grid `normalizeRows((props.rows || []).filter((row) => !row.medication_request))` instead of all rows. The value the grid's data is read from keeps the same variable name it has today. Add above the grid host in the template:

```vue
<div v-if="orderedRows.length" class="prescription-ordered" data-test="prescription-ordered">
  <p class="status-note">{{ __("Already ordered. These rows cannot change.") }}</p>
  <ul>
    <li v-for="row in orderedRows" :key="row.medication_request">
      <b>{{ row.drug_name || row.medication || row.drug_code }}</b>
      <small>{{ [row.dosage, row.period].filter(Boolean).join(" · ") }} · {{ row.medication_request }}</small>
    </li>
  </ul>
</div>
```

The server keeps ordered rows, so the save payload stays grid rows only.

- [ ] **Step 7: Commit**

```bash
git add do_derma/api.py do_derma/tests/test_encounter_tabs.py do_derma/public/js/chart/components/PrescriptionPanel.vue
git commit -m "fix(prescriptions): keep rows already ordered as a Medication Request"
```

---

### Task 4: Reopen a completed encounter

**Files:**
- Create: `do_derma/reopen.py`
- Modify: `do_derma/api.py` (import `reopen`; `reopen_derma_session` after `complete_derma_session`)
- Create: `do_derma/tests/test_reopen.py`

**Interfaces:**
- Produces: `reopen.reopen_document(doc, reason: str | None, status: str) -> None`; whitelisted `api.reopen_derma_session(encounter: str, reason: str | None = None) -> dict` returning `{"encounter": name, "docstatus": 0}`.

- [ ] **Step 1: Write the failing tests**

`do_derma/tests/test_reopen.py`:

```python
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
		values = frappe.db.get_value("Patient Encounter", self.encounter.name, ["docstatus", "status"], as_dict=True)
		self.assertEqual((values.docstatus, values.status), (0, "Open"))

	def test_writes_the_reason_on_the_timeline(self):
		self._reopen("Added a missed finding")
		comments = frappe.get_all(
			"Comment",
			filters={"reference_doctype": "Patient Encounter", "reference_name": self.encounter.name, "comment_type": "Comment"},
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
		frappe.set_user(self._make_user_with_role("Nursing User"))
		with self.assertRaises(frappe.PermissionError):
			self._reopen()

	def test_submitted_procedures_stay_submitted(self):
		encounter = self._make_encounter(self.patient)
		procedure = self._make_clinical_procedure(self.patient)
		procedure.db_set(api._get_clinical_procedure_encounter_field(), encounter.name)
		api.complete_derma_session(encounter=encounter.name, patient=self.patient)

		api.reopen_derma_session(encounter.name, "Fix the note")

		self.assertEqual(frappe.db.get_value("Clinical Procedure", procedure.name, "docstatus"), 1)

	def test_completing_again_orders_nothing_twice(self):
		encounter = self._make_encounter(self.patient)
		api.set_derma_prescriptions(payload=json.dumps([self._row(drug_name="First")]), encounter=encounter.name)
		api.complete_derma_session(encounter=encounter.name, patient=self.patient)
		api.reopen_derma_session(encounter.name, "Add a second drug")
		api.set_derma_prescriptions(payload=json.dumps([self._row(drug_name="Second")]), encounter=encounter.name)

		api.complete_derma_session(encounter=encounter.name, patient=self.patient)

		self.assertEqual(frappe.db.count("Medication Request", {"order_group": encounter.name}), 2)
```

If `_make_user_with_role("Nursing User")` has cancel on Patient Encounter on this site, pick a role that has clinical access but no cancel. List candidates with `bench --site dermaone.localhost console` → `api.CLINICAL_ACCESS_ROLES`.

- [ ] **Step 2: Run to verify they fail**

Run: `cd /Users/hameed/Developer/bench-v16 && bench --site dermaone.localhost run-tests --module do_derma.tests.test_reopen | grep -E "^(Ran |OK|FAILED)|^\s*(FAIL|ERROR)\s"`
Expected: ERROR, `module 'do_derma.api' has no attribute 'reopen_derma_session'`.

- [ ] **Step 3: Implement `reopen.py`**

```python
from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, get_fullname


def reopen_document(doc, reason: str | None, status: str) -> None:
	"""Put a submitted document and its child rows back to draft, and say why on its timeline."""
	if not doc.has_permission("cancel"):
		frappe.throw(_("You are not permitted to reopen {0} {1}.").format(_(doc.doctype), doc.name), frappe.PermissionError)
	reason = (reason or "").strip()
	if not reason:
		frappe.throw(_("A reason is required to reopen {0}.").format(doc.name), frappe.ValidationError)
	if cint(doc.docstatus) != 1:
		frappe.throw(_("{0} {1} is not completed, so it cannot be reopened.").format(_(doc.doctype), doc.name), frappe.ValidationError)

	for table in doc.meta.get_table_fields():
		child = frappe.qb.DocType(table.options)
		(
			frappe.qb.update(child)
			.set(child.docstatus, 0)
			.where((child.parent == doc.name) & (child.parenttype == doc.doctype))
			.run()
		)
	doc.db_set({"docstatus": 0, "status": status}, update_modified=True)
	doc.add_comment("Comment", _("Reopened by {0}: {1}").format(get_fullname(), reason))
```

- [ ] **Step 4: Implement the endpoint**

`api.py`: add `from do_derma import reopen` with the other module imports at the top, then after `complete_derma_session`:

```python
@frappe.whitelist()
def reopen_derma_session(encounter: str, reason: str | None = None):
	"""Put a completed visit back to draft. Procedures, invoices and orders stay submitted."""
	_ensure_clinical_access()
	doc = frappe.get_doc("Patient Encounter", encounter)
	reopen.reopen_document(doc, reason, status="Open")
	return {"encounter": doc.name, "docstatus": 0}
```

- [ ] **Step 5: Run tests**

Same command as Step 2. Expected: OK. If `test_completing_again_orders_nothing_twice` counts 3, the merge from Task 3 is not in the path. Check that the second `set_derma_prescriptions` returned the ordered row first.

- [ ] **Step 6: Commit**

```bash
pipx run ruff check do_derma/reopen.py do_derma/tests/test_reopen.py && pipx run ruff format --check do_derma/reopen.py do_derma/tests/test_reopen.py
git add do_derma/reopen.py do_derma/api.py do_derma/tests/test_reopen.py
git commit -m "feat(chart): reopen a completed encounter with a reason"
```

---

### Task 5: Reopen one procedure inside a reopened encounter

**Files:**
- Modify: `do_derma/reopen.py`
- Modify: `do_derma/api.py` (`reopen_derma_procedure`; `_complete_derma_procedures_for_session` ~3628)
- Test: `do_derma/tests/test_reopen.py`

**Interfaces:**
- Consumes: `reopen.reopen_document`, `api._ensure_encounter_open`, `api._get_owning_encounter`.
- Produces: `reopen.get_submitted_invoices(procedures: list[str]) -> dict[str, str]`; `reopen.get_nursing_tasks(procedure: str) -> list[dict]`; `reopen.drop_repeated_nursing_tasks(procedure: str, before: list[dict]) -> None`; whitelisted `api.reopen_derma_procedure(procedure: str, reason: str | None = None) -> dict` returning `{"procedure": name, "docstatus": 0}`.

- [ ] **Step 1: Write the failing tests**

Append to `test_reopen.py`:

```python
from unittest.mock import patch

from do_derma import reopen


class TestReopenProcedure(PrescriptionHelpers, IntegrationTestCase):
	def setUp(self):
		self.patient = self._make_patient()
		self.encounter = self._make_encounter(self.patient)
		self.procedure = self._make_clinical_procedure(self.patient)
		self.procedure.db_set(api._get_clinical_procedure_encounter_field(), self.encounter.name)
		api.complete_derma_session(encounter=self.encounter.name, patient=self.patient)

	def test_is_refused_while_the_encounter_is_completed(self):
		with self.assertRaises(frappe.ValidationError):
			api.reopen_derma_procedure(self.procedure.name, "Wrong dose")

	def test_puts_the_procedure_back_to_draft(self):
		api.reopen_derma_session(self.encounter.name, "Fix a procedure")
		api.reopen_derma_procedure(self.procedure.name, "Wrong dose")
		values = frappe.db.get_value("Clinical Procedure", self.procedure.name, ["docstatus", "status"], as_dict=True)
		self.assertEqual((values.docstatus, values.status), (0, "In Progress"))

	def test_an_invoiced_procedure_is_refused(self):
		api.reopen_derma_session(self.encounter.name, "Fix a procedure")
		with patch.object(reopen, "get_submitted_invoices", return_value={self.procedure.name: "ACC-SINV-TEST"}):
			with self.assertRaises(frappe.ValidationError) as caught:
				api.reopen_derma_procedure(self.procedure.name, "Wrong dose")
		self.assertIn("ACC-SINV-TEST", str(caught.exception))

	def test_invoice_lookup_runs_against_the_real_schema(self):
		self.assertEqual(reopen.get_submitted_invoices([self.procedure.name]), {})

	def test_completing_again_resubmits_it(self):
		api.reopen_derma_session(self.encounter.name, "Fix a procedure")
		api.reopen_derma_procedure(self.procedure.name, "Wrong dose")
		api.complete_derma_session(encounter=self.encounter.name, patient=self.patient)
		self.assertEqual(frappe.db.get_value("Clinical Procedure", self.procedure.name, "docstatus"), 1)


class TestResubmitSideEffects(PrescriptionHelpers, IntegrationTestCase):
	"""Submitting a reopened procedure again must not bill or request its checklist twice."""

	def _billable_template(self, pre_op_checklist=None):
		token = frappe.generate_hash(length=8)
		doc = frappe.get_doc(
			{
				"doctype": "Clinical Procedure Template",
				"template": f"DermaBill{token}",
				"item_code": f"DermaBill{token}",
				"description": "Resubmit fixture.",
				"item_group": frappe.db.get_value("Item Group", {}, "name"),
				"pre_op_nursing_checklist_template": pre_op_checklist,
			}
		)
		doc.set("is_billable", 1)
		return doc.insert(ignore_permissions=True).name

	def _checklist(self):
		token = frappe.generate_hash(length=8)
		activity = frappe.get_doc({"doctype": "Healthcare Activity", "activity": f"Derma prep {token}"}).insert(
			ignore_permissions=True
		)
		return (
			frappe.get_doc(
				{"doctype": "Nursing Checklist Template", "title": f"Derma {token}", "tasks": [{"activity": activity.name}]}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def _round_trip(self, template):
		patient = self._make_patient()
		appointment = frappe.get_doc(
			{
				"doctype": "Patient Appointment",
				"patient": patient,
				"appointment_type": self._get_or_create_appointment_type(),
				"practitioner": self._get_or_create_practitioner(),
				"appointment_date": frappe.utils.nowdate(),
				"appointment_time": frappe.utils.nowtime(),
				"company": frappe.db.get_value("Company", {}, "name"),
			}
		).insert(ignore_permissions=True)
		encounter = self._make_encounter(patient)
		encounter.db_set("appointment", appointment.name)
		procedure = self._make_clinical_procedure(patient)
		procedure.db_set({"procedure_template": template, "appointment": appointment.name})
		procedure.db_set(api._get_clinical_procedure_encounter_field(), encounter.name)
		with patch("do_health.api.methods.create_invoice_for_visit", return_value=None):
			api.complete_derma_session(encounter=encounter.name, patient=patient)
			api.reopen_derma_session(encounter.name, "Fix")
			api.reopen_derma_procedure(procedure.name, "Fix")
			api.complete_derma_session(encounter=encounter.name, patient=patient)
		return procedure.name

	def test_billing_is_not_added_twice(self):
		if not frappe.db.exists("DocType", "Appointment Billing Items"):
			self.skipTest("do_health billing is not installed.")
		procedure = self._round_trip(self._billable_template())
		self.assertEqual(
			frappe.db.count("Appointment Billing Items", {"clinical_procedure": procedure, "parenttype": "Patient Appointment"}),
			1,
		)

	def test_nursing_tasks_are_not_requested_twice(self):
		procedure = self._round_trip(self._billable_template(pre_op_checklist=self._checklist()))
		self.assertEqual(
			frappe.db.count("Nursing Task", {"reference_doctype": "Clinical Procedure", "reference_name": procedure}),
			1,
		)
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd /Users/hameed/Developer/bench-v16 && bench --site dermaone.localhost run-tests --module do_derma.tests.test_reopen | grep -E "^(Ran |OK|FAILED)|^\s*(FAIL|ERROR)\s"`
Expected: ERRORs, `no attribute 'reopen_derma_procedure'` / `'get_submitted_invoices'`.

- [ ] **Step 3: Implement in `reopen.py`**

```python
def get_submitted_invoices(procedures: list[str]) -> dict[str, str]:
	"""The submitted Sales Invoice billing each procedure, keyed by procedure."""
	if not procedures:
		return {}
	invoices = {
		row.reference_dn: row.parent
		for row in frappe.get_all(
			"Sales Invoice Item",
			filters={"reference_dt": "Clinical Procedure", "reference_dn": ["in", procedures], "docstatus": 1},
			fields=["reference_dn", "parent"],
		)
	}
	if frappe.db.exists("DocType", "Billing Charge"):
		for row in frappe.get_all(
			"Billing Charge",
			filters={"clinical_procedure": ["in", procedures]},
			fields=["clinical_procedure", "lite_patient_invoice", "lite_insurance_invoice"],
		):
			for invoice in (row.lite_patient_invoice, row.lite_insurance_invoice):
				if invoice and cint(frappe.db.get_value("Sales Invoice", invoice, "docstatus")) == 1:
					invoices.setdefault(row.clinical_procedure, invoice)
	return invoices


def get_nursing_tasks(procedure: str) -> list[dict]:
	if not frappe.db.exists("DocType", "Nursing Task"):
		return []
	return frappe.get_all(
		"Nursing Task",
		filters={"reference_doctype": "Clinical Procedure", "reference_name": procedure},
		fields=["name", "activity"],
	)


def drop_repeated_nursing_tasks(procedure: str, before: list[dict]) -> None:
	"""healthcare requests the pre-op checklist on every submit; a reopened procedure has it already."""
	requested = {row.activity for row in before}
	existing = {row.name for row in before}
	for row in get_nursing_tasks(procedure):
		if row.name not in existing and row.activity in requested:
			frappe.delete_doc("Nursing Task", row.name, ignore_permissions=True)
```

- [ ] **Step 4: Implement the endpoint and the completion hook**

`api.py`, after `reopen_derma_session`:

```python
@frappe.whitelist()
def reopen_derma_procedure(procedure: str, reason: str | None = None):
	"""Put one completed procedure back to draft inside a reopened visit."""
	_ensure_clinical_access()
	doc = frappe.get_doc("Clinical Procedure", procedure)
	_ensure_encounter_open(_get_owning_encounter("Clinical Procedure", doc.name))
	invoice = reopen.get_submitted_invoices([doc.name]).get(doc.name)
	if invoice:
		frappe.throw(
			_("Procedure {0} is billed on submitted invoice {1}. Cancel or return the invoice first.").format(doc.name, invoice),
			frappe.ValidationError,
		)
	reopen.reopen_document(doc, reason, status="In Progress")
	return {"procedure": doc.name, "docstatus": 0}
```

In `_complete_derma_procedures_for_session`, wrap the submit:

```python
			before = reopen.get_nursing_tasks(name)
			doc.submit()
			reopen.drop_repeated_nursing_tasks(name, before)
```

- [ ] **Step 5: Run tests**

Same command as Step 2. Expected: OK. If `test_billing_is_not_added_twice` counts 2, do_health's `sync_clinical_procedure_billing` is not idempotent for this shape. Stop and report: the fix belongs in do_health, not here.

- [ ] **Step 6: Commit**

```bash
pipx run ruff check do_derma/reopen.py do_derma/tests/test_reopen.py && pipx run ruff format --check do_derma/reopen.py do_derma/tests/test_reopen.py
git add do_derma/reopen.py do_derma/api.py do_derma/tests/test_reopen.py
git commit -m "feat(chart): reopen a single procedure inside a reopened encounter"
```

---

### Task 6: Chart payload says who may reopen and what is invoiced

**Files:**
- Modify: `do_derma/api.py` (`get_patient_derma_chart` ~2473)
- Test: `do_derma/tests/test_reopen.py`

**Interfaces:**
- Consumes: `reopen.get_submitted_invoices`.
- Produces: chart payload keys `permissions: {"can_reopen_encounter": bool, "can_reopen_procedure": bool}` and `submitted_invoice: str` on every `procedures` row.

- [ ] **Step 1: Write the failing test**

```python
class TestChartReopenPayload(PrescriptionHelpers, IntegrationTestCase):
	def test_reports_reopen_rights_and_invoices(self):
		patient = self._make_patient()
		encounter = self._make_encounter(patient)
		procedure = self._make_clinical_procedure(patient)
		procedure.db_set(api._get_clinical_procedure_encounter_field(), encounter.name)

		with patch.object(reopen, "get_submitted_invoices", return_value={procedure.name: "ACC-SINV-TEST"}):
			chart = api.get_patient_derma_chart(patient_id=patient, encounter=encounter.name)

		self.assertEqual(chart["permissions"], {"can_reopen_encounter": True, "can_reopen_procedure": True})
		rows = [row for row in chart["procedures"] if (row.get("clinical_procedure") or row.get("name")) == procedure.name]
		self.assertEqual(rows[0]["submitted_invoice"], "ACC-SINV-TEST")
```

- [ ] **Step 2: Run to verify it fails.** Same module command. Expected: `KeyError: 'permissions'`.

- [ ] **Step 3: Implement**

In `get_patient_derma_chart`, directly after `procedures` is computed:

```python
	invoices = reopen.get_submitted_invoices(
		[row.get("clinical_procedure") or row.get("name") for row in procedures if row.get("name")]
	)
	for row in procedures:
		row["submitted_invoice"] = invoices.get(row.get("clinical_procedure") or row.get("name"), "")
```

and in its returned dict:

```python
		"permissions": {
			"can_reopen_encounter": bool(encounter_id)
			and frappe.has_permission("Patient Encounter", "cancel", doc=encounter_id),
			"can_reopen_procedure": frappe.has_permission("Clinical Procedure", "cancel"),
		},
```

- [ ] **Step 4: Run tests.** Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add do_derma/api.py do_derma/tests/test_reopen.py
git commit -m "feat(chart): send reopen rights and procedure invoices with the chart"
```

---

### Task 7: Previous visits endpoint

**Files:**
- Modify: `do_derma/assessment.py` (`get_preview` after `read_assessment`)
- Create: `do_derma/previous_visits.py`
- Modify: `do_derma/api.py` (import; `_load_visit_drawings`; `get_previous_visits`)
- Create: `do_derma/tests/test_previous_visits.py`

**Interfaces:**
- Produces: `assessment.get_preview(encounter_doc) -> list[dict]` of `{"label": str, "value": str}`; `previous_visits.get_page(patient, current_encounter, start, page_length, load_drawings) -> {"visits", "has_more", "next_start"}`; whitelisted `api.get_previous_visits(patient, current_encounter=None, start=0, page_length=5)`. Each visit is `{"encounter", "encounter_date", "practitioner_name", "drawings", "assessment"}`.

- [ ] **Step 1: Write the failing tests**

`do_derma/tests/test_previous_visits.py`:

```python
from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

import do_derma.api as api
from do_derma import assessment
from do_derma.tests.test_api import PIXEL_PNG, DermaTestHelpers

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
		api.save_derma_annotation({"doctype": "Patient Encounter", "docname": encounter.name, "file_data": PIXEL_PNG})
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

		self.assertEqual(self._page(current_encounter=current.name)["visits"], [])

	def test_preview_is_label_and_plain_text(self):
		self._with_assessment("<p>Mild erythema.</p>")
		preview = self._page()["visits"][0]["assessment"]
		self.assertIn("Mild erythema.", [row["value"] for row in preview])

	def test_editor_markup_alone_is_not_an_assessment(self):
		self._with_assessment("<p><br></p>")
		self.assertEqual(self._page()["visits"], [])

	def test_pages_by_five_and_says_when_more_remain(self):
		for _ in range(6):
			self._with_assessment()
		self._make_encounter(self.patient)

		first = self._page()
		second = self._page(start=first["next_start"])

		self.assertEqual((len(first["visits"]), first["has_more"]), (5, True))
		self.assertEqual((len(second["visits"]), second["has_more"]), (1, False))
		self.assertFalse({v["encounter"] for v in first["visits"]} & {v["encounter"] for v in second["visits"]})

	def test_is_gated(self):
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user(self._make_limited_user())
		with self.assertRaises(frappe.PermissionError):
			self._page()
```

- [ ] **Step 2: Run to verify they fail.** Run: `cd /Users/hameed/Developer/bench-v16 && bench --site dermaone.localhost run-tests --module do_derma.tests.test_previous_visits | grep -E "^(Ran |OK|FAILED)|^\s*(FAIL|ERROR)\s"`. Expected: ERROR, no attribute `get_previous_visits`.

- [ ] **Step 3: `assessment.get_preview`**

Add `strip_html` to the existing `frappe.utils` import in `assessment.py`, then after `read_assessment`:

```python
def get_preview(encounter_doc) -> list[dict[str, str]]:
	"""The documented format's filled fields as label and plain text."""
	layout = get_layout(get_assessment_mode(encounter_doc))
	values = serialize_values(encounter_doc, layout)
	preview = []
	for row in layout:
		text = _preview_text(row, values.get(row.get("fieldname")))
		if text:
			preview.append({"label": _(row.get("label") or row.get("fieldname")), "value": text})
	return preview


def _preview_text(row: dict[str, Any], value: Any) -> str:
	if row.get("fieldtype") in TABLE_FIELD_TYPES:
		return _("{0} row(s)").format(len(value)) if value else ""
	if row.get("fieldtype") == "Check":
		return _("Yes") if cint(value) else ""
	return strip_html(cstr(value or "")).strip()
```

If `_` is not already imported in `assessment.py`, add `from frappe import _`.

- [ ] **Step 4: `previous_visits.py`**

```python
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import frappe

from do_derma import assessment

BATCH_SIZE = 20


def get_page(
	patient: str,
	current_encounter: str | None,
	start: int,
	page_length: int,
	load_drawings: Callable[[str], list[dict[str, Any]]],
) -> dict[str, Any]:
	"""Earlier visits with a drawing or an assessment, newest first.

	`next_start` is an encounter offset, not a visit count: visits with neither are skipped.
	"""
	visits: list[dict[str, Any]] = []
	offset = start
	while True:
		batch = _get_encounters(patient, current_encounter, offset)
		if not batch:
			return {"visits": visits, "has_more": False, "next_start": offset}
		for row in batch:
			visit = _build_visit(row, load_drawings)
			if visit and len(visits) == page_length:
				return {"visits": visits, "has_more": True, "next_start": offset}
			if visit:
				visits.append(visit)
			offset += 1


def _get_encounters(patient: str, current_encounter: str | None, start: int) -> list[dict[str, Any]]:
	filters: dict[str, Any] = {"patient": patient, "docstatus": ["<", 2]}
	if current_encounter:
		filters["name"] = ["!=", current_encounter]
	return frappe.get_all(
		"Patient Encounter",
		filters=filters,
		fields=["name", "encounter_date", "practitioner", "practitioner_name"],
		order_by="encounter_date desc, creation desc",
		limit_start=start,
		limit_page_length=BATCH_SIZE,
	)


def _build_visit(row, load_drawings) -> dict[str, Any] | None:
	drawings = load_drawings(row.name)
	preview = assessment.get_preview(frappe.get_doc("Patient Encounter", row.name))
	if not drawings and not preview:
		return None
	return {
		"encounter": row.name,
		"encounter_date": row.encounter_date,
		"practitioner_name": row.practitioner_name or row.practitioner or "",
		"drawings": drawings,
		"assessment": preview,
	}
```

- [ ] **Step 5: Endpoint**

`api.py`: add `from do_derma import previous_visits` with the other imports, then near `get_patient_timeline`:

```python
def _load_visit_drawings(encounter: str) -> list[dict[str, Any]]:
	"""An encounter's drawings and its procedures', without the scene JSON."""
	field = _get_clinical_procedure_encounter_field()
	procedures = frappe.get_all("Clinical Procedure", filters={field: encounter}, pluck="name") if field else []
	parents = [("Patient Encounter", encounter), *(("Clinical Procedure", name) for name in procedures)]
	return _load_annotations_for_parents(parents, include_scene=False)


@frappe.whitelist()
def get_previous_visits(patient: str, current_encounter: str | None = None, start: int = 0, page_length: int = 5):
	_ensure_clinical_access()
	if not patient:
		frappe.throw(_("Patient is required."))
	return previous_visits.get_page(
		patient, current_encounter, cint(start), min(cint(page_length) or 5, 20), _load_visit_drawings
	)
```

- [ ] **Step 6: Run tests.** Same command as Step 2. Expected: OK (skips allowed only when the layout has no text field).

- [ ] **Step 7: Commit**

```bash
pipx run ruff check do_derma/previous_visits.py do_derma/assessment.py do_derma/tests/test_previous_visits.py
pipx run ruff format --check do_derma/previous_visits.py do_derma/assessment.py do_derma/tests/test_previous_visits.py
git add do_derma/previous_visits.py do_derma/assessment.py do_derma/api.py do_derma/tests/test_previous_visits.py
git commit -m "feat(assessment): list earlier visits with drawings or an assessment"
```

---

### Task 8: Chart UI: reopen and lock gaps

**Files:**
- Modify: `public/js/chart/components/DermaEncounterHeader.vue`
- Modify: `public/js/chart/components/ProcedurePanel.vue`
- Modify: `public/js/chart/DermaChart.vue`
- Modify: `public/js/chart/derma_chart.bundle.css`

**Interfaces:**
- Consumes: `reopen_derma_session`, `reopen_derma_procedure`, `data.permissions`, row `submitted_invoice`.
- Produces: header props `canReopen: Boolean`, `reopening: Boolean`, emit `reopen`. ProcedurePanel prop `canReopen: Boolean`, emit `reopen-procedure(row)`.

- [ ] **Step 1: Header**

Replace the `encounter-actions` block:

```vue
    <div class="encounter-actions">
      <button
        v-if="!isCompleted"
        type="button"
        class="primary"
        data-test="complete-session"
        :disabled="!hasSessionContext || completing || pending"
        @click="$emit('complete')"
      >
        {{ completing ? __("Completing...") : __("Complete Encounter") }}
      </button>
      <button
        v-else-if="canReopen"
        type="button"
        class="reopen"
        data-test="reopen-session"
        :disabled="reopening"
        @click="$emit('reopen')"
      >
        {{ reopening ? __("Reopening...") : __("Reopen Encounter") }}
      </button>
    </div>
```

Script: add props `canReopen: { type: Boolean, default: false }` and `reopening: { type: Boolean, default: false }`, make the emits `["complete", "reopen", "alert-action"]`, and add `const isCompleted = computed(() => Number(props.encounter.docstatus) === 1)`.

- [ ] **Step 2: CSS**

Append to `derma_chart.bundle.css` next to the first `.encounter-actions` rule (~140):

```css
.encounter-actions button.reopen {
  background: #b45309;
  border: 1px solid #b45309;
  color: var(--derma-white);
}

.encounter-actions button.reopen:hover:not(:disabled) {
  background: #92400e;
  border-color: #92400e;
}
```

Match padding and radius to however `.encounter-actions button.primary` is styled. Check the button rules the header already uses and copy their box model.

- [ ] **Step 3: ProcedurePanel**

Add prop `canReopen: { type: Boolean, default: false }` and `"reopen-procedure"` to `defineEmits`. On the annotate button add `:disabled="readOnly || Number(row.docstatus || 0) > 0"`. Before the delete button in `td.row-actions`:

```vue
                <button
                  v-if="canReopen && !readOnly && Number(row.docstatus || 0) === 1"
                  class="icon-btn"
                  type="button"
                  data-test="procedure-reopen"
                  :disabled="Boolean(row.submitted_invoice)"
                  :title="row.submitted_invoice ? __('Billed on {0}. Cancel or return the invoice before reopening.').replace('{0}', row.submitted_invoice) : __('Reopen procedure')"
                  :aria-label="__('Reopen procedure')"
                  @click="$emit('reopen-procedure', row)"
                >
                  <i class="fa-solid fa-lock-open"></i>
                </button>
```

- [ ] **Step 4: DermaChart wiring**

Script, next to `completeSession`:

```js
const reopeningSession = ref(false)
const reopenPermissions = computed(() => data.value.permissions || {})

/** The typed reason, or null when the clinician backed out. */
function askReopenReason(title) {
  return new Promise((resolve) => {
    let answered = false
    const dialog = new frappe.ui.Dialog({
      title,
      fields: [{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason"), reqd: 1 }],
      primary_action_label: __("Reopen"),
      primary_action({ reason }) {
        answered = true
        dialog.hide()
        resolve((reason || "").trim() || null)
      },
    })
    dialog.onhide = () => {
      if (!answered) resolve(null)
    }
    dialog.show()
  })
}

async function reopenRecord(method, args, title) {
  if (reopeningSession.value) return
  reopeningSession.value = true
  try {
    const reason = await askReopenReason(title)
    if (!reason) return
    await frappe.call({ method, args: { ...args, reason } })
    frappe.show_alert({ message: __("Reopened."), indicator: "orange" })
    await refresh()
  } catch (err) {
    frappe.msgprint({ title, message: serverErrorText(err, __("Unable to reopen.")), indicator: "red" })
  } finally {
    reopeningSession.value = false
  }
}

function reopenSession() {
  if (!encounter.value.name) return
  reopenRecord("do_derma.api.reopen_derma_session", { encounter: encounter.value.name }, __("Reopen Encounter"))
}

function reopenProcedure(row) {
  const procedure = row?.clinical_procedure || row?.name
  if (!procedure) return
  reopenRecord("do_derma.api.reopen_derma_procedure", { procedure }, __("Reopen Procedure"))
}
```

If `refresh()` is not async, `await` on it is harmless. Keep it.

Template:
- `DermaEncounterHeader`: add `:can-reopen="Boolean(reopenPermissions.can_reopen_encounter)"`, `:reopening="reopeningSession"`, `@reopen="reopenSession"`.
- `ProcedurePanel`: add `:can-reopen="Boolean(reopenPermissions.can_reopen_procedure)"`, `@reopen-procedure="reopenProcedure"`.
- Annotate Consultation button: `:disabled="annotationStudioBusy || isEncounterLocked"` and `:title="isEncounterLocked ? __('Reopen the encounter to draw.') : ''"`.
- Drawing resume (✎) button: `v-if="isResumableAnnotation(annotation) && !isEncounterLocked"`.
- First line of `openAnnotationStudio` body: `if (isEncounterLocked.value) return`.

- [ ] **Step 5: Build**

Run: `cd /Users/hameed/Developer/bench-v16 && bench build --app do_derma 2>&1 | tail -5`
Expected: build succeeds. Restart the web server afterwards, or `/assets/do_derma/...` returns 404 (memory `bench-v16-test-and-lint-quirks`).

- [ ] **Step 6: Commit**

```bash
git add do_derma/public/js/chart
git commit -m "feat(chart): Reopen Encounter button, procedure reopen, and locked drawing controls"
```

---

### Task 9: Previous visits panel

**Files:**
- Create: `public/js/chart/components/assessment/PreviousVisitsPanel.vue`
- Modify: `public/js/chart/DermaChart.vue`
- Modify: `public/js/chart/derma_chart.bundle.css`

**Interfaces:**
- Consumes: `do_derma.api.get_previous_visits`.
- Produces: `<PreviousVisitsPanel :patient :current-encounter :preview-of :label-of :format-date @open-drawing>`.

- [ ] **Step 1: Component**

```vue
<template>
  <section v-if="visits.length || error" class="chart-annotation-history previous-visits" data-test="previous-visits">
    <header>
      <div>
        <strong>{{ __("Previous Visits") }}</strong>
        <small>{{ __("Drawings and assessment from earlier visits") }}</small>
      </div>
    </header>
    <article v-for="visit in visits" :key="visit.encounter" class="previous-visit" data-test="previous-visit">
      <header>
        <b>{{ formatDate(visit.encounter_date) }}</b>
        <small>{{ visit.practitioner_name }}</small>
      </header>
      <div v-if="visit.drawings.length" class="chart-annotation-list">
        <div v-for="drawing in visit.drawings" :key="drawing.name" class="chart-annotation-card">
          <button type="button" @click="$emit('open-drawing', drawing)">
            <span class="chart-annotation-preview">
              <img v-if="previewOf(drawing)" :src="previewOf(drawing)" :alt="labelOf(drawing)" loading="lazy" />
              <span v-else>{{ __("No preview") }}</span>
            </span>
            <b>{{ labelOf(drawing) }}</b>
          </button>
        </div>
      </div>
      <dl v-if="visit.assessment.length" class="previous-visit-assessment">
        <template v-for="field in shownFields(visit)" :key="field.label">
          <dt>{{ field.label }}</dt>
          <dd>{{ field.value }}</dd>
        </template>
      </dl>
      <button
        v-if="visit.assessment.length > PREVIEW_FIELDS"
        type="button"
        class="ghost small"
        data-test="previous-visit-show-all"
        @click="toggle(visit.encounter)"
      >
        {{ expanded.has(visit.encounter) ? __("Show less") : __("Show all") }}
      </button>
    </article>
    <p v-if="error" class="panel-muted" role="alert">
      {{ error }}
      <button type="button" class="ghost small" @click="loadPage">{{ __("Retry") }}</button>
    </p>
    <button
      v-if="hasMore && !error"
      type="button"
      class="ghost small"
      data-test="previous-visits-more"
      :disabled="loading"
      @click="loadPage"
    >
      <span v-if="loading" class="chart-spinner" aria-hidden="true"></span>
      {{ __("Load more") }}
    </button>
  </section>
</template>

<script setup>
import { reactive, ref, watch } from "vue"
import { serverErrorText } from "../../../shared/error_text.js"

const __ = window.__ || ((txt) => txt)
const PREVIEW_FIELDS = 3

const props = defineProps({
  patient: { type: String, default: "" },
  currentEncounter: { type: String, default: "" },
  previewOf: { type: Function, required: true },
  labelOf: { type: Function, required: true },
  formatDate: { type: Function, required: true },
})
defineEmits(["open-drawing"])

const visits = ref([])
const hasMore = ref(false)
const loading = ref(false)
const error = ref("")
const expanded = reactive(new Set())
let nextStart = 0

async function loadPage() {
  if (!props.patient || loading.value) return
  loading.value = true
  error.value = ""
  try {
    const { message } = await frappe.call({
      method: "do_derma.api.get_previous_visits",
      args: { patient: props.patient, current_encounter: props.currentEncounter, start: nextStart },
    })
    visits.value = [...visits.value, ...(message.visits || [])]
    hasMore.value = Boolean(message.has_more)
    nextStart = message.next_start
  } catch (err) {
    error.value = serverErrorText(err, __("Unable to load previous visits."))
  } finally {
    loading.value = false
  }
}

function shownFields(visit) {
  return expanded.has(visit.encounter) ? visit.assessment : visit.assessment.slice(0, PREVIEW_FIELDS)
}

function toggle(encounter) {
  if (expanded.has(encounter)) expanded.delete(encounter)
  else expanded.add(encounter)
}

watch(
  () => [props.patient, props.currentEncounter],
  () => {
    visits.value = []
    hasMore.value = false
    error.value = ""
    expanded.clear()
    nextStart = 0
    loadPage()
  },
  { immediate: true }
)
</script>
```

If a visit switch lands while a page is still loading, `loadPage` returns early and the new visit shows nothing. To prevent that, keep a request counter: capture `const request = ++requestId` at the start and ignore responses where `request !== requestId`. Also clear `loading` in the watcher. Write it that way.

- [ ] **Step 2: CSS**

Append after the `.chart-annotation-list small` rule (~501):

```css
.previous-visit {
  display: grid;
  gap: 8px;
  padding: 10px 0;
  border-top: 1px solid var(--derma-border-subtle);
}

.previous-visit > header {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.previous-visit > header small,
.previous-visit-assessment dt {
  color: var(--derma-text-muted);
}

.previous-visit-assessment {
  display: grid;
  grid-template-columns: minmax(96px, max-content) 1fr;
  gap: 4px 12px;
  margin: 0;
}

.previous-visit-assessment dd {
  margin: 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
```

- [ ] **Step 3: Mount in DermaChart**

Import `PreviousVisitsPanel from "./components/assessment/PreviousVisitsPanel.vue"`. Place it directly after the Drawings `</section>` inside `.clinical-soap-stack`:

```vue
                <PreviousVisitsPanel
                  :patient="patient.name || ''"
                  :current-encounter="encounter.name || ''"
                  :preview-of="annotationPreview"
                  :label-of="annotationTemplateLabel"
                  :format-date="formatDate"
                  @open-drawing="openAnnotationHistory"
                />
```

- [ ] **Step 4: Build and commit**

```bash
cd /Users/hameed/Developer/bench-v16 && bench build --app do_derma 2>&1 | tail -5
cd apps/do_derma && git add do_derma/public/js/chart
git commit -m "feat(assessment): show earlier visits' drawings and assessment"
```

---

### Task 10: Open Derma Chart from Visit History

**Files:**
- Modify: `public/js/derma_sidebar.js`

**Interfaces:**
- Consumes: `do_derma.openChart({ patient })` (existing, returns `{ route }`), `window.doHealthSidebar.getSelectedPatient()`.

- [ ] **Step 1: Implement**

Inside the existing IIFE, after `do_derma.openChart`:

```js
	// do_health renders these in health_sidebar.js renderVisitHistoryCardDetail; renamed there, the button just disappears.
	const VISIT_HISTORY = {
		actions: ".visit-history-actions",
		encounterButton: '[data-open-visit-history-route="Patient Encounter"]',
		chartButton: "[data-open-derma-chart]",
	};

	function addChartButtons(root) {
		root.querySelectorAll?.(VISIT_HISTORY.actions).forEach((actions) => {
			const encounterButton = actions.querySelector(VISIT_HISTORY.encounterButton);
			if (!encounterButton || actions.querySelector(VISIT_HISTORY.chartButton)) return;
			const button = document.createElement("button");
			button.type = "button";
			button.dataset.openDermaChart = encounterButton.dataset.docname;
			button.textContent = translate("Open Derma Chart");
			encounterButton.after(button);
		});
	}

	new MutationObserver((mutations) => {
		mutations.forEach((mutation) => mutation.addedNodes.forEach((node) => node.nodeType === 1 && addChartButtons(node.parentElement || node)));
	}).observe(document.body, { childList: true, subtree: true });

	document.addEventListener("click", async (event) => {
		const button = event.target.closest?.(VISIT_HISTORY.chartButton);
		if (!button) return;
		const selected = window.doHealthSidebar?.getSelectedPatient?.() || {};
		const result = await do_derma.openChart({
			patient: { patient: selected.patient, encounter: button.dataset.openDermaChart },
		});
		if (result?.route) frappe.set_route(...result.route);
	});
```

`document.body` exists when `app_include_js` runs in desk. If it is null, wrap the observer in `frappe.ready` or `$(() => ...)`. `openChart` passes `encounter` through to `ensure_chart_context`, so no appointment is needed.

- [ ] **Step 2: Build and verify in the browser**

```bash
cd /Users/hameed/Developer/bench-v16 && bench build --app do_derma 2>&1 | tail -3
bench --site dermaone.localhost browse --user Administrator
```

Restart the web server after the build. In the browser, select a patient with two or more visits, open **Visit History**, expand a visit and confirm there is exactly one **Open Derma Chart** after **Open Encounter**. Change a filter and expand again: there must still be exactly one. Click it: the derma chart opens on that encounter (header status matches, and a completed visit shows Reopen Encounter).

- [ ] **Step 3: Commit**

```bash
git add do_derma/public/js/derma_sidebar.js
git commit -m "feat(sidebar): open a visit in the derma chart from Visit History"
```

---

### Task 11: Whole-branch verification

- [ ] **Step 1: Full suite vs baseline**

Run: `cd /Users/hameed/Developer/bench-v16 && bench --site dermaone.localhost run-tests --app do_derma 2>&1 | grep -E "^(Ran |OK|FAILED)|^\s*(FAIL|ERROR)\s"`
Expected: exactly the five baseline names fail. Anything else is a regression from this branch: fix it.

- [ ] **Step 2: Lint changed Python**

```bash
cd /Users/hameed/Developer/bench-v16/apps/do_derma
git diff --name-only main...HEAD -- '*.py' | xargs pipx run ruff check
git diff --name-only main...HEAD -- '*.py' | grep -v "^do_derma/api.py$" | xargs pipx run ruff format --check
```

`api.py` has baseline findings, so only confirm no new ones in the lines this branch changed (`pipx run ruff check do_derma/api.py` output compared with the same run on `main`).

- [ ] **Step 3: Browser round trip**

With the chart open on a draft encounter that has a procedure and a prescription:
1. Complete Encounter. The header now shows amber **Reopen Encounter**. Annotate Consultation is disabled, and the Procedures tab shows Read only with no row Reopen.
2. Reopen Encounter, cancel the dialog. Nothing changes.
3. Reopen Encounter with a reason. The chart is editable, and the procedure row shows the unlock action. The Rx tab shows the ordered row under "Already ordered".
4. Reopen the procedure with a reason, change its notes, then Complete Encounter. The procedure is submitted again, and the encounter timeline in desk shows both "Reopened by …" comments.
5. Assessment tab on a patient with 6 or more earlier assessed visits: 5 cards, then Load more adds the rest and the button disappears. A thumbnail opens the read-only review dialog. On a patient with no earlier visits, the section is absent.

- [ ] **Step 4: Report** the results of each step, including any skipped tests and why.
