<template>
  <section class="procedure-panel" data-test="procedure-panel">
    <div class="procedure-primary-toolbar">
      <label class="history-search">
        <i class="fa-solid fa-magnifying-glass"></i>
        <input
          v-model.trim="searchQuery"
          type="search"
          :placeholder="__('Search procedure, code, area, doctor, note')"
        />
      </label>

      <label class="history-filter-control compact-sort">
        <span>{{ __("Sort") }}</span>
        <select v-model="sortKey">
          <option value="newest">{{ __("Newest first") }}</option>
          <option value="oldest">{{ __("Oldest first") }}</option>
          <option value="tooth">{{ __("Area") }}</option>
          <option value="procedure">{{ __("Procedure A-Z") }}</option>
          <option value="price_desc">{{ __("Price high-low") }}</option>
          <option value="doctor">{{ __("Doctor") }}</option>
          <option value="status">{{ __("Status") }}</option>
        </select>
      </label>

      <button
        type="button"
        class="filter-toggle-btn"
        data-test="procedure-filters-toggle"
        :class="{ active: advancedFiltersOpen || hasSecondaryFilters }"
        @click="advancedFiltersOpen = !advancedFiltersOpen"
      >
        <i class="fa-solid fa-filter"></i>
        <span>{{ __("Filters") }}</span>
        <strong v-if="activeSecondaryFilterCount">{{ activeSecondaryFilterCount }}</strong>
      </button>

      <button v-if="hasActiveFilters" type="button" class="ghost small clear-filters-btn" @click="clearHistoryFilters">
        {{ __("Clear") }}
      </button>

      <div class="panel-actions">
        <label class="history-load-selector">
          <span>{{ __("Load") }}</span>
          <select v-model.number="rowBatchSize">
            <option v-for="size in ROW_BATCH_OPTIONS" :key="size" :value="size">{{ size }}</option>
          </select>
        </label>
        <button
          type="button"
          class="ghost small"
          data-test="procedure-copy-marks"
          :disabled="readOnly || !previousMarkCount"
          :title="previousMarkCount ? '' : __('This patient has no marks on an earlier visit.')"
          @click="emit('copy-marks')"
        >
          {{ __("Copy marks from last visit") }}
        </button>
        <button
          type="button"
          class="primary small"
          data-test="procedure-new"
          :disabled="readOnly"
          @click="emit('new-procedure')"
        >
          {{ __("New Procedure") }}
        </button>
        <span v-if="readOnly" class="badge read-only-badge">{{ __("Read only") }}</span>
        <span v-if="anesthesiaRecorded" class="badge anesthesia-badge">{{ __("Anesthesia recorded") }}</span>
      </div>
    </div>

    <div class="status-filter-row">
      <button
        class="status-chip"
        :class="{ active: activeStatus === 'all' }"
        type="button"
        @click="setFilter('all')"
      >
        {{ __("All") }}
      </button>
      <button
        v-for="pill in statusPills"
        :key="pill.key"
        class="status-chip"
        :class="{ active: activeStatus === pill.key }"
        type="button"
        @click="setFilter(pill.key)"
      >
        {{ pill.label }}
      </button>
    </div>

    <div v-if="advancedFiltersOpen" class="procedure-history-controls">
      <label class="history-filter-control">
        <span>{{ __("Area") }}</span>
        <select v-model="toothFilter">
          <option value="all">{{ __("All") }}</option>
          <option v-for="tooth in toothOptions" :key="tooth" :value="tooth">{{ tooth }}</option>
        </select>
      </label>
      <label class="history-filter-control">
        <span>{{ __("Doctor") }}</span>
        <select v-model="doctorFilter">
          <option value="all">{{ __("All") }}</option>
          <option v-for="doctor in doctorOptions" :key="doctor" :value="doctor">{{ doctor }}</option>
        </select>
      </label>
      <label class="history-filter-control">
        <span>{{ __("Date") }}</span>
        <select v-model="dateFilter">
          <option value="all">{{ __("All time") }}</option>
          <option value="today">{{ __("Today") }}</option>
          <option value="30">{{ __("Last 30 days") }}</option>
          <option value="90">{{ __("Last 90 days") }}</option>
          <option value="undated">{{ __("No date") }}</option>
        </select>
      </label>
      <label v-if="enableLabCases" class="history-filter-control" data-test="procedure-lab-filter">
        <span>{{ __("Lab") }}</span>
        <select v-model="labFilter">
          <option value="all">{{ __("All") }}</option>
          <option value="linked">{{ __("Linked") }}</option>
          <option value="suggested">{{ __("Suggested") }}</option>
          <option value="missing">{{ __("Needs case") }}</option>
          <option value="ready">{{ __("Ready") }}</option>
          <option value="overdue">{{ __("Overdue") }}</option>
        </select>
      </label>
      <label class="history-filter-control">
        <span>{{ __("Notes") }}</span>
        <select v-model="noteFilter">
          <option value="all">{{ __("All") }}</option>
          <option value="has_note">{{ __("Has note") }}</option>
          <option value="missing_note">{{ __("Missing note") }}</option>
        </select>
      </label>
      <label class="history-filter-control">
        <span>{{ __("Billing") }}</span>
        <select v-model="billingFilter">
          <option value="all">{{ __("All") }}</option>
          <option value="override">{{ __("Override") }}</option>
          <option value="no_charge">{{ __("No charge") }}</option>
          <option value="insurance">{{ __("Insurance") }}</option>
          <option value="billable">{{ __("Billable") }}</option>
        </select>
      </label>
    </div>

    <div class="procedure-history-summary" aria-live="polite">
      <div class="summary-tile">
        <span>{{ __("Matching") }}</span>
        <strong>{{ totalFilteredRows }}</strong>
      </div>
      <div class="summary-tile attention">
        <span>{{ __("Drafts") }}</span>
        <strong>{{ historyStats.drafts }}</strong>
      </div>
      <div class="summary-tile">
        <span>{{ __("Missing notes") }}</span>
        <strong>{{ historyStats.missingNotes }}</strong>
      </div>
      <div v-if="enableLabCases" class="summary-tile">
        <span>{{ __("Lab follow-up") }}</span>
        <strong>{{ historyStats.labFollowUp }}</strong>
      </div>
      <div class="summary-tile">
        <span>{{ __("Billing review") }}</span>
        <strong>{{ historyStats.billingReview }}</strong>
      </div>
    </div>

    <div
      class="procedure-table-wrapper"
      v-if="filteredGroups.length"
      ref="tableWrapperEl"
      @scroll.passive="handleHistoryScroll"
    >
      <table class="procedure-table">
        <colgroup>
          <col class="col-status" />
          <col class="col-procedure" />
          <col class="col-tooth" />
          <col class="col-details" />
          <col class="col-price" />
          <col class="col-notes" />
          <col class="col-doctor" />
          <col class="col-actions" />
        </colgroup>
        <thead>
          <tr>
            <th>Status</th>
            <th>Procedure</th>
            <th>{{ __("Area") }}</th>
            <th>Details</th>
            <th>Price</th>
            <th>Notes</th>
            <th>Doctor</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="group in filteredGroups" :key="group.key">
            <tr class="procedure-date-row">
              <td colspan="8">{{ formatGroupLabel(group) }}</td>
            </tr>
            <template v-for="row in group.items" :key="row.name">
            <tr @dblclick="handleRowDoubleClick(row, $event)">
              <td>
                <span class="pill" :class="statusClass(row.status)">
                  {{ row.status || "Draft" }}
                </span>
              </td>
              <td class="procedure-cell">
                <span v-if="row.procedure_code" class="procedure-code">{{ row.procedure_code }}</span>
                <button
                  v-if="getProcedureName(row)"
                  type="button"
                  class="procedure-open-link"
                  :title="__('Open Clinical Procedure')"
                  @click.stop="openProcedure(row)"
                >
                  {{ row.display_name || row.procedure_template || "-" }}
                </button>
                <span v-else>{{ row.display_name || row.procedure_template || "-" }}</span>
              </td>
              <td class="tooth-cell">{{ formatToothLabel(row.tooth) }}</td>
              <td>
                <div class="details-cell">
                  <button
                    v-if="row.derma_detail_text"
                    type="button"
                    class="detail-chip derma-detail-chip"
                    :title="row.derma_detail_text"
                    @click.stop="openProcedure(row)"
                  >
                    <i class="fa-solid fa-notes-medical"></i>
                    <span>{{ row.derma_detail_text }}</span>
                  </button>
                  <button
                    v-else-if="enableLabCases && rowAllowsSurfaces(row)"
                    type="button"
                    class="detail-chip detail-chip-button"
                    data-test="procedure-edit-surfaces"
                    :class="{ muted: !formatSurfaceText(row) }"
                    :disabled="!isEditable(row)"
                    @click="isEditable(row) ? $emit('edit-surfaces', row) : null"
                  >
                    <i class="fa-solid fa-layer-group"></i>
                    <span>{{ formatSurfaceText(row) || __("Details") }}</span>
                  </button>
                  <span v-else class="detail-chip muted">
                    <i class="fa-solid fa-layer-group"></i>
                    <span>{{ __("No details") }}</span>
                  </span>

                  <template v-if="enableLabCases && row.lab_case_name">
                    <button
                      class="detail-chip detail-chip-button"
                      :class="labCaseStatusClass(row.lab_case_status)"
                      type="button"
                      data-test="procedure-open-lab-case"
                      @click="$emit('open-lab-case', row)"
                    >
                      <i class="fa-solid fa-flask"></i>
                      <span>{{ row.lab_case_status || __("Lab linked") }}</span>
                    </button>
                  </template>
                  <template v-else-if="enableLabCases && row.lab_case_recommended && isEditable(row)">
                    <button
                      class="detail-chip detail-chip-button lab-suggested"
                      type="button"
                      data-test="procedure-create-lab-case"
                      @click="$emit('create-lab-case', row)"
                    >
                      <i class="fa-solid fa-flask"></i>
                      <span>{{ __("Create lab") }}</span>
                    </button>
                  </template>

                  <span v-if="rowIsInsurance(row)" class="detail-chip insurance-locked-label">
                    <i class="fa-solid fa-shield-halved"></i>
                    <span>{{ __("Insurance") }}</span>
                  </span>
                  <button
                    v-if="consumableOwners(row).length"
                    type="button"
                    class="detail-chip detail-chip-button"
                    data-test="procedure-toggle-consumables"
                    :aria-expanded="isConsumablesOpen(row)"
                    :title="__('Materials consumed')"
                    @click.stop="toggleConsumables(row)"
                  >
                    <i class="fa-solid fa-box-open"></i>
                    <span>{{ __("Materials") }} ({{ consumableCount(row) }})</span>
                  </button>
                  <!-- Offered even when empty: the studio only records these alongside a
                       drawing, so a procedure that needs no drawing has no other way in. -->
                  <button
                    v-if="row.derma_captures_variables_per_procedure"
                    type="button"
                    class="detail-chip detail-chip-button"
                    data-test="procedure-edit-variables"
                    :title="__('Recorded once for the whole procedure')"
                    :disabled="readOnly"
                    @click.stop="$emit('edit-procedure-variables', row)"
                  >
                    <i class="fa-solid fa-sliders"></i>
                    <span>{{ row.derma_procedure_variables_text || __("Add details") }}</span>
                  </button>
                  <span v-if="row.derma_artifact_text" class="detail-chip derma-artifact-chip">
                    <i class="fa-regular fa-images"></i>
                    <span>{{ row.derma_artifact_text }}</span>
                  </span>
                  <span v-else-if="isNoCharge(row)" class="detail-chip no-charge-label">
                    <i class="fa-solid fa-circle-dollar-to-slot"></i>
                    <span>{{ __("No charge") }}</span>
                  </span>
                  <span v-else-if="hasAnyOverride(row)" class="detail-chip override-label">
                    <i class="fa-solid fa-pen"></i>
                    <span>{{ __("Override") }}</span>
                  </span>

                  <div v-if="isEditable(row) && !rowIsInsurance(row)" class="override-picker compact" @keydown.escape="closeOverrideList">
                    <input
                      type="number"
                      class="inline-input"
                      :placeholder="__('Override')"
                      :value="edits[row.name]?.price ?? row.price_override ?? ''"
                      @focus="openOverrideList(row)"
                      @click="openOverrideList(row)"
                      @change="updatePriceManual(row, $event.target.value)"
                    />
                    <button
                      type="button"
                      class="ghost small no-charge-btn"
                      :class="{ active: isNoCharge(row) }"
                      :title="__('Mark as no charge')"
                      @click.stop="markNoCharge(row)"
                    >
                      {{ __("No charge") }}
                    </button>
                    <button
                      v-if="hasAnyOverride(row)"
                      type="button"
                      class="ghost small reset-btn"
                      :title="__('Clear override')"
                      @click.stop="clearPriceOverride(row)"
                    >
                      {{ __("Reset") }}
                    </button>
                    <span
                      v-if="isRowSaving(row)"
                      class="chart-spinner"
                      role="status"
                      data-test="procedure-row-saving"
                      :aria-label="__('Saving the price')"
                    ></span>
                    <div v-if="overrideListOpenRow === row.name" class="override-dropdown" @mousedown.prevent>
                      <button
                        v-for="pl in getPriceListOptions(row)"
                        :key="pl"
                        type="button"
                        class="override-option"
                        @mousedown.prevent="setOverrideFromPriceList(row, pl)"
                      >
                        {{ pl }}
                      </button>
                    </div>
                  </div>
                </div>
              </td>
              <td>
                <div class="price-readonly">
                  <span>{{ formatCurrency(computedPrice(row)) || "—" }}</span>
                  <span class="price-meta">{{ displayPriceList(row) }}</span>
                </div>
              </td>
              <td>
                <template v-if="isEditable(row)">
                  <div class="note-cell">
                    <button
                      type="button"
                      class="ghost small note-dialog-btn"
                      :title="__('Open procedure note editor')"
                      @click.stop="openProcedureNoteDialog(row)"
                    >
                      <i class="fa-regular fa-pen-to-square"></i>
                      <span>{{ getRowNoteValue(row) ? __("Edit Note") : __("Add Note") }}</span>
                    </button>
                    <div class="note-presence-indicator" :class="{ present: Boolean(getRowNoteRawValue(row)) }">
                      <i class="fa-solid fa-circle"></i>
                      <span>{{ getRowNoteRawValue(row) ? __("Note added") : __("No note") }}</span>
                    </div>
                  </div>
                </template>
                <template v-else>
                  <div v-if="getRowNoteRawValue(row)" class="note-readonly-cell">
                    <button
                      type="button"
                      class="ghost small note-view-btn"
                      :title="__('View procedure note')"
                      @click.stop="openProcedureNoteDialog(row)"
                    >
                      <i class="fa-regular fa-eye"></i>
                      <span>{{ __("View Note") }}</span>
                    </button>
                    <div class="note-presence-indicator present">
                      <i class="fa-solid fa-circle"></i>
                      <span>{{ __("Note added") }}</span>
                    </div>
                  </div>
                  <span v-else>—</span>
                </template>
              </td>
              <td>{{ row.practitioner_name || row.practitioner || "—" }}</td>
              <td class="row-actions">
                <button
                  v-if="getProcedureName(row)"
                  class="icon-btn"
                  type="button"
                  data-test="procedure-annotate"
                  :disabled="readOnly || Number(row.docstatus || 0) > 0"
                  :title="annotateLabel(row)"
                  :aria-label="annotateLabel(row)"
                  @click="$emit('annotate-procedure', row)"
                >
                  <i class="fa-regular fa-pen-to-square"></i>
                  <span v-if="Number(row.annotation_count || 0)" class="icon-badge">{{ row.annotation_count }}</span>
                </button>
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
                <button
                  v-if="isEditable(row)"
                  class="icon-btn danger"
                  type="button"
                  data-test="procedure-delete"
                  :title="__('Delete procedure')"
                  :aria-label="__('Delete procedure')"
                  @click="deleteRow(row)"
                >
                  <i class="fa-regular fa-trash-can"></i>
                </button>
                <span v-if="!getProcedureName(row) && !isEditable(row)" class="text-muted">—</span>
              </td>
            </tr>
            <tr v-if="isConsumablesOpen(row)" class="consumables-row" data-test="procedure-consumables-row">
              <td colspan="8">
                <ConsumablesEditor
                  v-for="owner in consumableOwners(row)"
                  :key="owner.name"
                  :owner-doctype="owner.doctype"
                  :owner-name="owner.name"
                  :label="owner.label"
                  :rows="consumablesOf(owner.source).consumables"
                  :removed="consumablesOf(owner.source).removed_consumables"
                  :defaults="consumablesOf(owner.source).default_consumables"
                  :read-only="readOnly || !owner.editable"
                  :saving="!!savingConsumables[owner.name]"
                  :error="consumableErrors[owner.name] || ''"
                  @change="saveConsumables(owner, $event)"
                />
              </td>
            </tr>
            </template>
          </template>
        </tbody>
      </table>
      <div class="procedure-load-more-row" v-if="totalFilteredRows > 0">
        <span class="text-muted">{{ displayedRows }} / {{ totalFilteredRows }} procedures</span>
        <button v-if="hasMoreRows" class="ghost small" type="button" @click="loadMoreRows">
          Load more
        </button>
      </div>
    </div>

    <div v-else class="procedure-empty">
      <strong>{{ emptyStateTitle }}</strong>
      <span>{{ emptyStateMessage }}</span>
      <button v-if="hasActiveFilters" type="button" class="ghost small" @click="clearHistoryFilters">
        {{ __("Clear filters") }}
      </button>
    </div>

    <div v-if="enableBillingSync" class="invoice-footer">
      <button
        type="button"
        class="invoice-btn ghost"
        data-test="procedure-sync-billables"
        :class="{ disabled: syncDisabled || readOnly }"
        :disabled="syncDisabled || readOnly"
        @click="$emit('sync-billables')"
      >
        {{ __("Sync Billables") }} ({{ totalCount }})
      </button>
    </div>

  </section>
</template>

<script setup>
import { computed, ref, onBeforeUnmount, onMounted, watch, nextTick } from "vue"
import ConsumablesEditor from "./consumables/ConsumablesEditor.vue"
import { procedureDisplayName } from "../../shared/procedure_label.js"
import { nameDialogControls } from "../../shared/dialog_a11y.js"
import { runDialogAction } from "../../shared/dialog_progress.js"
import { htmlToPlainText, serverErrorText } from "../../shared/error_text.js"

const __ = window.__ || ((txt) => txt)

const props = defineProps({
  statusPills: { type: Array, default: () => [] },
  groups: { type: Array, default: () => [] },
  totalCount: { type: Number, default: 0 },
  doctorName: { type: String, default: "" },
  priceLists: { type: Array, default: () => [] },
  defaultPriceList: { type: String, default: "" },
  syncDisabled: { type: Boolean, default: false },
  anesthesiaRecorded: { type: Boolean, default: false },
  readOnly: { type: Boolean, default: false },
  previousMarkCount: { type: Number, default: 0 },
  enableLabCases: { type: Boolean, default: false },
  enableBillingSync: { type: Boolean, default: false },
  canReopen: { type: Boolean, default: false },
})

const emit = defineEmits([
  "refresh",
  "sync-billables",
  "annotate-procedure",
  "edit-procedure-variables",
  "new-procedure",
  "copy-marks",
  "edit-surfaces",
  "create-lab-case",
  "open-lab-case",
  "reopen-procedure",
])

const advancedFiltersOpen = ref(false)
const activeStatus = ref("all")
const searchQuery = ref("")
const toothFilter = ref("all")
const doctorFilter = ref("all")
const dateFilter = ref("all")
const labFilter = ref("all")
const noteFilter = ref("all")
const billingFilter = ref("all")
const sortKey = ref("newest")
const CUSTOM_PRICE_LIST = "Custom"
const ROW_BATCH_OPTIONS = [20, 50, 100]
const rowBatchSize = ref(50)
const loadedRowsCount = ref(rowBatchSize.value)
const tableWrapperEl = ref(null)

const STATUS_COLORS = {
  draft: "pill-draft",
  pending: "pill-pending",
  in_progress: "pill-in-progress",
  submitted: "pill-submitted",
  completed: "pill-completed",
  cancelled: "pill-cancelled",
}

function buildGroupView(group, items = []) {
  const hasLabCaseColumn = items.some((row) => !!row.lab_case_name || !!row.lab_case_recommended)
  const hasDraft = items.some((row) => isEditable(row))
  const hasOverrideColumn =
    hasDraft ||
    items.some((row) => hasAnyOverride(row))
  const showActions = items.some((row) => isEditable(row))
  return { ...group, items, hasLabCaseColumn, hasOverrideColumn, showActions }
}

function normalizeTooth(value) {
  return String(value || "").trim()
}

function formatToothLabel(value) {
  const tooth = normalizeTooth(value)
  if (!tooth) return "—"
  return tooth === "Full Mouth" ? tooth : tooth.replace(/^Tooth\s+/i, "")
}

function compareToothLabels(a, b) {
  const left = Number(a)
  const right = Number(b)
  if (Number.isFinite(left) && Number.isFinite(right)) return left - right
  if (Number.isFinite(left)) return -1
  if (Number.isFinite(right)) return 1
  return String(a).localeCompare(String(b), undefined, { numeric: true })
}

function getDoctorLabel(row) {
  return String(row?.practitioner_name || row?.practitioner || "").trim()
}

function getProcedureLabel(row) {
  return String(row?.display_name || row?.procedure_template || row?.procedure || row?.name || "").trim()
}

function getRowDateValue(row) {
  return row?.procedure_date || row?.start_date || row?.date || ""
}

function getRowTimestamp(row) {
  const dateValue = getRowDateValue(row)
  if (!dateValue) return null
  const timeValue = row?.procedure_time || row?.time || row?.start_time || "00:00:00"
  const normalizedDate = String(dateValue).includes("T") ? String(dateValue) : `${dateValue}T${timeValue}`
  const parsed = Date.parse(normalizedDate)
  return Number.isFinite(parsed) ? parsed : null
}

function todayDateString() {
  const now = new Date()
  return [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, "0"),
    String(now.getDate()).padStart(2, "0"),
  ].join("-")
}

function rowMatchesDateFilter(row) {
  if (dateFilter.value === "all") return true
  const dateValue = getRowDateValue(row)
  if (dateFilter.value === "undated") return !dateValue
  if (!dateValue) return false
  const rowDate = String(dateValue).slice(0, 10)
  if (dateFilter.value === "today") return rowDate === todayDateString()
  const days = Number(dateFilter.value)
  if (!Number.isFinite(days)) return true
  const timestamp = getRowTimestamp(row)
  if (timestamp === null) return false
  const start = new Date()
  start.setHours(0, 0, 0, 0)
  start.setDate(start.getDate() - days + 1)
  return timestamp >= start.getTime()
}

function rowNeedsLabFollowUp(row) {
  const status = String(row?.lab_case_status || "").toLowerCase()
  if (Number(row?.lab_case_overdue || row?.is_lab_case_overdue || 0)) return true
  if (["ready for delivery", "quality checked", "received in clinic"].includes(status)) return true
  return Boolean(row?.lab_case_recommended && !row?.lab_case_name)
}

function rowMatchesLabFilter(row) {
  if (labFilter.value === "all") return true
  const status = String(row?.lab_case_status || "").toLowerCase()
  if (labFilter.value === "linked") return Boolean(row?.lab_case_name)
  if (labFilter.value === "suggested") return Boolean(row?.lab_case_recommended)
  if (labFilter.value === "missing") return Boolean(row?.lab_case_recommended && !row?.lab_case_name)
  if (labFilter.value === "ready") return ["ready for delivery", "quality checked", "received in clinic"].includes(status)
  if (labFilter.value === "overdue") return Boolean(Number(row?.lab_case_overdue || row?.is_lab_case_overdue || 0))
  return true
}

function rowMatchesBillingFilter(row) {
  if (billingFilter.value === "all") return true
  if (billingFilter.value === "override") return hasAnyOverride(row) && !isNoCharge(row)
  if (billingFilter.value === "no_charge") return isNoCharge(row)
  if (billingFilter.value === "insurance") return rowIsInsurance(row)
  if (billingFilter.value === "billable") return Number(computedPrice(row) || 0) > 0 && !isNoCharge(row)
  return true
}

function rowMatchesSearch(row) {
  const query = searchQuery.value.toLowerCase()
  if (!query) return true
  const haystack = [
    getProcedureLabel(row),
    row?.procedure_code,
    row?.procedure_template,
    row?.status,
    row?.tooth,
    row?.derma_detail_text,
    row?.derma_artifact_text,
    formatSurfaceText(row),
    getDoctorLabel(row),
    row?.lab_case_name,
    row?.lab_case_status,
    getRowNoteValue(row),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase()
  return haystack.includes(query)
}

function rowMatchesFilters(row) {
  if (activeStatus.value !== "all") {
    const target = activeStatus.value.toLowerCase()
    if ((row?.status || "").toString().toLowerCase() !== target) return false
  }
  if (toothFilter.value !== "all" && normalizeTooth(row?.tooth) !== toothFilter.value) return false
  if (doctorFilter.value !== "all" && getDoctorLabel(row) !== doctorFilter.value) return false
  if (noteFilter.value === "has_note" && !getRowNoteRawValue(row)) return false
  if (noteFilter.value === "missing_note" && getRowNoteRawValue(row)) return false
  return rowMatchesSearch(row) && rowMatchesDateFilter(row) && rowMatchesLabFilter(row) && rowMatchesBillingFilter(row)
}

function sortRows(rows = []) {
  const copy = [...rows]
  const direction = sortKey.value === "oldest" ? 1 : -1
  if (["newest", "oldest"].includes(sortKey.value)) {
    return copy.sort((a, b) => {
      const left = getRowTimestamp(a)
      const right = getRowTimestamp(b)
      if (left === null && right === null) return 0
      if (left === null) return 1
      if (right === null) return -1
      return direction * (left - right)
    })
  }
  if (sortKey.value === "tooth") {
    return copy.sort((a, b) => compareToothLabels(normalizeTooth(a?.tooth), normalizeTooth(b?.tooth)))
  }
  if (sortKey.value === "procedure") {
    return copy.sort((a, b) => getProcedureLabel(a).localeCompare(getProcedureLabel(b)))
  }
  if (sortKey.value === "price_desc") {
    return copy.sort((a, b) => Number(computedPrice(b) || 0) - Number(computedPrice(a) || 0))
  }
  if (sortKey.value === "doctor") {
    return copy.sort((a, b) => getDoctorLabel(a).localeCompare(getDoctorLabel(b)))
  }
  if (sortKey.value === "status") {
    return copy.sort((a, b) => String(a?.status || "").localeCompare(String(b?.status || "")))
  }
  return copy
}

function sortGroups(groups = []) {
  const copy = [...groups]
  if (sortKey.value === "oldest") {
    return copy.sort((a, b) => {
      if (a.timestamp === null && b.timestamp === null) return 0
      if (a.timestamp === null) return 1
      if (b.timestamp === null) return -1
      return a.timestamp - b.timestamp
    })
  }
  return copy.sort((a, b) => {
    if (a.timestamp === null && b.timestamp === null) return 0
    if (a.timestamp === null) return 1
    if (b.timestamp === null) return -1
    return b.timestamp - a.timestamp
  })
}

const allRows = computed(() =>
  (props.groups || []).flatMap((group) => group.items || [])
)

const toothOptions = computed(() => {
  const values = new Set()
  for (const row of allRows.value) {
    const tooth = normalizeTooth(row?.tooth)
    if (tooth) values.add(tooth)
  }
  return Array.from(values).sort(compareToothLabels)
})

const doctorOptions = computed(() => {
  const values = new Set()
  for (const row of allRows.value) {
    const doctor = getDoctorLabel(row)
    if (doctor) values.add(doctor)
  }
  return Array.from(values).sort((a, b) => a.localeCompare(b))
})

const hasActiveFilters = computed(() =>
  Boolean(
    searchQuery.value ||
      activeStatus.value !== "all" ||
      toothFilter.value !== "all" ||
      doctorFilter.value !== "all" ||
      dateFilter.value !== "all" ||
      labFilter.value !== "all" ||
      noteFilter.value !== "all" ||
      billingFilter.value !== "all" ||
      sortKey.value !== "newest"
  )
)

const activeSecondaryFilterCount = computed(() => {
  let count = 0
  if (toothFilter.value !== "all") count += 1
  if (doctorFilter.value !== "all") count += 1
  if (dateFilter.value !== "all") count += 1
  if (labFilter.value !== "all") count += 1
  if (noteFilter.value !== "all") count += 1
  if (billingFilter.value !== "all") count += 1
  return count
})

const hasSecondaryFilters = computed(() => activeSecondaryFilterCount.value > 0)

const allFilteredGroups = computed(() => {
  const sourceGroups = props.groups || []
  return sortGroups(
    sourceGroups
    .map((group) => {
      const items = sortRows((group.items || []).filter(rowMatchesFilters))
      return buildGroupView(group, items)
    })
    .filter((group) => group.items.length)
  )
})

const totalFilteredRows = computed(() =>
  allFilteredGroups.value.reduce((sum, group) => sum + (group.items?.length || 0), 0)
)

const filteredGroups = computed(() => {
  let remaining = Math.max(0, loadedRowsCount.value)
  const visible = []
  for (const group of allFilteredGroups.value) {
    if (remaining <= 0) break
    const allItems = group.items || []
    const items = allItems.slice(0, remaining)
    remaining -= items.length
    if (items.length) {
      visible.push(buildGroupView(group, items))
    }
  }
  return visible
})

const displayedRows = computed(() =>
  filteredGroups.value.reduce((sum, group) => sum + (group.items?.length || 0), 0)
)

const hasMoreRows = computed(() => displayedRows.value < totalFilteredRows.value)

const filteredRows = computed(() =>
  allFilteredGroups.value.flatMap((group) => group.items || [])
)

const historyStats = computed(() => {
  const stats = {
    drafts: 0,
    missingNotes: 0,
    labFollowUp: 0,
    billingReview: 0,
  }
  for (const row of filteredRows.value) {
    const missingNote = !getRowNoteRawValue(row)
    const labFollowUp = rowNeedsLabFollowUp(row)
    if (isEditable(row)) stats.drafts += 1
    if (missingNote) stats.missingNotes += 1
    if (labFollowUp) stats.labFollowUp += 1
    if (hasAnyOverride(row) || rowIsInsurance(row)) stats.billingReview += 1
  }
  return stats
})

const emptyStateTitle = computed(() =>
  allRows.value.length > 0 && hasActiveFilters.value ? __("No matching procedures") : __("No procedures added yet")
)

// The list holds this visit only; earlier visits live on the Review timeline.
const emptyStateMessage = computed(() =>
  allRows.value.length > 0 && hasActiveFilters.value
    ? __("Adjust or clear the filters to bring this visit's procedures back into view.")
    : __("Procedures recorded on this visit appear here. Earlier visits are on the Review timeline.")
)

function setFilter(status) {
  activeStatus.value = status
  loadedRowsCount.value = rowBatchSize.value
  if (tableWrapperEl.value) tableWrapperEl.value.scrollTop = 0
  nextTick(() => ensureViewportFilled())
}

function loadMoreRows() {
  if (!hasMoreRows.value) return
  loadedRowsCount.value += rowBatchSize.value
}

function clearHistoryFilters() {
  searchQuery.value = ""
  activeStatus.value = "all"
  toothFilter.value = "all"
  doctorFilter.value = "all"
  dateFilter.value = "all"
  labFilter.value = "all"
  noteFilter.value = "all"
  billingFilter.value = "all"
  sortKey.value = "newest"
  loadedRowsCount.value = rowBatchSize.value
  if (tableWrapperEl.value) tableWrapperEl.value.scrollTop = 0
  nextTick(() => ensureViewportFilled())
}

function handleHistoryScroll(event) {
  if (!hasMoreRows.value) return
  const el = event?.target
  if (!el) return
  const threshold = 96
  if (el.scrollTop + el.clientHeight >= el.scrollHeight - threshold) {
    loadMoreRows()
  }
}

async function ensureViewportFilled() {
  const el = tableWrapperEl.value
  if (!el) return
  let guard = 0
  while (hasMoreRows.value && guard < 12 && el.scrollHeight <= el.clientHeight + 4) {
    loadMoreRows()
    guard += 1
    await nextTick()
  }
}

watch(rowBatchSize, async (value) => {
  loadedRowsCount.value = value
  await nextTick()
  if (tableWrapperEl.value) tableWrapperEl.value.scrollTop = 0
  ensureViewportFilled()
})

watch(
  () => props.groups,
  async () => {
    loadedRowsCount.value = rowBatchSize.value
    await nextTick()
    if (tableWrapperEl.value) tableWrapperEl.value.scrollTop = 0
    ensureViewportFilled()
  }
)

watch(
  [searchQuery, toothFilter, doctorFilter, dateFilter, labFilter, noteFilter, billingFilter, sortKey],
  async () => {
    loadedRowsCount.value = rowBatchSize.value
    await nextTick()
    if (tableWrapperEl.value) tableWrapperEl.value.scrollTop = 0
    ensureViewportFilled()
  }
)

onMounted(() => {
  nextTick(() => ensureViewportFilled())
})

const edits = ref({})
const overrideListOpenRow = ref(null)
let overrideOutsideHandler = null

function getEditValue(row, key) {
  return edits.value[row.name]?.[key]
}

function isEditable(row) {
  if (props.readOnly) return false
  if (Number(row?.docstatus || 0) > 0) return false
  const status = (row.status || "").toLowerCase()
  return !["submitted", "completed", "cancelled"].includes(status)
}

function isPersistedRow(row) {
  const name = row?.name
  return Boolean(name) && !String(name).startsWith("local-")
}

function computedPrice(row) {
  if (row.base_rate !== undefined && row.base_rate !== null) return row.base_rate
  if (row.base_price !== undefined && row.base_price !== null) return row.base_price
  return ""
}

function displayPrice(row) {
  if (getEditValue(row, "price") !== undefined) return getEditValue(row, "price")
  if (row.price_override !== null && row.price_override !== undefined) return row.price_override
  if (row.display_price !== undefined && row.display_price !== null) return row.display_price
  if (row.base_rate !== undefined && row.base_rate !== null) return row.base_rate
  if (row.base_price !== undefined && row.base_price !== null) return row.base_price
  return ""
}

// Consumables belong to the mark, not to the procedure row that shows them. The server
// decides what counts as a deviation from the template; nothing here recomputes it.
const expandedConsumables = ref({})
const consumablesByOwner = ref({})
const consumableErrors = ref({})
const savingConsumables = ref({})
// Which procedure rows have a price, no-charge or note write in flight.
const savingRows = ref({})

watch(
  () => props.groups,
  () => {
    consumablesByOwner.value = {}
    consumableErrors.value = {}
  }
)

function marksOf(row) {
  return row?.derma_marks || []
}

function consumablesOf(owner) {
  return (
    consumablesByOwner.value[owner?.name] || {
      consumables: owner?.consumables || [],
      removed_consumables: owner?.removed_consumables || [],
      default_consumables: owner?.default_consumables || [],
    }
  )
}

// A procedure records its materials on its annotations when it has any, and on itself when
// it has none, so exactly one owner is ever on screen for a row.
function consumableOwners(row) {
  const marks = marksOf(row)
  if (marks.length) {
    return marks.map((mark) => ({
      doctype: "Derma Chart Mark",
      name: mark.name,
      label: markConsumablesLabel(mark),
      source: mark,
      editable: isEditable(row),
    }))
  }
  if (!isPersistedRow(row)) return []
  return [
    {
      doctype: "Clinical Procedure",
      name: row.name,
      label: procedureDisplayName(row),
      source: row,
      editable: isEditable(row),
    },
  ]
}

function consumableCount(row) {
  return consumableOwners(row).reduce(
    (total, owner) => total + consumablesOf(owner.source).consumables.length,
    0
  )
}

function isConsumablesOpen(row) {
  return !!expandedConsumables.value[row.name]
}

function toggleConsumables(row) {
  expandedConsumables.value = {
    ...expandedConsumables.value,
    [row.name]: !expandedConsumables.value[row.name],
  }
}

/** Names the mark the way the rest of the chart does - "#3 Botox - Forehead", never its autoname. */
function markConsumablesLabel(mark) {
  const detail = [mark.procedure_template || mark.category, mark.region_label || mark.body_region]
    .filter(Boolean)
    .join(" — ")
  const number = mark.sequence ? `#${mark.sequence}` : ""
  return [number, detail].filter(Boolean).join(" ") || __("Mark")
}

async function saveConsumables(owner, rows) {
  const name = owner.name
  savingConsumables.value = { ...savingConsumables.value, [name]: true }
  consumableErrors.value = { ...consumableErrors.value, [name]: "" }
  try {
    // Silent: a refused save already reports itself on the line it came from, and the
    // modal on top of it only buries the row the clinician is fixing.
    const resp = await frappe.call({
      method: "do_derma.api.save_consumables",
      args: { owner_doctype: owner.doctype, owner_name: name, rows },
      silent: true,
    })
    if (resp?.message) {
      consumablesByOwner.value = { ...consumablesByOwner.value, [name]: resp.message }
    }
  } catch (err) {
    consumableErrors.value = { ...consumableErrors.value, [name]: consumableErrorText(err) }
  } finally {
    savingConsumables.value = { ...savingConsumables.value, [name]: false }
  }
}

function consumableErrorText(err) {
  return serverErrorText(err, __("The materials could not be saved."))
}

function updateLocal(row, key, value) {
  edits.value = {
    ...edits.value,
    [row.name]: {
      ...(edits.value[row.name] || {}),
      [key]: value,
    },
  }
}

function resolveRowPatient(row) {
  if (row?.patient) return row.patient
  return window?.do_health?.patientWatcher?.read?.()?.patient || ""
}

function resolveRowProcedureName(row) {
  return row?.clinical_procedure || row?.name || ""
}

function escapeHtml(value) {
  const text = String(value ?? "")
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;")
}

function formatHistoryNote(value) {
  return htmlToPlainText(value || "")
}

async function fetchRowRelatedHistory(row, limit = 8) {
  const patient = resolveRowPatient(row)
  const clinicalProcedure = resolveRowProcedureName(row)
  if (!patient || !clinicalProcedure || String(clinicalProcedure).startsWith("local-")) {
    return { notes: [] }
  }
  try {
    const resp = await frappe.call("do_health.api.notes_center.get_related_procedure_notes", {
      patient,
      clinical_procedure: clinicalProcedure,
      limit,
    })
    const payload = resp?.message || { notes: [] }
    const filteredNotes = Array.isArray(payload.notes)
      ? payload.notes.filter((item) => {
          const sourceName = String(item?.clinical_procedure || item?.name || "").trim()
          if (!sourceName) return true
          return sourceName !== String(clinicalProcedure).trim()
        })
      : []
    return { ...payload, notes: filteredNotes }
  } catch (err) {
    // eslint-disable-next-line no-console
    console.warn("Failed to fetch related procedure notes", err)
    return { notes: [] }
  }
}

function getRowNoteRawValue(row) {
  const value = edits.value[row.name]?.notes ?? row.notes ?? ""
  return String(value || "")
}

function getRowNoteValue(row) {
  const value = getRowNoteRawValue(row)
  return htmlToPlainText(value)
}

function toEditorHtml(value) {
  const text = String(value || "").trim()
  if (!text) return ""
  if (/<[a-z][\s\S]*>/i.test(text)) {
    return text.replace(/<script[\s\S]*?>[\s\S]*?<\/script>/gi, "")
  }
  const escaped = escapeHtml(text).replace(/\r\n/g, "\n")
  const paragraphs = escaped
    .split(/\n{2,}/)
    .map((chunk) => chunk.replace(/\n/g, "<br>").trim())
    .filter(Boolean)
  return paragraphs.length ? `<p>${paragraphs.join("</p><p>")}</p>` : ""
}

async function fetchNoteTemplate(templateName) {
  if (!templateName) return { raw_html: "", plain_text: "" }
  try {
    const resp = await frappe.call("frappe.client.get", {
      doctype: "Derma Note Template",
      name: templateName,
    })
    const raw = String(resp?.message?.note || "")
    return {
      raw_html: toEditorHtml(raw),
      plain_text: htmlToPlainText(raw),
    }
  } catch (err) {
    // eslint-disable-next-line no-console
    console.warn("Failed to fetch note template", err)
    frappe.show_alert({
      message: __("Could not load note template {0}.").replace("{0}", templateName),
      indicator: "red",
    })
    return null
  }
}

function renderRelatedNotesHtml(notes = []) {
  if (!Array.isArray(notes) || !notes.length) {
    return `<div class="procedure-note-dialog__empty">${__("No related notes yet.")}</div>`
  }
  return `
    <div class="procedure-note-dialog__history-list">
      ${notes
        .map((item) => {
          const title = escapeHtml(item?.source_procedure_label || item?.procedure_template || item?.name || __("Procedure"))
          const meta = escapeHtml(item?.occurred_at_label || item?.occurred_at || item?.modified || "")
          const body = escapeHtml(formatHistoryNote(item?.note || ""))
          return `
            <div class="procedure-note-dialog__history-item">
              <div class="procedure-note-dialog__history-title">${title}</div>
              <div class="procedure-note-dialog__history-meta">${meta}</div>
              <div class="procedure-note-dialog__history-body">${body || "—"}</div>
            </div>
          `
        })
        .join("")}
    </div>
  `
}

async function openProcedureNoteDialog(row) {
  if (!row) return
  const editable = isEditable(row)
  const currentPlain = getRowNoteValue(row).trim()
  const procedureLabel = row.display_name || row.procedure_template || row.name || __("Procedure")
  let dialog = null

  const setTemplatePreview = (templateHtml = "", templatePlain = "") => {
    if (!dialog) return
    const $wrapper = dialog.fields_dict?.template_preview?.$wrapper
    if (!$wrapper?.length) return
    if (!templateHtml && !templatePlain) {
      $wrapper.html("")
      return
    }
    const safePlain = escapeHtml(templatePlain || "")
    $wrapper.html(`
      <div class="procedure-note-dialog__template-preview">
        <div class="procedure-note-dialog__template-preview-rendered">${templateHtml || `<p>${safePlain || "—"}</p>`}</div>
        <div class="procedure-note-dialog__template-preview-plain">${safePlain || "—"}</div>
      </div>
    `)
  }

  // The procedure template's own note sentence is the default when nothing is
  // picked from the library.
  const noteSentence = String(row.note_sentence_template || "").trim()

  const setTemplateMessage = (text) => {
    const $wrapper = dialog?.fields_dict?.template_preview?.$wrapper
    if ($wrapper?.length) $wrapper.html(`<div class="procedure-note-dialog__loading">${escapeHtml(text)}</div>`)
  }

  const updateTemplatePreview = async () => {
    if (!dialog) return
    const templateName = dialog.get_value("note_template")
    if (!templateName) {
      setTemplatePreview(toEditorHtml(noteSentence), htmlToPlainText(noteSentence))
      return
    }
    setTemplateMessage(__("Loading the template..."))
    const templateData = await fetchNoteTemplate(templateName)
    if (!templateData) {
      setTemplatePreview()
      return
    }
    setTemplatePreview(templateData.raw_html, templateData.plain_text)
  }

  const applyTemplateToNote = async () => {
    if (!dialog) return
    const templateName = dialog.get_value("note_template")
    if (!templateName && !noteSentence) {
      frappe.show_alert({ message: __("Select a note template first."), indicator: "orange" })
      return
    }
    const templateData = templateName
      ? await fetchNoteTemplate(templateName)
      : { raw_html: toEditorHtml(noteSentence), plain_text: htmlToPlainText(noteSentence) }
    if (!templateData) return
    if (!templateData.raw_html && !templateData.plain_text) {
      frappe.show_alert({ message: __("Selected template has no text."), indicator: "orange" })
      return
    }
    const append = Boolean(dialog.get_value("append_to_existing"))
    const existing = String(dialog.get_value("note") || "").trim()
    const incoming = String(templateData.raw_html || "").trim()
    const merged = append && existing ? `${existing}<p><br></p>${incoming}` : incoming
    dialog.set_value("note", merged)
    setTemplatePreview(templateData.raw_html, templateData.plain_text)
    frappe.show_alert({ message: __("Template inserted."), indicator: "green" })
  }

  const renderRelatedHistory = async () => {
    if (!dialog) return
    const $wrapper = dialog.fields_dict?.related_notes?.$wrapper
    if (!$wrapper?.length) return
    $wrapper.html(`<div class="procedure-note-dialog__loading">${__("Loading related notes...")}</div>`)
    const payload = await fetchRowRelatedHistory(row, 14)
    $wrapper.html(renderRelatedNotesHtml(payload?.notes || []))
  }

  dialog = new frappe.ui.Dialog({
    title: `${__("Procedure Note")} · ${procedureLabel}`,
    size: "large",
    fields: [
      {
        fieldname: "related_notes_label",
        fieldtype: "HTML",
      },
      {
        fieldname: "related_notes",
        fieldtype: "HTML",
      },
      {
        fieldname: "template_break",
        fieldtype: "Section Break",
        hidden: editable ? 0 : 1,
      },
      {
        fieldname: "note_template",
        fieldtype: "Link",
        label: __("Apply Note Template"),
        options: "Derma Note Template",
        hidden: editable ? 0 : 1,
        get_query: () => ({ filters: { disabled: 0 } }),
        onchange: updateTemplatePreview,
      },
      {
        fieldname: "append_to_existing",
        fieldtype: "Check",
        label: __("Append template to current note"),
        default: currentPlain ? 1 : 0,
        hidden: editable ? 0 : 1,
      },
      {
        fieldname: "template_preview",
        fieldtype: "HTML",
        label: __("Template Preview"),
        hidden: editable ? 0 : 1,
      },
      {
        fieldname: "note_break",
        fieldtype: "Section Break",
      },
      {
        fieldname: "note",
        fieldtype: "Text Editor",
        label: __("Note"),
        default: getRowNoteRawValue(row),
        read_only: editable ? 0 : 1,
      },
    ],
    primary_action_label: editable ? __("Save Note") : __("Close"),
    primary_action: (values) => {
      if (!editable) {
        dialog.hide()
        return undefined
      }
      return runDialogAction(dialog, __("Saving the note..."), async () => {
        updateLocal(row, "notes", values?.note ?? "")
        const saved = await saveRow(row, { silent: true })
        if (!saved) {
          frappe.show_alert({ message: __("Could not save the note."), indicator: "red" })
          return false
        }
        frappe.show_alert({ message: __("Procedure note saved."), indicator: "green" })
        return true
      })
    },
  })

  dialog.$wrapper?.addClass("procedure-note-dialog")
  dialog.fields_dict.related_notes_label?.$wrapper?.html(
    `<div class="procedure-note-dialog__related-title">${__("Related Procedure Notes")}</div>`
  )
  if (editable) {
    dialog.set_secondary_action_label(__("Apply Template"))
    dialog.set_secondary_action(async (event) => {
      const button = event?.currentTarget
      if (button) button.disabled = true
      try {
        await applyTemplateToNote()
      } finally {
        if (button) button.disabled = false
      }
    })
  }
  dialog.show()
  nameDialogControls(dialog)
  void renderRelatedHistory()
  if (editable && noteSentence) void updateTemplatePreview()
}

function normalizePriceListName(value) {
  if (!value) return value
  const trimmed = String(value).trim()
  const parts = trimmed.split(" — ")
  if (parts.length <= 1) return trimmed
  const suffix = parts[parts.length - 1].trim()
  if (/^[0-9.,]+$/.test(suffix)) {
    return parts.slice(0, -1).join(" — ").trim()
  }
  return trimmed
}

function displayPriceList(row) {
  const frozen = row.price_list_used || row.price_list
  const value = normalizePriceListName(getEditValue(row, "price_list") || frozen || props.defaultPriceList)
  if (!value) return "—"
  return value === CUSTOM_PRICE_LIST || value === "Custom" ? "Custom" : value
}

function priceSourceLabel(row) {
  if (isNoCharge(row)) return "No charge"
  if (row.price_override !== null && row.price_override !== undefined && row.price_override > 0) return "Override"
  return ""
}

function isNoCharge(row) {
  return Number(row?.no_charge || 0) === 1 && Number(row?.price_override || 0) === 0
}

function rowIsInsurance(row) {
  return Boolean(row?.appointment_is_insurance) || String(row?.appointment_payment_type || "").toLowerCase().includes("insur")
}

function hasAnyOverride(row) {
  if (isNoCharge(row)) return true
  return row?.price_override !== null && row?.price_override !== undefined && Number(row.price_override) !== 0
}

function displayOverride(row) {
  if (isNoCharge(row)) return __("No charge")
  if (row.price_override === null || row.price_override === undefined) return "—"
  if (Number(row.price_override) === 0) return "—"
  return formatCurrency(row.price_override)
}

function getPriceListOptions(row) {
  const set = new Set()
  ;[row.price_list, props.defaultPriceList, ...(props.priceLists || [])].forEach((pl) => {
    const normalized = normalizePriceListName(pl)
    if (normalized && normalized !== "Custom" && normalized !== CUSTOM_PRICE_LIST) set.add(normalized)
  })
  return Array.from(set)
}

function updatePriceManual(row, value) {
  if (rowIsInsurance(row)) {
    frappe.show_alert({ message: __("Overrides are not allowed for insurance visits."), indicator: "orange" })
    return
  }
  updateLocal(row, "price", value)
  updateLocal(row, "no_charge", false)
  updateLocal(row, "price_override_reason", "")
  // updateLocal(row, "price_list", CUSTOM_PRICE_LIST)
  saveRow(row, { silent: true })
}

function clearPriceOverride(row) {
  updateLocal(row, "price", null)
  updateLocal(row, "no_charge", false)
  updateLocal(row, "price_override_reason", "")
  saveRow(row)
}

function promptNoChargeReason() {
  return new Promise((resolve) => {
    frappe.prompt(
      [
        {
          fieldtype: "Small Text",
          fieldname: "reason",
          label: __("No Charge Reason"),
          reqd: 1,
        },
      ],
      (values) => resolve(values?.reason || ""),
      __("Confirm No Charge"),
      __("Apply")
    )
  })
}

async function markNoCharge(row) {
  if (!isEditable(row)) return
  if (rowIsInsurance(row)) {
    frappe.show_alert({ message: __("Overrides are not allowed for insurance visits."), indicator: "orange" })
    return
  }
  const reason = await promptNoChargeReason()
  if (!reason) return
  updateLocal(row, "price", 0)
  updateLocal(row, "no_charge", true)
  updateLocal(row, "price_override_reason", reason)
  saveRow(row)
}

function onPriceListSelect(row, priceList) {
  const normalized = normalizePriceListName(priceList)
  if (priceList === CUSTOM_PRICE_LIST) {
    updateLocal(row, "price_list", CUSTOM_PRICE_LIST)
    return
  }
  updateLocal(row, "price_list", normalized)
  repriceRow(row, normalized)
}

function openOverrideList(row) {
  overrideListOpenRow.value = row?.name || null
  if (!overrideOutsideHandler) {
    overrideOutsideHandler = (event) => {
      if (!event.target?.closest?.(".override-picker")) {
        closeOverrideList()
      }
    }
    document.addEventListener("click", overrideOutsideHandler)
  }
}

function closeOverrideList() {
  overrideListOpenRow.value = null
  if (overrideOutsideHandler) {
    document.removeEventListener("click", overrideOutsideHandler)
    overrideOutsideHandler = null
  }
}

async function setOverrideFromPriceList(row, priceList) {
  const normalized = normalizePriceListName(priceList)
  if (!normalized || isRowSaving(row)) return
  await withRowSaving(row.name, async () => {
    try {
      const resp = await frappe.call("do_derma.api.get_procedure_price", {
        procedure_name: row.name,
        price_list: normalized,
      })
      const rate = resp?.message?.rate
      updateLocal(row, "price", rate)
      updateLocal(row, "no_charge", false)
      updateLocal(row, "price_override_reason", "")
      await saveRow(row, { silent: true })
    } catch (err) {
      frappe.show_alert({ message: __("Could not fetch price."), indicator: "red" })
      // eslint-disable-next-line no-console
      console.warn("Failed to fetch override price", err)
    } finally {
      closeOverrideList()
    }
  })
}

onBeforeUnmount(() => {
  if (overrideOutsideHandler) {
    document.removeEventListener("click", overrideOutsideHandler)
    overrideOutsideHandler = null
  }
})

async function repriceRow(row, priceList) {
  if (!isPersistedRow(row)) {
    updateLocal(row, "price_list", normalizePriceListName(priceList) || CUSTOM_PRICE_LIST)
    return
  }
  if (isRowSaving(row)) return
  await withRowSaving(row.name, async () => {
    try {
      const resp = await frappe.call("do_derma.api.get_procedure_price", {
        procedure_name: row.name,
        price_list: normalizePriceListName(priceList),
      })
      const rate = resp?.message?.rate
      updateLocal(row, "price", rate)
      updateLocal(row, "price_list", normalizePriceListName(priceList))
      updateLocal(row, "no_charge", false)
      updateLocal(row, "price_override_reason", "")
      row.base_rate = rate
      await saveRow(row)
    } catch (err) {
      frappe.show_alert({ message: __("Could not fetch price."), indicator: "red" })
      // eslint-disable-next-line no-console
      console.warn("Failed to fetch price", err)
    }
  })
}

// Client row keys -> Clinical Procedure fieldnames (do_derma custom fields,
// created by schema.py). The note rides on the core `notes` field, which do_derma's
// property setter unlocks so an edit after insert lands instead of throwing.
const PROCEDURE_UPDATE_FIELD_MAP = {
  price_override: "custom_derma_price_override",
  price_list: "custom_derma_price_list",
  no_charge: "custom_derma_no_charge",
  price_override_reason: "custom_derma_price_override_reason",
  notes: "notes",
}

/** Resolves true when the row is persisted (or there was nothing to save), false on failure. */
function saveRow(row, opts = {}) {
  if (!isPersistedRow(row)) return Promise.resolve(false)
  return withRowSaving(row.name, () => writeRow(row, opts))
}

function isRowSaving(row) {
  return Boolean(savingRows.value[row?.name])
}

/**
 * Counted, because a reprice wraps this around the save that wraps it again. Both ends read
 * the live count: two saves of one row can overlap - a price edit still in flight when Reset
 * is clicked - and writing back a depth captured on the way in left the count above zero for
 * good, so the row span forever and refused every later reprice.
 */
async function withRowSaving(name, action) {
  savingRows.value = { ...savingRows.value, [name]: (savingRows.value[name] || 0) + 1 }
  try {
    return await action()
  } finally {
    const remaining = Math.max((savingRows.value[name] || 1) - 1, 0)
    savingRows.value = { ...savingRows.value, [name]: remaining }
  }
}

function writeRow(row, opts) {
  const payload = edits.value[row.name] || {}
  const updates = {}
  if (payload.price !== undefined) {
    const parsedPrice = payload.price === "" || payload.price === null ? null : Number(payload.price)
    if (parsedPrice !== null && !Number.isNaN(parsedPrice)) {
      // Clear override if it matches base rate
      const base = row.base_rate !== undefined ? Number(row.base_rate) : null
      if (parsedPrice === 0 && payload.no_charge) {
        updates.price_override = 0
      } else if (parsedPrice === 0) {
        updates.price_override = null
      } else if (base !== null && !Number.isNaN(base) && parsedPrice === base) {
        updates.price_override = null
      } else {
        updates.price_override = parsedPrice
      }
    } else if (parsedPrice === null) {
      updates.price_override = null
    }
  }
  if (payload.price_list !== undefined) {
    updates.price_list =
      payload.price_list === CUSTOM_PRICE_LIST ? "Custom" : normalizePriceListName(payload.price_list)
  }
  if (payload.no_charge !== undefined) updates.no_charge = payload.no_charge ? 1 : 0
  if (payload.price_override_reason !== undefined) {
    updates.price_override_reason = String(payload.price_override_reason || "")
  }
  if (payload.notes !== undefined) updates.notes = String(payload.notes || "")
  if (!Object.keys(updates).length) return Promise.resolve(true)

  const serverUpdates = Object.fromEntries(
    Object.entries(updates).map(([key, value]) => [PROCEDURE_UPDATE_FIELD_MAP[key] || key, value])
  )
  return frappe
    .call("do_derma.api.update_clinical_procedure_fields", {
      procedure_name: row.name,
      updates: serverUpdates,
    })
    .then((resp) => {
      Object.assign(row, {
        price_override: updates.price_override !== undefined ? updates.price_override : row.price_override,
        no_charge: updates.no_charge !== undefined ? Boolean(updates.no_charge) : row.no_charge,
        price_override_reason:
          updates.price_override_reason !== undefined ? updates.price_override_reason : row.price_override_reason,
        display_price:
          updates.price_override !== undefined
            ? updates.price_override === null
              ? row.base_rate
              : updates.price_override
            : displayPrice(row),
        price_list: updates.price_list !== undefined ? updates.price_list : row.price_list,
        price_source:
          updates.price_override !== undefined
            ? updates.price_override === null
              ? "Price List"
              : updates.no_charge
                ? "No charge"
                : "Override"
            : row.price_source,
        notes: updates.notes !== undefined ? updates.notes : row.notes,
      })
      edits.value[row.name] = {}
      if (updates.price_override !== undefined && typeof window !== "undefined") {
        const syncInfo = resp?.message?.billing_sync || {}
        const appointment = syncInfo?.appointment || row.appointment || null
        if (appointment) {
          window.dispatchEvent(
            new CustomEvent("do_health:appointment_billing_needs_refresh", {
              detail: {
                appointment,
                source: "chart_procedure_override",
              },
            })
          )
        }
      }
      if (!opts.silent) {
        frappe.show_alert({ message: __("Updated."), indicator: "green" })
      }
      return true
    })
    .catch((err) => {
      if (!opts.silent) {
        frappe.show_alert({ message: __("Could not update."), indicator: "red" })
      }
      // eslint-disable-next-line no-console
      console.warn("Failed to update procedure row", err)
      return false
    })
}

function formatCurrency(value, currency = null) {
  if (value === undefined || value === null || value === "") return ""
  const amount = Number(value)
  if (!Number.isFinite(amount)) return value
  return new Intl.NumberFormat(undefined, {
    style: currency ? "currency" : "decimal",
    currency: currency || "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(amount)
}

function formatGroupLabel(group) {
  const first = group?.items?.[0]
  const dateValue = group?.procedure_date || first?.procedure_date || first?.date || first?.start_date || ""
  if (!dateValue) return __("No date")
  try {
    return frappe.datetime.str_to_user(dateValue)
  } catch (err) {
    return dateValue
  }
}

function resetPrice(row) {
  updateLocal(row, "price", "0")
  saveRow(row)
}

function statusClass(status) {
  const key = (status || "draft").toString().toLowerCase()
  return STATUS_COLORS[key] || STATUS_COLORS.draft
}

function labCaseStatusClass(status) {
  const key = (status || "").toString().toLowerCase()
  if (["delivered", "closed"].includes(key)) return "pill-completed"
  if (["cancelled"].includes(key)) return "pill-cancelled"
  if (["ready for delivery", "quality checked", "received in clinic"].includes(key)) return "pill-in-progress"
  if (["sent", "in production", "received by lab", "shipped"].includes(key)) return "pill-pending"
  return "pill-draft"
}

function formatSurfaceText(row) {
  const list = edits.value[row.name]?.surfaces || row.surfaces || []
  return list.map((s) => s.surface || s).join(", ")
}

function rowAllowsSurfaces(row) {
  const profile = String(row.surface_profile || "").toLowerCase()
  if (!profile || profile === "none") return false
  const style = String(row.render_style || "").toLowerCase()
  return !["crown", "implant", "extraction", "outline", "prosthesis"].includes(style)
}

function deleteRow(row) {
  if (!row?.name) return
  const procedure = row.clinical_procedure || row.name
  const label = __("Delete the procedure with its marks and drawings?")
  frappe.confirm(label, () => {
    const doctype = "Clinical Procedure"
    const name = procedure
    frappe
      .call("do_derma.api.delete_clinical_procedure_entry", { doctype, name })
      .then(() => {
        frappe.show_alert({ message: __("Deleted."), indicator: "green" })
        emit("refresh")
      })
      .catch((err) => {
        frappe.show_alert({ message: __("Could not delete."), indicator: "red" })
        // eslint-disable-next-line no-console
        console.warn("Failed to delete procedure row", err)
      })
  })
}

function getProcedureName(row) {
  const name = row?.clinical_procedure || row?.name
  if (!name || String(name).startsWith("local-")) return ""
  return name
}

function annotateLabel(row) {
  const count = Number(row?.annotation_count || 0)
  return count ? `${__("Annotate")} (${count})` : __("Annotate")
}

function openProcedure(row) {
  const procedureName = getProcedureName(row)
  if (!procedureName) return
  frappe.msgprint({
    title: row?.procedure_template || row?.template_label || __("Clinical Procedure"),
    message: [
      `<p><b>${__("Procedure")}:</b> ${escapeHtml(procedureName)}</p>`,
      row?.status ? `<p><b>${__("Status")}:</b> ${escapeHtml(row.status)}</p>` : "",
      row?.notes ? `<p>${escapeHtml(row.notes)}</p>` : "",
    ].filter(Boolean).join(""),
    indicator: "blue",
  })
}

function handleRowDoubleClick(row, event) {
  const target = event?.target
  if (
    target?.closest?.(
      "input, textarea, select, button, a, .override-dropdown, .note-cell, .surface-cell .clickable, .lab-case-cell"
    )
  ) {
    return
  }
  openProcedure(row)
}

</script>

<style scoped>
.dental-chart-page .procedure-panel {
  background: #fff;
  border-radius: 14px;
  border: 1px solid #e5e7eb;
  padding: 14px;
  box-shadow: 0 10px 28px rgba(15, 23, 42, 0.05);
}

.dental-chart-page .procedure-primary-toolbar {
  display: grid;
  grid-template-columns: minmax(300px, 1fr) minmax(140px, 0.32fr) auto auto auto;
  gap: 8px;
  align-items: center;
  margin-bottom: 10px;
}

.dental-chart-page .status-filter-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  margin-bottom: 10px;
}

.dental-chart-page .status-chip {
  border: 1px solid #dbe4f0;
  background: #f8fafc;
  color: #475569;
  border-radius: 999px;
  padding: 6px 12px;
  font-size: 12px;
  font-weight: 800;
  cursor: pointer;
}

.dental-chart-page .status-chip.active {
  background: #eef4ff;
  border-color: #93c5fd;
  color: #1d4ed8;
  box-shadow: inset 0 0 0 1px rgba(37, 99, 235, 0.08);
}

.dental-chart-page .proc-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.dental-chart-page .proc-tabs button {
  border: 1px solid #e5e7eb;
  background: #f9fafb;
  border-radius: 8px;
  padding: 6px 10px;
  font-weight: 600;
  cursor: pointer;
}

.dental-chart-page .proc-tabs button.active {
  background: #e0e7ff;
  border-color: #c7d2fe;
  color: #1d4ed8;
}

.dental-chart-page .panel-actions {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: flex-end;
}

.dental-chart-page .panel-actions .history-load-selector {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #475467;
  font-size: 12px;
  font-weight: 700;
}

.dental-chart-page .panel-actions .history-load-selector select {
  border: 1px solid #d1d5db;
  background: #fff;
  border-radius: 8px;
  padding: 4px 8px;
  font-size: 12px;
}

.dental-chart-page .panel-actions .badge.anesthesia-badge {
  background: #e0f2fe;
  color: #0369a1;
  border-radius: 10px;
  padding: 6px 10px;
  font-weight: 700;
  font-size: 12px;
}
.dental-chart-page .panel-actions .badge.read-only-badge {
  background: #eff6ff;
  color: #1e3a8a;
  border-radius: 10px;
  padding: 6px 10px;
  font-weight: 700;
  font-size: 12px;
}

.dental-chart-page .panel-actions .ghost {
  border: 1px solid #d1d5db;
  background: #f8fafc;
  border-radius: 10px;
  padding: 6px 10px;
  cursor: pointer;
}

.dental-chart-page .panel-actions .session-badge {
  background: #e0f2fe;
  color: #0369a1;
  border-radius: 12px;
  padding: 6px 10px;
  font-weight: 700;
}

.dental-chart-page .procedure-history-controls {
  display: grid;
  grid-template-columns: repeat(6, minmax(120px, 1fr));
  gap: 8px;
  align-items: end;
  margin-bottom: 12px;
  padding: 10px;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  background: #f8fafc;
}

.dental-chart-page .history-search,
.dental-chart-page .history-filter-control {
  border: 1px solid #dbe4f0;
  background: #fff;
  border-radius: 8px;
  min-height: 40px;
}

.dental-chart-page .history-search {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 10px;
  min-width: 0;
}

.dental-chart-page .history-search i {
  color: #64748b;
  font-size: 12px;
}

.dental-chart-page .history-search input {
  width: 100%;
  min-width: 0;
  border: 0;
  outline: none;
  font-size: 13px;
  background: transparent;
}

.dental-chart-page .history-filter-control {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 5px 8px;
}

.dental-chart-page .history-filter-control span {
  color: #64748b;
  font-size: 10px;
  font-weight: 800;
  line-height: 1;
  text-transform: uppercase;
}

.dental-chart-page .history-filter-control select {
  width: 100%;
  min-width: 0;
  border: 0;
  outline: none;
  background: transparent;
  color: #0f172a;
  font-size: 12px;
  font-weight: 700;
  padding: 0;
}

.dental-chart-page .compact-sort {
  min-width: 140px;
}

.dental-chart-page .filter-toggle-btn {
  min-height: 40px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  border: 1px solid #d1d5db;
  background: #fff;
  color: #334155;
  border-radius: 8px;
  padding: 0 12px;
  font-size: 12px;
  font-weight: 800;
  cursor: pointer;
}

.dental-chart-page .filter-toggle-btn.active {
  border-color: #93c5fd;
  color: #1d4ed8;
  background: #eff6ff;
}

.dental-chart-page .filter-toggle-btn strong {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  border-radius: 999px;
  background: #2563eb;
  color: #fff;
  font-size: 11px;
  line-height: 1;
}

.dental-chart-page .clear-filters-btn {
  min-height: 40px;
  border: 1px solid #d1d5db;
  background: #f8fafc;
  border-radius: 8px;
  padding: 0 12px;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}

.dental-chart-page .procedure-history-summary {
  display: grid;
  grid-template-columns: repeat(5, minmax(110px, 1fr));
  gap: 8px;
  margin-bottom: 12px;
}

.dental-chart-page .summary-tile {
  border: 1px solid #e2e8f0;
  background: #f8fafc;
  border-radius: 8px;
  padding: 8px 10px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-width: 0;
}

.dental-chart-page .summary-tile span {
  color: #64748b;
  font-size: 11px;
  font-weight: 800;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dental-chart-page .summary-tile strong {
  color: #0f172a;
  font-size: 16px;
  line-height: 1;
}

.dental-chart-page .summary-tile.attention {
  background: #fff7ed;
  border-color: #fed7aa;
}

.dental-chart-page .summary-tile.attention strong {
  color: #c2410c;
}

.dental-chart-page .procedure-table-wrapper {
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  overflow: auto;
  max-height: 62vh;
}

.dental-chart-page .procedure-load-more-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 10px 12px;
  border-top: 1px solid #e5e7eb;
  background: #f8fafc;
}

.dental-chart-page .procedure-load-more-row .text-muted {
  color: #64748b;
  font-size: 12px;
  font-weight: 600;
}

.dental-chart-page .procedure-load-more-row .ghost.small {
  border: 1px solid #d1d5db;
  background: #fff;
  border-radius: 8px;
  padding: 4px 10px;
  font-size: 12px;
  cursor: pointer;
}

/* Below this the eight columns cannot all hold their content, so the wrapper
   scrolls the whole table rather than clipping the Actions column off the end. */
.dental-chart-page .procedure-table {
  width: 100%;
  min-width: 860px;
  border-collapse: collapse;
  table-layout: fixed;
}

.dental-chart-page .procedure-table .col-status {
  width: 8%;
}

.dental-chart-page .procedure-table .col-procedure {
  width: 21%;
}

.dental-chart-page .procedure-table .col-tooth {
  width: 7%;
}

.dental-chart-page .procedure-table .col-details {
  width: 22%;
}

.dental-chart-page .procedure-table .col-price {
  width: 8%;
}

.dental-chart-page .procedure-table .col-notes {
  width: 12%;
}

.dental-chart-page .procedure-table .col-doctor {
  width: 12%;
}

/* Two icon buttons plus their count badge: never less than ~86px. */
.dental-chart-page .procedure-table .col-actions {
  width: 10%;
}

.dental-chart-page .procedure-table th {
  background: #f9fafb;
  font-weight: 700;
  padding: 10px;
  border-bottom: 1px solid #e5e7eb;
  text-align: left;
  font-size: 13px;
  position: sticky;
  top: 0;
  z-index: 1;
}

.dental-chart-page .procedure-table .price-list-meta {
  font-size: 11px;
  color: #6b7280;
  margin-top: 4px;
}

.dental-chart-page .procedure-table .price-meta {
  font-size: 11px;
  color: #6b7280;
  margin-top: 4px;
}

.dental-chart-page .procedure-table .price-readonly {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.dental-chart-page .procedure-table .price-source {
  font-size: 11px;
  color: #2563eb;
  background: #e0e7ff;
  padding: 2px 6px;
  border-radius: 999px;
  width: fit-content;
}


.dental-chart-page .procedure-table .surface-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}

.dental-chart-page .procedure-table .tooth-cell {
  color: #475569;
  font-weight: 700;
}

.dental-chart-page .procedure-table .details-cell {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  min-width: 0;
}

.dental-chart-page .procedure-table .detail-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  max-width: 100%;
  min-height: 24px;
  border: 1px solid #dbe4f0;
  background: #f8fafc;
  color: #334155;
  border-radius: 999px;
  padding: 3px 8px;
  font-size: 11px;
  font-weight: 800;
  line-height: 1.2;
}

.dental-chart-page .procedure-table .detail-chip span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dental-chart-page .procedure-table .detail-chip i {
  flex: 0 0 auto;
  color: #64748b;
  font-size: 10px;
}

.dental-chart-page .procedure-table .detail-chip.muted {
  color: #64748b;
  background: #f8fafc;
}

.dental-chart-page .procedure-table .detail-chip-button {
  cursor: pointer;
}

.dental-chart-page .procedure-table .detail-chip-button:disabled {
  cursor: default;
  opacity: 0.8;
}

.dental-chart-page .procedure-table .derma-detail-chip {
  max-width: 280px;
  border-color: #99f6e4;
  background: #f0fdfa;
  color: #115e59;
  cursor: pointer;
}

.dental-chart-page .procedure-table .derma-artifact-chip {
  border-color: #bfdbfe;
  background: #eff6ff;
  color: #1d4ed8;
}

.dental-chart-page .procedure-table .lab-suggested {
  border-color: #fed7aa;
  background: #fff7ed;
  color: #c2410c;
}

.dental-chart-page .procedure-table .override-label {
  border-color: #bfdbfe;
  background: #eff6ff;
  color: #1d4ed8;
}

.dental-chart-page .procedure-table .note-cell {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.dental-chart-page .procedure-table .note-dialog-btn {
  width: fit-content;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.dental-chart-page .procedure-table .note-presence-indicator {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  width: fit-content;
  font-size: 11px;
  font-weight: 700;
  color: #64748b;
  background: #f1f5f9;
  border: 1px solid #dbe4f0;
  border-radius: 999px;
  padding: 3px 8px;
}

.dental-chart-page .procedure-table .note-presence-indicator i {
  font-size: 8px;
  color: #94a3b8;
}

.dental-chart-page .procedure-table .note-presence-indicator.present {
  color: #166534;
  background: #ecfdf3;
  border-color: #bbf7d0;
}

.dental-chart-page .procedure-table .note-presence-indicator.present i {
  color: #16a34a;
}

.dental-chart-page .procedure-table .note-readonly-cell {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.dental-chart-page .procedure-table .note-view-btn {
  width: fit-content;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.dental-chart-page .procedure-table .lab-case-cell {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.dental-chart-page .procedure-table .surface-cell .clickable {
  cursor: pointer;
  color: #2563eb;
  font-weight: 600;
}

.dental-chart-page .procedure-table .ghost.small {
  border: 1px solid #d1d5db;
  background: #f8fafc;
  border-radius: 8px;
  padding: 4px 8px;
  font-size: 12px;
  cursor: pointer;
}

.dental-chart-page .procedure-table .ghost.small.danger {
  border-color: #fecaca;
  background: #fff1f2;
  color: #b91c1c;
}

.dental-chart-page .procedure-table td.row-actions {
  white-space: nowrap;
  padding-left: 6px;
  padding-right: 6px;
}

.dental-chart-page .procedure-table .icon-btn {
  position: relative;
  border: 1px solid #d1d5db;
  background: #f8fafc;
  border-radius: 8px;
  padding: 5px 7px;
  font-size: 13px;
  color: #334155;
  cursor: pointer;
}

.dental-chart-page .procedure-table .icon-btn + .icon-btn {
  margin-left: 4px;
}

.dental-chart-page .procedure-table .icon-btn:hover {
  border-color: #94a3b8;
  background: #f1f5f9;
}

.dental-chart-page .procedure-table .icon-btn.danger {
  border-color: #fecaca;
  background: #fff1f2;
  color: #b91c1c;
}

.dental-chart-page .procedure-table .icon-btn .icon-badge {
  position: absolute;
  top: -6px;
  right: -6px;
  min-width: 15px;
  height: 15px;
  padding: 0 3px;
  border-radius: 999px;
  background: #087b75;
  color: #ffffff;
  font-size: 10px;
  font-weight: 800;
  line-height: 15px;
  text-align: center;
}

.dental-chart-page .procedure-table td {
  padding: 10px;
  border-bottom: 1px solid #f1f5f9;
  font-size: 13px;
}

.dental-chart-page .procedure-table .procedure-code {
  font-size: 11px;
  font-weight: 700;
  color: #1f2937;
  background: #e5e7eb;
  padding: 2px 6px;
  border-radius: 999px;
  margin-right: 6px;
  display: inline-flex;
  align-items: center;
}

.dental-chart-page .procedure-table .procedure-open-link {
  border: 0;
  background: transparent;
  padding: 0;
  margin: 0;
  color: #0f172a;
  text-decoration: none;
  cursor: pointer;
  font: inherit;
  font-weight: 600;
  border-bottom: 1px solid transparent;
  line-height: 1.3;
  transition: color 120ms ease, border-color 120ms ease;
}

.dental-chart-page .procedure-table .procedure-open-link:hover {
  color: #1d4ed8;
  border-bottom-color: #bfdbfe;
}

.dental-chart-page .procedure-table .procedure-open-link:focus-visible {
  outline: none;
  color: #1d4ed8;
  border-bottom-color: #1d4ed8;
}

.dental-chart-page .procedure-table .pill {
  display: inline-block;
  padding: 4px 8px;
  border-radius: 8px;
  font-weight: 700;
  font-size: 12px;
  color: #111827;
  background: #f3f4f6;
  border: 1px solid #e5e7eb;
}

.dental-chart-page .procedure-table .pill-draft {
  background: #e5e7eb;
  color: #374151;
  border-color: #d1d5db;
}

.dental-chart-page .procedure-table .pill-pending {
  background: #fef3c7;
  color: #92400e;
  border-color: #fcd34d;
}

.dental-chart-page .procedure-table .pill-in-progress {
  background: #dbeafe;
  color: #1e40af;
  border-color: #bfdbfe;
}

.dental-chart-page .procedure-table .pill-submitted {
  background: #eef2ff;
  color: #3730a3;
  border-color: #c7d2fe;
}

.dental-chart-page .procedure-table .pill-completed {
  background: #dcfce7;
  color: #166534;
  border-color: #bbf7d0;
}

.dental-chart-page .procedure-table .pill-cancelled {
  background: #fee2e2;
  color: #991b1b;
  border-color: #fecaca;
}

.dental-chart-page .procedure-table .inline-input {
  width: 100%;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  padding: 6px 8px;
  font-size: 12px;
}

.dental-chart-page .procedure-table .price-edit {
  display: flex;
  align-items: center;
  gap: 6px;
}

.dental-chart-page .procedure-table .price-edit.price-list-edit {
  margin-top: 6px;
}

.dental-chart-page .procedure-table .override-picker {
  position: relative;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  min-width: 220px;
}

.dental-chart-page .procedure-table .override-picker.compact {
  min-width: 0;
  flex: 1 1 170px;
}

.dental-chart-page .procedure-table .override-picker .inline-input {
  flex: 1 1 90px;
}

.dental-chart-page .procedure-table .override-dropdown {
  position: absolute;
  top: 100%;
  left: 0;
  min-width: 100%;
  width: 100%;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.12);
  padding: 6px;
  z-index: 10;
}

.dental-chart-page .procedure-table .override-option {
  display: block;
  width: 100%;
  text-align: left;
  padding: 6px 8px;
  border: none;
  background: transparent;
  border-radius: 6px;
  cursor: pointer;
  font-size: 12px;
}

.dental-chart-page .procedure-table .override-option:hover {
  background: #f1f5f9;
}

.dental-chart-page .procedure-table .ghost.small {
  border: 1px solid #d1d5db;
  background: #f8fafc;
  border-radius: 8px;
  padding: 6px 10px;
  font-size: 12px;
  cursor: pointer;
}

.dental-chart-page .procedure-table .reset-btn {
  flex: 0 0 auto;
}

.dental-chart-page .procedure-table .no-charge-btn {
  flex: 0 0 auto;
  border-color: #bfdbfe;
  color: #1d4ed8;
  background: #eff6ff;
}

.dental-chart-page .procedure-table .no-charge-btn.active,
.dental-chart-page .procedure-table .no-charge-label {
  border-color: #bbf7d0;
  color: #166534;
  background: #dcfce7;
  font-weight: 700;
}

.dental-chart-page .procedure-table .no-charge-label {
  display: inline-flex;
  align-items: center;
  border: 1px solid #bbf7d0;
  border-radius: 999px;
  padding: 6px;
  line-height: 1;
}

.dental-chart-page .procedure-table .insurance-locked-label {
  display: inline-flex;
  align-items: center;
  border: 1px solid #bfdbfe;
  border-radius: 999px;
  color: #1d4ed8;
  background: #eff6ff;
  padding: 6px;
  line-height: 1;
  font-weight: 700;
}

.dental-chart-page .procedure-date-row td {
  background: #f8fafc;
  color: #334155;
  padding: 12px 14px 8px;
  font-size: 16px;
  font-weight: 800;
  border-bottom: 1px solid #e5e7eb;
}

.dental-chart-page .procedure-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 22px 16px;
  text-align: center;
  color: #6b7280;
  border: 1px dashed #cbd5e1;
  border-radius: 12px;
  background: #f8fafc;
}

.dental-chart-page .procedure-empty strong {
  color: #0f172a;
  font-size: 14px;
}

.dental-chart-page .procedure-empty span {
  max-width: 520px;
  font-size: 13px;
  line-height: 1.45;
}

.dental-chart-page .procedure-empty .ghost.small {
  border: 1px solid #d1d5db;
  background: #fff;
  border-radius: 8px;
  padding: 6px 10px;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}

.dental-chart-page .invoice-footer {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
  padding-top: 12px;
}

.dental-chart-page .invoice-btn {
  background: #16a34a;
  color: #fff;
  border: none;
  border-radius: 10px;
  padding: 10px 16px;
  font-weight: 700;
  cursor: pointer;
  transition: opacity 120ms ease, background 120ms ease;
}

.dental-chart-page .invoice-btn.disabled,
.dental-chart-page .invoice-btn:disabled {
  background: #9ca3af;
  cursor: not-allowed;
  opacity: 0.7;
}

.dental-chart-page .invoice-btn.ghost {
  background: #f8fafc;
  border: 1px solid #cbd5e1;
  color: #334155;
}

.dental-chart-page .invoice-btn.complete {
  background: #2563eb;
}

:global(.procedure-note-dialog .modal-dialog) {
  width: min(980px, calc(100vw - 32px));
  max-width: 980px;
}

:global(.procedure-note-dialog .modal-content) {
  border: 1px solid #dbe4f0;
  border-radius: 14px;
  overflow: hidden;
}

:global(.procedure-note-dialog .modal-header) {
  background: linear-gradient(135deg, #f8fafc, #eef2ff);
  border-bottom: 1px solid #dbe4f0;
}

:global(.procedure-note-dialog .modal-footer) {
  border-top: 1px solid #e2e8f0;
  background: #fcfdff;
}

:global(.procedure-note-dialog .frappe-control[data-fieldname="note"] .ql-editor) {
  min-height: 230px;
}

:global(.procedure-note-dialog__template-preview) {
  border: 1px solid #dbe4f0;
  border-radius: 10px;
  background: #f8fafc;
  overflow: hidden;
}

:global(.procedure-note-dialog__template-preview-rendered) {
  padding: 10px 12px;
  background: #fff;
  border-bottom: 1px solid #e2e8f0;
  max-height: 180px;
  overflow: auto;
}

:global(.procedure-note-dialog__template-preview-plain) {
  padding: 10px 12px;
  color: #64748b;
  font-size: 12px;
  line-height: 1.45;
  max-height: 100px;
  overflow: auto;
  white-space: pre-wrap;
}

:global(.procedure-note-dialog__related-title) {
  margin-top: 4px;
  margin-bottom: 8px;
  font-weight: 700;
  color: #0f172a;
  font-size: 13px;
  letter-spacing: 0.01em;
}

:global(.procedure-note-dialog__loading),
:global(.procedure-note-dialog__empty) {
  border: 1px dashed #cbd5e1;
  border-radius: 10px;
  padding: 12px;
  background: #f8fafc;
  color: #64748b;
  font-size: 12px;
}

:global(.procedure-note-dialog__history-list) {
  display: grid;
  gap: 8px;
  max-height: 260px;
  overflow: auto;
  padding-right: 2px;
}

:global(.procedure-note-dialog__history-item) {
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 10px;
  background: #f8fafc;
}

:global(.procedure-note-dialog__history-title) {
  font-size: 12px;
  font-weight: 700;
  color: #0f172a;
  margin-bottom: 3px;
}

:global(.procedure-note-dialog__history-meta) {
  font-size: 11px;
  color: #64748b;
  margin-bottom: 6px;
}

:global(.procedure-note-dialog__history-body) {
  font-size: 12px;
  line-height: 1.45;
  color: #334155;
  white-space: pre-wrap;
}

@media (max-width: 768px) {
  .dental-chart-page .procedure-primary-toolbar {
    grid-template-columns: 1fr;
  }

  .dental-chart-page .panel-actions {
    justify-content: flex-start;
    flex-wrap: wrap;
  }

  .dental-chart-page .procedure-history-controls {
    grid-template-columns: 1fr 1fr;
  }

  .dental-chart-page .history-search {
    grid-column: 1 / -1;
  }

  .dental-chart-page .clear-filters-btn {
    grid-column: 1 / -1;
  }

  .dental-chart-page .procedure-history-summary {
    grid-template-columns: 1fr 1fr;
  }

  :global(.procedure-note-dialog .modal-dialog) {
    width: calc(100vw - 12px);
    margin: 6px auto;
  }

  :global(.procedure-note-dialog .frappe-control[data-fieldname="note"] .ql-editor) {
    min-height: 170px;
  }
}
</style>
