<template>
  <section class="ai-documents" data-test="ai-documents">
    <header>
      <div>
        <strong>{{ __("AI Documents") }}</strong>
        <small>{{ __("Drafted from this visit's note and transcript. PDF prints it on the clinic letterhead.") }}</small>
      </div>
      <div class="ai-documents-actions">
        <button
          v-for="kind in KINDS"
          :key="kind.key"
          type="button"
          class="ghost small"
          :disabled="busy !== ''"
          :data-test="`ai-doc-${kind.key}`"
          @click="generate(kind.key)"
        >
          <span v-if="busy === kind.key" class="voice-spinner" aria-hidden="true"></span>
          {{ kind.label }}
        </button>
      </div>
    </header>

    <p v-if="error" class="error-text">{{ error }}</p>
    <p v-else-if="!documents.length" class="ai-documents-empty">{{ __("No documents yet.") }}</p>

    <ul v-else class="ai-documents-list">
      <li v-for="doc in documents" :key="doc.name" :data-status="doc.status">
        <div class="ai-doc-head">
          <b>{{ doc.title }}</b>
          <span class="ai-doc-status">{{ doc.status }}</span>
          <small>{{ formatDate(doc.creation) }}</small>
          <span class="ai-doc-buttons">
            <button type="button" class="ghost small" @click="open(doc)">{{ __("Open") }}</button>
            <a v-if="doc.pdf_url" class="ghost small" :href="doc.pdf_url" target="_blank" rel="noopener">{{ __("PDF") }}</a>
            <button v-else-if="doc.docstatus === 0" type="button" class="primary small" :disabled="busy !== ''" data-test="ai-doc-pdf" @click="issueAndOpen(doc)">{{ __("PDF") }}</button>
          </span>
        </div>
        <details>
          <summary>{{ __("Preview text") }}</summary>
          <pre>{{ doc.body }}</pre>
        </details>
        <details v-if="doc.body_ar">
          <summary>{{ __("Arabic version") }}</summary>
          <pre dir="rtl">{{ doc.body_ar }}</pre>
        </details>
      </li>
    </ul>
  </section>
</template>

<script setup>
import { onMounted, ref, watch } from "vue"

const __ = window.__ || ((txt) => txt)

const KINDS = [
  { key: "report", label: __("Medical Report") },
  { key: "referral", label: __("Referral Letter") },
  { key: "education", label: __("Patient Education") },
  { key: "explainer", label: __("Patient Explainer") },
]

const props = defineProps({
  encounter: { type: String, default: "" },
})

const documents = ref([])
const busy = ref("")
const error = ref("")

onMounted(load)
watch(() => props.encounter, load)

async function load() {
  if (!props.encounter) return
  try {
    const response = await frappe.call({ method: "do_derma.documents.list_documents", args: { encounter: props.encounter } })
    documents.value = response.message || []
  } catch (err) {
    error.value = err?.message || __("Unable to load documents.")
  }
}

async function generate(kind) {
  const addressee = kind === "referral" ? await askAddressee() : ""
  if (addressee === null) return
  busy.value = kind
  error.value = ""
  try {
    const queued = await frappe.call({ method: "do_derma.documents.queue_document", args: { kind, encounter: props.encounter, addressee } })
    const doc = await waitForJob(queued.message?.job)
    documents.value = [doc, ...documents.value]
    frappe.show_alert({ message: __("Draft ready. Review it, then PDF."), indicator: "green" })
  } catch (err) {
    error.value = err?.message || __("The document could not be generated.")
  } finally {
    busy.value = ""
  }
}

async function waitForJob(job) {
  if (!job) throw new Error(__("The document job was not started."))
  for (let i = 0; i < 100; i++) {
    const status = await frappe.call({ method: "do_derma.voice.job_status", args: { job } })
    const m = status.message || {}
    if (m.status === "done") return m.result
    if (m.status === "failed") throw new Error(m.error || __("The document failed."))
    if (m.status === "unknown") throw new Error(__("The document job expired."))
    await new Promise((r) => setTimeout(r, 3000))
  }
  throw new Error(__("The AI is taking too long. Try again in a minute."))
}

function askAddressee() {
  return new Promise((resolve) => {
    if (!window.frappe?.prompt) return resolve("")
    let settled = false
    frappe.prompt(
      { fieldname: "addressee", fieldtype: "Data", label: __("Refer to (doctor / clinic)") },
      (values) => {
        settled = true
        resolve(values.addressee || "")
      },
      __("Referral Letter"),
      __("Draft")
    )
    // frappe.prompt has no cancel callback: a closed dialog resolves to null on the next tick.
    setTimeout(() => {
      const dialog = window.cur_dialog
      if (dialog) dialog.onhide = () => !settled && resolve(null)
    }, 0)
  })
}

// PDF on a draft issues it (the PDF only exists once issued), then opens it. The tab is
// opened before the await so the browser still treats it as the click's own pop-up.
async function issueAndOpen(doc) {
  const tab = window.open("", "_blank")
  busy.value = doc.name
  error.value = ""
  try {
    const response = await frappe.call({ method: "do_derma.documents.issue_document", args: { name: doc.name } })
    const issued = response.message
    documents.value = documents.value.map((row) => (row.name === doc.name ? issued : row))
    if (tab && issued?.pdf_url) tab.location.href = issued.pdf_url
    else tab?.close()
  } catch (err) {
    tab?.close()
    error.value = err?.message || __("The PDF could not be created.")
  } finally {
    busy.value = ""
  }
}

function open(doc) {
  frappe.set_route("Form", "Patient Official Document", doc.name)
}

function formatDate(value) {
  return value ? frappe.datetime.str_to_user(value.slice(0, 10)) : ""
}
</script>
