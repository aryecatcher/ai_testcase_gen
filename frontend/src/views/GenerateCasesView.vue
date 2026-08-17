<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import ErrorAlert from '../components/ErrorAlert.vue'
import {
  cancelGenerateJob,
  getGenerateJobStatus,
  getRequirements,
  streamGenerateCases,
  type Requirement,
  type StreamUpdate,
} from '../api/backend'

const loadingReq = ref(false)
const generating = ref(false)
const requirements = ref<Requirement[]>([])
const selectedReqIds = ref<string[]>([])
const updates = ref<StreamUpdate[]>([])
const resultCount = ref(0)
const error = ref('')
const currentJobId = ref('')
const lastRunReqIds = ref<string[]>([])
const generationDone = ref(false)
let abortController: AbortController | null = null
const currentBatchId = ref(localStorage.getItem('current-batch-id') || '')
const currentJobEventIndex = ref(0)
let statusPollTimer: number | null = null
const isDragging = ref(false)
const dragMode = ref<'select' | 'unselect'>('select')
const anchorIndex = ref<number | null>(null)
const progressConsole = ref<HTMLElement | null>(null)
const handleMouseUp = () => {
  isDragging.value = false
}
const handleBatchChanged = () => {
  currentBatchId.value = localStorage.getItem('current-batch-id') || ''
  selectedReqIds.value = []
  loadRequirements()
}

const selectedRequirements = computed(() =>
  requirements.value.filter((r) => selectedReqIds.value.includes(r.id)),
)
const selectedSet = computed(() => new Set(selectedReqIds.value))
const latestUpdate = computed(() => updates.value.at(-1) || null)
const progressText = ref('')
const ACTIVE_JOB_KEY = 'active-generation-job-id'
const LAST_JOB_KEY = 'last-generation-job-id'
const currentStageText = computed(() => {
  const u = latestUpdate.value
  if (!u) return generating.value ? '正在初始化生成任务...' : '尚未开始'
  const node = String(u.node || '').trim()
  const status = String(u.status || u.message || '').trim()
  return [node, status].filter(Boolean).join(' / ') || '处理中'
})
function formatProgressUpdate(u: StreamUpdate): string[] {
  const lines: string[] = []
  const prefix = [String(u.type || '').toUpperCase(), String(u.node || '').trim(), String((u as any).req_id || '').trim()]
    .filter(Boolean)
    .join(' | ')
  const status = String(u.status || u.message || '').trim()
  if (status) {
    lines.push(prefix ? `[${prefix}] ${status}` : status)
  }
  if (Array.isArray((u as any).trace)) {
    for (const item of (u as any).trace as unknown[]) {
      lines.push(`  ${String(item)}`)
    }
  }
  if (u.type === 'result' && Array.isArray(u.data)) {
    lines.push(`  本轮生成 ${u.data.length} 条用例`)
  }
  if (u.type === 'cancelled') {
    lines.push('  任务已中断')
  }
  return lines
}

function appendUpdate(u: StreamUpdate) {
  updates.value.push(u)
  const chunk = formatProgressUpdate(u).join('\n')
  if (chunk) {
    progressText.value += `${progressText.value ? '\n' : ''}${chunk}`
  }
  if (u.type === 'result' && Array.isArray(u.data)) {
    resultCount.value += u.data.length
  }
}

function clearStatusPolling() {
  if (statusPollTimer !== null) {
    window.clearInterval(statusPollTimer)
    statusPollTimer = null
  }
}

async function syncJobStatus(jobId: string) {
  const status = await getGenerateJobStatus(jobId, currentJobEventIndex.value)
  currentJobId.value = jobId
  lastRunReqIds.value = status.req_ids || lastRunReqIds.value
  for (const event of status.events || []) {
    appendUpdate(event)
  }
  currentJobEventIndex.value = status.next_index || currentJobEventIndex.value
  resultCount.value = status.result_count || resultCount.value
  if (status.done) {
    generating.value = false
    generationDone.value = status.status === 'completed'
    if (status.status === 'failed' && status.error) {
      error.value = status.error
    }
    localStorage.removeItem(ACTIVE_JOB_KEY)
    if (status.status === 'completed') {
      localStorage.setItem(LAST_JOB_KEY, jobId)
      window.dispatchEvent(new Event('cases-updated'))
      window.dispatchEvent(new Event('generation-run-updated'))
    }
    clearStatusPolling()
  } else {
    generating.value = true
  }
}

function startStatusPolling(jobId: string) {
  clearStatusPolling()
  void syncJobStatus(jobId)
  statusPollTimer = window.setInterval(() => {
    void syncJobStatus(jobId)
  }, 2000)
}

function resumeActiveJob() {
  const activeJobId = localStorage.getItem(ACTIVE_JOB_KEY)
  if (!activeJobId) return
  updates.value = []
  progressText.value = ''
  resultCount.value = 0
  generationDone.value = false
  currentJobEventIndex.value = 0
  startStatusPolling(activeJobId)
}

async function loadRequirements() {
  loadingReq.value = true
  error.value = ''
  try {
    requirements.value = await getRequirements(currentBatchId.value || undefined)
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loadingReq.value = false
  }
}

async function runGenerate() {
  if (selectedRequirements.value.length === 0) return
  generating.value = true
  error.value = ''
  updates.value = []
  resultCount.value = 0
  progressText.value = ''
  generationDone.value = false
  currentJobEventIndex.value = 0
  lastRunReqIds.value = [...selectedReqIds.value]
  currentJobId.value = `job_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`
  localStorage.setItem(ACTIVE_JOB_KEY, currentJobId.value)
  abortController = new AbortController()
  try {
    await streamGenerateCases(selectedRequirements.value, currentJobId.value, (u) => {
      currentJobEventIndex.value += 1
      appendUpdate(u)
    }, abortController.signal)
    generationDone.value = true
    localStorage.removeItem(ACTIVE_JOB_KEY)
    localStorage.setItem(LAST_JOB_KEY, currentJobId.value)
    window.dispatchEvent(new Event('cases-updated'))
    window.dispatchEvent(new Event('generation-run-updated'))
  } catch (e) {
    if ((e as any)?.name !== 'AbortError') {
      error.value = e instanceof Error ? e.message : String(e)
      localStorage.removeItem(ACTIVE_JOB_KEY)
    }
  } finally {
    generating.value = false
    abortController = null
  }
}

async function stopGenerate() {
  if (!currentJobId.value) return
  try {
    await cancelGenerateJob(currentJobId.value)
  } catch {
    // ignore cancel endpoint failure
  }
  localStorage.removeItem(ACTIVE_JOB_KEY)
  abortController?.abort()
}

async function retryGenerate() {
  if (lastRunReqIds.value.length === 0) return
  selectedReqIds.value = [...lastRunReqIds.value]
  await runGenerate()
}

function setReqSelected(reqId: string, selected: boolean) {
  const set = new Set(selectedReqIds.value)
  if (selected) set.add(reqId)
  else set.delete(reqId)
  selectedReqIds.value = [...set]
}

function selectAll() {
  selectedReqIds.value = requirements.value.map((r) => r.id)
}

function clearAll() {
  selectedReqIds.value = []
}

function onReqToggle(reqId: string, index: number, checked: boolean, withShift: boolean) {
  if (withShift && anchorIndex.value !== null) {
    const [start, end] = [anchorIndex.value, index].sort((a, b) => a - b)
    for (let i = start; i <= end; i += 1) {
      setReqSelected(requirements.value[i].id, checked)
    }
  } else {
    setReqSelected(reqId, checked)
  }
  anchorIndex.value = index
}

function onDragStart(reqId: string, index: number) {
  const next = !selectedSet.value.has(reqId)
  dragMode.value = next ? 'select' : 'unselect'
  isDragging.value = true
  onReqToggle(reqId, index, next, false)
}

function onDragEnter(reqId: string, index: number) {
  if (!isDragging.value) return
  onReqToggle(reqId, index, dragMode.value === 'select', false)
}

function onReqCheckboxClick(reqId: string, index: number, event: MouseEvent) {
  onReqToggle(reqId, index, !selectedSet.value.has(reqId), event.shiftKey)
}

onMounted(() => {
  loadRequirements()
  resumeActiveJob()
  window.addEventListener('requirements-updated', loadRequirements)
  window.addEventListener('batch-changed', handleBatchChanged)
  window.addEventListener('mouseup', handleMouseUp)
})

onUnmounted(() => {
  clearStatusPolling()
  if (generating.value) {
    abortController?.abort()
  }
  window.removeEventListener('requirements-updated', loadRequirements)
  window.removeEventListener('batch-changed', handleBatchChanged)
  window.removeEventListener('mouseup', handleMouseUp)
})

watch(progressText, async () => {
  await nextTick()
  if (!progressConsole.value) return
  progressConsole.value.scrollTop = progressConsole.value.scrollHeight
})
</script>


<template>
  <a-card title="生成用例">
    <template #extra>
      <a-space>
        <span style="color:#6b7280;font-size:12px">当前批次：{{ currentBatchId || '全部' }}</span>
        <a-button size="small" @click="loadRequirements">刷新需求</a-button>
        <a-button
          type="primary"
          :loading="generating"
          :disabled="selectedRequirements.length === 0"
          @click="runGenerate"
        >
          开始生成
        </a-button>
        <a-button status="danger" :disabled="!generating" @click="stopGenerate">
          中断
        </a-button>
        <a-button :disabled="generating || lastRunReqIds.length === 0" @click="retryGenerate">
          重试上次
        </a-button>
      </a-space>
    </template>

    <ErrorAlert title="操作失败" :message="error" />

    <a-space direction="vertical" fill size="medium">
      <a-card size="small" title="选择需求">
        <a-space style="margin-bottom: 8px">
          <a-button size="mini" @click="selectAll">全选</a-button>
          <a-button size="mini" @click="clearAll">清空</a-button>
          <span style="color:#6b7280;font-size:12px">支持 Shift 区间选择，或按住鼠标滑动连续选择</span>
        </a-space>
        <div style="display:flex;flex-direction:column;gap:6px">
          <label
            v-for="(req, idx) in requirements"
            :key="req.id"
            style="display:flex;gap:8px;align-items:flex-start;padding:6px 8px;border-radius:8px;cursor:pointer"
            @mousedown.prevent="onDragStart(req.id, idx)"
            @mouseenter="onDragEnter(req.id, idx)"
          >
            <a-checkbox
              :model-value="selectedSet.has(req.id)"
              @click.stop="onReqCheckboxClick(req.id, idx, $event as MouseEvent)"
            />
            <span>{{ req.id }} - {{ req.original_text?.slice(0, 80) || '无内容' }}</span>
          </label>
        </div>
        <a-empty v-if="!loadingReq && requirements.length === 0" description="暂无需求数据" />
      </a-card>

      <a-card size="small" title="生成进度">
        <div style="margin-bottom: 8px">
          已接收更新：{{ updates.length }} 条；生成用例：{{ resultCount }} 条；任务ID：{{ currentJobId || '—' }}
        </div>
        <div class="progress-stage">当前阶段：{{ currentStageText }}</div>
        <div v-if="generationDone" class="progress-done">生成完毕</div>
        <div class="progress-shell">
          <div ref="progressConsole" class="progress-scroll">
            <pre class="progress-log">{{ progressText }}</pre>
          </div>
        </div>
      </a-card>
    </a-space>
  </a-card>
</template>

<style scoped>
.progress-shell {
  height: 420px;
  max-height: 420px;
  width: 100%;
  min-width: 0;
  overflow: hidden;
  display: flex;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #f8fafc;
}

.progress-scroll {
  height: 100%;
  width: 100%;
  min-width: 0;
  overflow: auto;
  overscroll-behavior: contain;
}

.progress-log {
  margin: 0;
  padding: 12px;
  box-sizing: border-box;
  width: 100%;
  min-width: 0;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
  max-width: 100%;
  overflow: hidden;
  font-size: 12px;
  line-height: 1.5;
  color: #0f172a;
  font-family: Consolas, 'Courier New', monospace;
}

.progress-stage {
  margin-bottom: 8px;
  color: #475569;
  font-size: 12px;
}

.progress-done {
  margin-bottom: 8px;
  color: #16a34a;
  font-size: 13px;
  font-weight: 700;
}
</style>
