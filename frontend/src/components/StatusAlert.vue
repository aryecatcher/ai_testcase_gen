<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    type?: 'success' | 'warning' | 'error' | 'info'
    title?: string
    message?: string
    lines?: string[]
    topSpacing?: boolean
    bottomSpacing?: boolean
  }>(),
  {
    type: 'info',
    title: '',
    message: '',
    lines: () => [],
    topSpacing: false,
    bottomSpacing: true,
  },
)

const normalizedLines = computed(() => {
  if (props.lines.length > 0) {
    return props.lines.filter((line) => String(line || '').trim())
  }
  return String(props.message || '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
})

const primaryMessage = computed(() => {
  return props.message || normalizedLines.value[0] || ''
})

const visible = computed(() => {
  return Boolean(primaryMessage.value || normalizedLines.value.length > 0)
})
</script>

<template>
  <a-alert
    v-if="visible"
    :type="type"
    :show-icon="true"
    :class="{
      'status-alert': true,
      'status-alert--top': topSpacing,
      'status-alert--bottom': bottomSpacing,
    }"
  >
    <template v-if="title" #title>{{ title }}</template>
    <div style="white-space: pre-wrap; line-height: 1.5">{{ primaryMessage }}</div>
    <ul v-if="normalizedLines.length > 1" style="margin: 8px 0 0 18px; padding: 0">
      <li v-for="(line, idx) in normalizedLines" :key="`${idx}-${line}`">{{ line }}</li>
    </ul>
  </a-alert>
</template>

<style scoped>
.status-alert--top {
  margin-top: 12px;
}

.status-alert--bottom {
  margin-bottom: 12px;
}
</style>
