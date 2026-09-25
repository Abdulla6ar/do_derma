<template>
  <div v-if="encounter" class="visit-summary-backdrop" data-test="visit-summary-backdrop" @click.self="$emit('close')">
    <section
      ref="dialog"
      class="visit-summary-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="visit-summary-title"
      tabindex="-1"
      data-test="visit-summary"
    >
      <header>
        <div>
          <strong id="visit-summary-title">{{ title }}</strong>
          <small v-if="summary?.practitioner_name">{{ summary.practitioner_name }}</small>
        </div>
        <button type="button" class="ghost small" data-test="visit-summary-close" @click="$emit('close')">
          {{ __("Close") }}
        </button>
      </header>
      <div class="visit-summary-body">
        <p v-if="loading" class="panel-muted" data-test="visit-summary-loading">
          <span class="chart-spinner" aria-hidden="true"></span>
          {{ __("Loading visit summary...") }}
        </p>
        <p v-else-if="error" class="panel-muted" role="alert">
          {{ error }}
          <button type="button" class="ghost small" data-test="visit-summary-retry" @click="load">
            {{ __("Retry") }}
          </button>
        </p>
        <template v-else-if="summary">
          <p v-if="isEmpty" class="panel-muted" data-test="visit-summary-empty">
            {{ __("Nothing was documented for this visit.") }}
          </p>
          <section v-if="summary.assessment.length" data-test="visit-summary-assessment">
            <h4>{{ __("Assessment") }} <small>{{ summary.mode_label }}</small></h4>
            <dl class="visit-summary-fields">
              <template v-for="(field, index) in summary.assessment" :key="`${field.label}-${index}`">
                <dt>{{ field.label }}</dt>
                <dd v-if="field.rows">
                  <span v-for="(row, rowIndex) in field.rows" :key="rowIndex" class="visit-summary-row">
                    {{ rowText(row) }}
                  </span>
                </dd>
                <dd v-else>{{ field.value }}</dd>
              </template>
            </dl>
          </section>
          <section v-if="summary.patient_advice" data-test="visit-summary-advice">
            <h4>{{ __("Patient Advice") }}</h4>
            <p class="visit-summary-text">{{ summary.patient_advice }}</p>
          </section>
          <section v-if="summary.procedures.length" data-test="visit-summary-procedures">
            <h4>{{ __("Procedures") }}</h4>
            <ul class="visit-summary-list">
              <li v-for="procedure in summary.procedures" :key="procedure.name">
                <b>{{ procedure.title }}</b>
                <small>{{ [procedure.status, procedure.practitioner_name].filter(Boolean).join(" · ") }}</small>
                <p v-if="procedure.notes" class="visit-summary-text">{{ procedure.notes }}</p>
              </li>
            </ul>
          </section>
          <section v-if="summary.prescriptions.length" data-test="visit-summary-prescriptions">
            <h4>{{ __("Prescriptions") }}</h4>
            <ul class="visit-summary-list">
              <li v-for="(prescription, index) in summary.prescriptions" :key="`${prescription.drug}-${index}`">
                <b>{{ prescription.drug }}</b>
                <small>{{ [prescription.dosage, prescription.period].filter(Boolean).join(" · ") }}</small>
                <p v-if="prescription.comment" class="visit-summary-text">{{ prescription.comment }}</p>
              </li>
            </ul>
          </section>
          <section v-if="summary.drawings.length" data-test="visit-summary-drawings">
            <h4>{{ __("Drawings") }}</h4>
            <div class="chart-annotation-list">
              <div v-for="drawing in summary.drawings" :key="drawing.name" class="chart-annotation-card">
                <button type="button" data-test="visit-summary-drawing" @click="$emit('open-drawing', drawing)">
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
          </section>
        </template>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue"
import { serverErrorText } from "../../../shared/error_text.js"
import { useBrokenImages } from "../../../shared/broken_images.js"

const __ = window.__ || ((txt) => txt)

const props = defineProps({
  encounter: { type: String, default: "" },
  previewOf: { type: Function, required: true },
  labelOf: { type: Function, required: true },
  formatDate: { type: Function, required: true },
})
const emit = defineEmits(["close", "open-drawing"])

const { isBroken, markBroken } = useBrokenImages()

const dialog = ref(null)
const summary = ref(null)
const loading = ref(false)
const error = ref("")
let requestId = 0

const title = computed(() =>
  summary.value
    ? __("Visit Summary · {0}").replace("{0}", props.formatDate(summary.value.encounter_date))
    : __("Visit Summary")
)

const isEmpty = computed(() => {
  const { assessment, patient_advice, procedures, prescriptions, drawings } = summary.value
  return !assessment.length && !patient_advice && !procedures.length && !prescriptions.length && !drawings.length
})

/** Document-level, so Escape still closes once a drawing opened from here hands focus back to body. */
function onKeydown(event) {
  if (event.key !== "Escape" || !props.encounter || document.body.classList.contains("modal-open")) return
  emit("close")
}

onMounted(() => document.addEventListener("keydown", onKeydown))
onBeforeUnmount(() => document.removeEventListener("keydown", onKeydown))

function rowText(row) {
  return row.map((pair) => `${pair.label}: ${pair.value}`).join(" · ")
}

async function load() {
  const request = ++requestId
  loading.value = true
  error.value = ""
  try {
    const { message } = await frappe.call({
      method: "do_derma.api.get_visit_summary",
      args: { encounter: props.encounter },
    })
    if (request !== requestId) return
    summary.value = message
  } catch (err) {
    if (request !== requestId) return
    error.value = serverErrorText(err, __("Unable to load the visit summary."))
  } finally {
    if (request === requestId) loading.value = false
  }
}

watch(
  () => props.encounter,
  async (encounter) => {
    requestId++
    summary.value = null
    loading.value = false
    error.value = ""
    if (!encounter) return
    load()
    await nextTick()
    dialog.value?.focus()
  },
  { immediate: true }
)
</script>
