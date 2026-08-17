<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { getHealth, getStartupStatus } from './api/backend'
import StatusAlert from './components/StatusAlert.vue'

const backendTarget = String(import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8003')
const checkingStartup = ref(false)
const startupMessage = ref('')
const startupLines = ref<string[]>([])
const startupType = ref<'success' | 'warning' | 'error' | 'info'>('info')

const showStartupAlert = computed(() => Boolean(startupMessage.value || startupLines.value.length))

async function checkStartup() {
  checkingStartup.value = true
  try {
    const [health, startup] = await Promise.all([getHealth(), getStartupStatus()])
    const startupStatus = String(health.status || '').toLowerCase()
    const bootStatus = String(health.startup_status || startup.status || '').toLowerCase()
    const llm = ((startup.llm || {}) as Record<string, unknown>)
    const llmStatus = String(llm.status || '').toLowerCase()
    const llmMessage = String(llm.message || '').trim()
    const steps = Array.isArray(startup.steps) ? (startup.steps as Array<Record<string, unknown>>) : []
    const failedSteps = steps
      .filter((item) => String(item.status || '').toLowerCase() === 'error')
      .map((item) => `${String(item.name || 'startup')}: ${String(item.detail || '').trim()}`)

    if (startupStatus !== 'healthy') {
      startupType.value = 'error'
      startupMessage.value = '后端服务不可用，请确认后端已经启动。'
      startupLines.value = [`建议先检查当前后端目标是否可访问: ${backendTarget}`]
      return
    }

    if (bootStatus === 'success' && llmStatus === 'success') {
      startupType.value = 'success'
      startupMessage.value = '系统初始化完成，模型连接正常。'
      startupLines.value = []
      return
    }

    startupType.value = bootStatus === 'error' ? 'error' : 'warning'
    startupMessage.value = llmStatus === 'error'
      ? '模型连接异常，当前前端已打开，但生成能力可能不可用。'
      : '系统启动未完全通过，请检查初始化状态。'
    startupLines.value = [
      llmMessage ? `模型检查: ${llmMessage}` : '',
      ...failedSteps,
    ].filter(Boolean)
  } catch (e) {
    startupType.value = 'error'
    startupMessage.value = '启动检查失败，前端无法确认后端和模型状态。'
    startupLines.value = [e instanceof Error ? e.message : String(e)]
  } finally {
    checkingStartup.value = false
  }
}

onMounted(() => {
  void checkStartup()
})
</script>

<template>
  <div class="app-shell">
    <StatusAlert
      v-if="showStartupAlert"
      :type="startupType"
      title="启动检查"
      :message="checkingStartup ? '正在检查后端与模型连接状态...' : startupMessage"
      :lines="startupLines"
      :bottom-spacing="true"
    />
    <router-view />
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
  padding: 12px;
  box-sizing: border-box;
}
</style>
