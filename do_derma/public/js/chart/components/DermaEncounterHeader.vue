<template>
  <header class="derma-encounter-header" data-test="encounter-header">
    <div class="encounter-patient">
      <img
        v-if="patient.image && !isBroken(patient.image)"
        :src="patient.image"
        :alt="patientName"
        @error="markBroken(patient.image)"
      />
      <span v-else class="patient-avatar">{{ initials }}</span>
      <div>
        <strong data-test="header-patient-name">{{ patientName }}</strong>
        <small>{{ patientMeta }}</small>
      </div>
    </div>

    <div class="encounter-status-strip">
      <span class="encounter-chip" :class="{ warning: allergyText }">
        <b>{{ __("Allergies") }}</b>
        {{ allergyText || __("None recorded") }}
      </span>
      <span class="encounter-chip">
        <b>{{ __("Visit") }}</b>
        {{ visitType || __("Visit") }}
      </span>
      <span class="encounter-chip" :class="{ active: hasSessionContext }">
        <b>{{ __("Status") }}</b>
        {{ encounter.docstatus === 1 ? __("Submitted") : hasSessionContext ? __("Open") : __("Pending") }}
      </span>
      <span class="encounter-chip">
        <b>{{ __("Insurance") }}</b>
        {{ insuranceLabel || __("Not set") }}
      </span>
    </div>

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
      <span v-else class="encounter-completed-note" data-test="encounter-completed-note">
        {{ __("Completed. Reopening needs cancel permission.") }}
      </span>
    </div>

    <div v-if="alerts.length" class="encounter-alert-chips" data-test="encounter-alerts">
      <button
        v-for="alert in alerts"
        :key="alert.key"
        type="button"
        class="encounter-alert-chip"
        :class="alert.tone"
        :title="alert.detail"
        @click="$emit('alert-action', alert)"
      >
        <b>{{ alert.label }}</b>
        <small>{{ alert.detail }}</small>
      </button>
    </div>
  </header>
</template>

<script setup>
import { computed } from "vue"
import { useBrokenImages } from "../../shared/broken_images.js"

const __ = window.__ || ((txt) => txt)

const { isBroken, markBroken } = useBrokenImages()

const props = defineProps({
  patient: { type: Object, default: () => ({}) },
  appointment: { type: Object, default: () => ({}) },
  encounter: { type: Object, default: () => ({}) },
  practitionerName: { type: String, default: "" },
  allergyText: { type: String, default: "" },
  insuranceLabel: { type: String, default: "" },
  hasSessionContext: { type: Boolean, default: false },
  completing: { type: Boolean, default: false },
  // A completion awaiting its confirm dialog: the button refuses a second click without
  // claiming that completion is under way.
  pending: { type: Boolean, default: false },
  canReopen: { type: Boolean, default: false },
  reopening: { type: Boolean, default: false },
  alerts: { type: Array, default: () => [] },
})

defineEmits(["complete", "reopen", "alert-action"])

const isCompleted = computed(() => Number(props.encounter.docstatus) === 1)
const patientName = computed(() => props.patient.patient_name || props.patient.name || __("Patient"))
const initials = computed(() => patientName.value.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "P")
const patientMeta = computed(() => {
  const parts = [
    props.patient.sex,
    props.patient.name ? `${__("MRN")}: ${props.patient.name}` : "",
    props.practitionerName,
  ].filter(Boolean)
  return parts.join(" · ")
})
const visitType = computed(() => props.appointment.custom_appointment_category || props.appointment.appointment_type || props.encounter.appointment_type || "")
</script>
