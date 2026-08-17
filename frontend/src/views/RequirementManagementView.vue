<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { getRequirements, ingestRequirements, type Requirement } from '../api/backend'
import ErrorAlert from '../components/ErrorAlert.vue'
import StatusAlert from '../components/StatusAlert.vue'

const loading = ref(false)
const parsing = ref(false)
const requirements = ref<Requirement[]>([])
const error = ref('')
const errorLines = ref<string[]>([])
const parseMessage = ref('')
const parseErrors = ref<string[]>([])
const replaceExisting = ref(false)
const selectedFiles = ref<File[]>([])
const currentBatchId = ref(localStorage.getItem('current-batch-id') || '')

async function loadRequirements() {
  loading.value = true
  error.value = ''
  errorLines.value = []
  try {
    requirements.value = await getRequirements(currentBatchId.value || undefined)
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
    errorLines.value = String(error.value || '')
      .split('\n')
      .map((s) => s.trim())
      .filter(Boolean)
  } finally {
    loading.value = false
  }
}

onMounted(loadRequirements)

function onFileChange(e: Event) {
  const target = e.target as HTMLInputElement
  selectedFiles.value = Array.from(target.files || [])
}

async function parseFiles() {
  if (selectedFiles.value.length === 0) return
  parsing.value = true
  error.value = ''
  errorLines.value = []
  parseMessage.value = ''
  parseErrors.value = []
  try {
    const result = await ingestRequirements(selectedFiles.value, replaceExisting.value)
    const saved = Number(result.saved || 0)
    currentBatchId.value = String(result.batch_id || '')
    if (currentBatchId.value) {
      localStorage.setItem('current-batch-id', currentBatchId.value)
    }
    const errs = Array.isArray(result.errors) ? result.errors : []
    parseErrors.value = errs.map((e) => {
      const item = e as Record<string, unknown>
      return `${String(item.file || '文件')}: ${String(item.error || '未知错误')}`
    })
    parseMessage.value =
      parseErrors.value.length > 0
        ? `解析完成：保存 ${saved} 条需求；失败 ${parseErrors.value.length} 个文件`
        : `解析完成：保存 ${saved} 条需求`
    await loadRequirements()
    window.dispatchEvent(new Event('requirements-updated'))
    window.dispatchEvent(new Event('batch-changed'))
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
    errorLines.value = String(error.value || '')
      .split('\n')
      .map((s) => s.trim())
      .filter(Boolean)
    if (errorLines.value.length === 0) {
      errorLines.value = ['解析失败，但未返回可显示的错误信息，请查看浏览器控制台和后端日志。']
    }
  } finally {
    parsing.value = false
  }
}
</script>

<template>
  <a-card title="导入需求">
    <template #extra>
      <a-space>
        <span style="color:#6b7280;font-size:12px">当前批次：{{ currentBatchId || '全部' }}</span>
        <a-button size="small" @click="loadRequirements">刷新</a-button>
      </a-space>
    </template>
    <ErrorAlert title="解析失败" :message="error" :lines="errorLines" />
    <StatusAlert
      type="info"
      message="支持 DOCX / XLSX / TXT / JSON / MD 上传并调用后端解析；文档较大时可能需要 1-3 分钟。"
    />
    <a-space direction="vertical" fill class="upload-actions">
      <input type="file" multiple accept=".doc,.docx,.xlsx,.xls,.txt,.json,.md,.markdown" @change="onFileChange" />
      <a-checkbox v-model="replaceExisting">替换已有需求（会清空当前需求与用例）</a-checkbox>
      <a-button type="primary" :loading="parsing" :disabled="selectedFiles.length === 0" @click="parseFiles">
        上传并解析
      </a-button>
      <StatusAlert type="success" :message="parseMessage" />
      <StatusAlert type="warning" title="解析失败明细" :lines="parseErrors" />
    </a-space>
    <a-table
      :loading="loading"
      :data="requirements"
      :pagination="{ pageSize: 8 }"
      row-key="id"
    >
      <template #columns>
        <a-table-column title="需求ID" data-index="id" :width="220" />
        <a-table-column title="需求原文" :ellipsis="true">
          <template #cell="{ record }">
            {{ record.original_text || '—' }}
          </template>
        </a-table-column>
        <a-table-column title="置信度" :width="120">
          <template #cell="{ record }">
            {{ record.confidence ?? '—' }}
          </template>
        </a-table-column>
      </template>
    </a-table>
  </a-card>
</template>

<style scoped>
.upload-actions {
  margin-bottom: 12px;
}
</style>
