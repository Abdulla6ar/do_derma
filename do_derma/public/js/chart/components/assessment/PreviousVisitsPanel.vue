<template>
  <section
    v-if="visits.length || hasMore || error"
    ref="panel"
    class="chart-annotation-history previous-visits"
    data-test="previous-visits"
  >
    <header>
      <div>
        <strong>{{ __("Previous Visits") }}</strong>
        <small>{{ __("Drawings and assessment from earlier visits") }}</small>
      </div>
    </header>
    <article v-for="visit in visits" :key="visit.encounter" class="previous-visit" data-test="previous-visit">
      <header>
        <b>{{ formatDate(visit.visit_date) }}</b>
        <small>{{ visit.practitioner_name }}</small>
        <button
          type="button"
          class="ghost small"
          data-test="previous-visit-summary"
          @click="summaryEncounter = visit.encounter"
        >
          {{ __("View Summary") }}
        </button>
      </header>
      <div v-if="visit.drawings.length" class="chart-annotation-list">
        <div v-for="drawing in visit.drawings" :key="drawing.name" class="chart-annotation-card">
          <button type="button" @click="$emit('open-drawing', drawing)">
            <span class="chart-annotation-preview">
              <img
                v-if="previewOf(drawing) && !isBroken(previewOf(drawing))"
                :src="previewOf(drawing)"
                :alt="labelOf(drawing)"
                loading="lazy"
                @error="markBroken(previewOf(drawing))"
              />
              <span v-else>{{ __("No preview") }}</span>
            </span>
            <b>{{ labelOf(drawing) }}</b>
          </button>
        </div>
      </div>
      <dl
        v-if="visit.assessment.length"
        class="previous-visit-assessment"
        :class="{ 'is-expanded': expanded.has(visit.encounter) }"
      >
        <template v-for="(field, index) in shownFields(visit)" :key="`${field.label}-${index}`">
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
    <p v-if="!visits.length && hasMore && !error" class="panel-muted">
      {{ __("None in the most recent visits.") }}
    </p>
    <p v-if="error" class="panel-muted" role="alert">
      {{ error }}
      <button type="button" class="ghost small" @click="loadPage">{{ __("Retry") }}</button>
    </p>
    <div v-if="(hasMore && !error) || isCollapsible" class="previous-visits-actions">
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
      <button
        v-if="isCollapsible"
        type="button"
        class="ghost small"
        data-test="previous-visits-collapse"
        @click="collapse"
      >
        {{ __("Collapse") }}
      </button>
    </div>
    <VisitSummaryDialog
      :encounter="summaryEncounter"
      :preview-of="previewOf"
      :label-of="labelOf"
      :format-date="formatDate"
      @close="summaryEncounter = ''"
      @open-drawing="$emit('open-drawing', $event)"
    />
  </section>
</template>

<script setup>
import { computed, reactive, ref, watch } from "vue"
import { serverErrorText } from "../../../shared/error_text.js"
import { useBrokenImages } from "../../../shared/broken_images.js"
import VisitSummaryDialog from "./VisitSummaryDialog.vue"

const __ = window.__ || ((txt) => txt)
const PREVIEW_FIELDS = 3
const FIRST_PAGE = 1
const MORE_PAGE = 5

const props = defineProps({
  patient: { type: String, default: "" },
  currentEncounter: { type: String, default: "" },
  previewOf: { type: Function, required: true },
  labelOf: { type: Function, required: true },
  formatDate: { type: Function, required: true },
})
defineEmits(["open-drawing"])

const { isBroken, markBroken } = useBrokenImages()

const visits = ref([])
const hasMore = ref(false)
const loading = ref(false)
const error = ref("")
const summaryEncounter = ref("")
const expanded = reactive(new Set())
const panel = ref(null)
// Where the first page ended, so Collapse can return to it without refetching.
const firstPage = ref(null)
let nextStart = 0
let requestId = 0

const isCollapsible = computed(() => Boolean(firstPage.value) && visits.value.length > firstPage.value.count)

async function loadPage() {
  if (!props.patient || loading.value) return
  const request = ++requestId
  loading.value = true
  error.value = ""
  try {
    const { message } = await frappe.call({
      method: "do_derma.api.get_previous_visits",
      args: {
        patient: props.patient,
        current_encounter: props.currentEncounter,
        start: nextStart,
        page_length: nextStart ? MORE_PAGE : FIRST_PAGE,
      },
    })
    if (request !== requestId) return
    visits.value = [...visits.value, ...(message.visits || [])]
    hasMore.value = Boolean(message.has_more)
    nextStart = message.next_start
    firstPage.value ||= { count: visits.value.length, hasMore: hasMore.value, nextStart }
  } catch (err) {
    if (request !== requestId) return
    error.value = serverErrorText(err, __("Unable to load previous visits."))
  } finally {
    if (request === requestId) loading.value = false
  }
}

function collapse() {
  requestId++
  loading.value = false
  error.value = ""
  visits.value = visits.value.slice(0, firstPage.value.count)
  hasMore.value = firstPage.value.hasMore
  nextStart = firstPage.value.nextStart
  panel.value?.scrollIntoView({ block: "start", behavior: "smooth" })
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
    requestId++
    visits.value = []
    hasMore.value = false
    loading.value = false
    error.value = ""
    summaryEncounter.value = ""
    expanded.clear()
    firstPage.value = null
    nextStart = 0
    loadPage()
  },
  { immediate: true }
)
</script>
