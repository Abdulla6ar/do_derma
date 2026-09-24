# Derma chart: reopen, previous visits, sidebar entry

Agreed 2026-09-24. Branch `feat/derma-chart-reopen-and-history`.

## Goal

1. A completed (submitted) encounter shows **Reopen Encounter** instead of **Complete Encounter**, and every action that could change submitted records is disabled until it is reopened. Submitted procedures can be reopened one at a time.
2. The Assessment tab shows earlier visits' drawings and an assessment preview under the Drawings section, 5 visits at a time.
3. do_health's Visit History drawer gets an **Open Derma Chart** button per visit, added by do_derma.

## Part 1: Reopen and lock

### Reopen encounter

`do_derma.api.reopen_derma_session(encounter, reason)`, whitelisted:

- Gate first (`_ensure_clinical_access`), then require `cancel` permission on the Patient Encounter. Roles that may reopen are configured through Role Permissions, not hardcoded.
- Throw when `reason` is blank or the encounter is not submitted (`docstatus != 1`).
- Set `docstatus = 0` on the encounter and its child rows, set `status = "Open"`, and add a timeline Comment: "Reopened by {user}: {reason}".
- Leave submitted Clinical Procedures, Sales Invoices, Medication Requests and Service Requests untouched.

Re-completing is safe by existing behaviour: `_complete_derma_procedures_for_session` submits drafts only, and `PatientEncounter.on_submit` skips prescription/order rows that already carry a request.

While reopened, a prescription row with a `medication_request` stays read-only, in the panel and in the save endpoint, so no submitted request is orphaned. The server keeps those rows as stored. Today the panel drops `medication_request` on save, and without that guard the next completion would order the drug twice.

### Reopen procedure

`do_derma.api.reopen_derma_procedure(procedure, reason)`, whitelisted, same pattern: gate, `cancel` permission on Clinical Procedure, required reason, `docstatus = 0`, `status = "In Progress"`, timeline Comment.

- Offered only while the encounter is open (decided 2026-09-24): reopen the encounter, then the procedures that need fixing. Complete Encounter resubmits them with the other drafts, so there is one completion path, and a completed encounter keeps the whole chart read-only.

- Refused when the procedure is billed on a submitted Sales Invoice. The UI disables the action and names the invoice.
- Resubmission re-runs do_health's `sync_clinical_procedure_billing`. A test must prove it updates the existing Billing Charge rather than adding one.
- Healthcare's `on_submit` recreates pre-op Nursing Tasks on every submit. Resubmission must skip creation when tasks already exist for the procedure.

### UI

- `DermaEncounterHeader.vue`: when `encounter.docstatus === 1`, the button reads **Reopen Encounter** in amber and opens a reason dialog. It is hidden when the user lacks cancel permission (sent in the chart context as `can_reopen`).
- `ProcedurePanel.vue`: submitted rows get a **Reopen** action with a reason dialog, disabled with the invoice name when invoiced.

### Lock

Every chart action that writes is gated twice: the control is disabled on `isEncounterLocked`, and the endpoint checks `docstatus`. Known gaps today:

- Annotate Consultation and the drawing resume (✎) button
- Copy marks from last visit
- Sync billables

Implementation audits every whitelisted write the chart calls and lists each one's gate in the plan. Fields Frappe marks `allow_on_submit` keep their existing edit mode.

## Part 2: Previous visits in the Assessment tab

`do_derma.api.get_previous_visits(patient, current_encounter=None, start=0, page_length=5)`, whitelisted:

- Gate first. Earlier encounters of the patient, excluding `current_encounter` and cancelled ones (`docstatus < 2`), ordered `encounter_date desc, creation desc`.
- A visit qualifies when it has at least one drawing **or** a filled assessment. Scan in batches until `page_length` qualifying visits are found or encounters run out.
- Each visit: `encounter`, `encounter_date`, `practitioner_name`, `drawings` (the encounter's and its procedures', via `_load_annotations_for_parents(..., include_scene=False)`), `assessment` (`mode` and filled `[{label, value}]` from `assessment.read_assessment`).
- Returns `{visits, has_more, next_start}`. `next_start` is the encounter offset to resume scanning from, since skipped encounters make visit counts differ from offsets.

`components/assessment/PreviousVisitsPanel.vue`, placed after the Drawings section:

- Fetched separately after the chart loads, not part of `get_chart_context`.
- Not rendered while the first page loads or when it returns no visits.
- Card: date and practitioner, drawing thumbnails opening the existing read-only `openAnnotationReviewDialog`, first 3 assessment fields as "Label: value" (long text clamped to 2 lines) with a **Show all** toggle.
- **Load more** appends 5 visits and shows a spinner, hidden when `has_more` is false. A failed fetch shows an inline message with Retry.

## Part 3: Open Derma Chart in Visit History

Override in `do_derma/public/js/derma_sidebar.js`, already loaded through `app_include_js`, so it only runs when do_derma is installed.

- A `MutationObserver` on the document watches for `.visit-history-actions`. Each row with a `[data-open-visit-history-route="Patient Encounter"]` button and no derma button gets **Open Derma Chart** inserted after it, carrying the encounter from `data-docname`.
- One delegated click handler calls `do_derma.openChart({ patient: { patient, encounter } })` with the patient from `window.doHealthSidebar.getSelectedPatient()`, then `frappe.set_route("derma-chart")`.
- The do_health selectors live in one constant at the top of the override. If do_health renames them, the button silently disappears and nothing else breaks.

## Testing

- Python (`do_derma/tests/`): reopen encounter (permission, blank reason, not submitted, comment written, procedures untouched, re-complete does not duplicate requests); reopen procedure (invoiced refusal, billing charge not duplicated, nursing tasks not duplicated); locked encounter refuses each gated write; `get_previous_visits` (qualification rule, excludes current and cancelled, paging with `has_more`/`next_start`).
- Browser: reopen/complete round trip in the chart, locked controls, the previous visits section and Load more, and the Visit History button opening the right encounter.

## Out of scope

- Editing past visits' drawings from the previous visits section.
- Reopening or adjusting invoices.
- Marking visits as derma visits in do_health's visit history API.
