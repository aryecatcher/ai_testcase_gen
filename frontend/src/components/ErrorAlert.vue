<script setup lang="ts">
import StatusAlert from './StatusAlert.vue'

const props = withDefaults(
  defineProps<{
    title?: string
    message?: string
    lines?: string[]
  }>(),
  {
    title: '加载失败',
    message: '',
    lines: () => [],
  },
)
</script>

<template>
  <StatusAlert
    type="error"
    :title="title"
    :message="message"
    :lines="lines"
  />
</template>
