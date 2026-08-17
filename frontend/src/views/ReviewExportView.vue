<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import ErrorAlert from '../components/ErrorAlert.vue'
import StatusAlert from '../components/StatusAlert.vue'
import {
  exportCasesExcel,
  exportCasesToFeishuSheet,
  getRequirements,
  getTestCases,
  type Requirement,
  type FeishuSheetPayload,
  type TestCase,
} from '../api/backend'

const loading = ref(false)
const cases = ref<TestCase[]>([])
const q = ref('')
const error = ref('')
const pushing = ref(false)
const sheetUrl = ref('')
const selectedCaseIds = ref<string[]>([])
const currentBatchId = ref(localStorage.getItem('current-batch-id') || '')
const currentGenerationJobId = ref(localStorage.getItem('last-generation-job-id') || '')
const onlyLatestGenerated = ref(false)
const currentPage = ref(1)
const pageSize = ref(10)
const requirements = ref<Requirement[]>([])
const feishu = ref<FeishuSheetPayload>({
  case_ids: [],
  requirement_link_base_url: '',
  app_id: '',
  app_secret: '',
  tenant_access_token: '',
  base_url: 'https://open.feishu.cn',
  spreadsheet_token: '',
  sheet_id: '',
  start_cell: 'A1',
  auto_create_sheet: true,
  sheet_title: '',
  sheet_folder_token: '',
})

const filteredCases = computed(() => {
  const key = q.value.trim().toLowerCase()
  const sourceCases =
    onlyLatestGenerated.value && currentGenerationJobId.value
      ? cases.value.filter((c) => String(c.system_env?.generation_job_id || '') === currentGenerationJobId.value)
      : cases.value
  if (!key) return sourceCases
  return sourceCases.filter((c) =>
    [c.test_case_id, c.related_req_id, c.title, getCaseQuality(c)]
      .map((v) => String(v || '').toLowerCase())
      .some((v) => v.includes(key)),
  )
})
const pagedCases = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return filteredCases.value.slice(start, start + pageSize.value)
})
const currentPageCaseIds = computed(() => pagedCases.value.map((c) => c.test_case_id))
const requirementQualityMap = computed(() => {
  const map = new Map<string, string>()
  for (const req of requirements.value) {
    const quality = String((req.ingestion_metadata || {}).quality_characteristic || '').trim()
    if (quality) {
      map.set(req.id, quality)
    }
  }
  return map
})

function getCaseQuality(c: TestCase): string {
  return (
    String(c.system_env?.quality_characteristic || '').trim() ||
    requirementQualityMap.value.get(String(c.related_req_id || '')) ||
    '未分类'
  )
}

async function loadCases() {
  loading.value = true
  error.value = ''
  try {
    currentGenerationJobId.value = localStorage.getItem('last-generation-job-id') || ''
    const [caseList, requirementList] = await Promise.all([
      getTestCases(currentBatchId.value || undefined),
      getRequirements(currentBatchId.value || undefined).catch(() => []),
    ])
    cases.value = caseList
    requirements.value = requirementList
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

async function exportExcel() {
  const ids = selectedCaseIds.value.length > 0
    ? selectedCaseIds.value
    : filteredCases.value.map((c) => c.test_case_id)
  const blob = await exportCasesExcel(ids)
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `test_cases_${Date.now()}.xlsx`
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

async function pushSheet() {
  pushing.value = true
  error.value = ''
  try {
    const payload: FeishuSheetPayload = {
      ...feishu.value,
      case_ids:
        selectedCaseIds.value.length > 0
          ? selectedCaseIds.value
          : filteredCases.value.map((c) => c.test_case_id),
    }
    const result = await exportCasesToFeishuSheet(payload)
    sheetUrl.value = String(result.sheet_url || '')
    if (result.spreadsheet_token) feishu.value.spreadsheet_token = String(result.spreadsheet_token)
    if (result.sheet_id) feishu.value.sheet_id = String(result.sheet_id)
    localStorage.setItem('feishu-sheet-config', JSON.stringify(feishu.value))
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    pushing.value = false
  }
}

function onCasesUpdated() {
  loadCases()
}

function onGenerationRunUpdated() {
  currentGenerationJobId.value = localStorage.getItem('last-generation-job-id') || ''
  loadCases()
}

function onBatchChanged() {
  currentBatchId.value = localStorage.getItem('current-batch-id') || ''
  selectedCaseIds.value = []
  loadCases()
}

function selectLatestGeneratedCases() {
  if (!currentGenerationJobId.value) return
  selectedCaseIds.value = cases.value
    .filter((c) => String(c.system_env?.generation_job_id || '') === currentGenerationJobId.value)
    .map((c) => c.test_case_id)
}

function selectAllFilteredCases() {
  selectedCaseIds.value = filteredCases.value.map((c) => c.test_case_id)
}

function invertFilteredCases() {
  const selected = new Set(selectedCaseIds.value)
  const filteredIds = filteredCases.value.map((c) => c.test_case_id)
  const outside = selectedCaseIds.value.filter((id) => !filteredIds.includes(id))
  const inverted = filteredIds.filter((id) => !selected.has(id))
  selectedCaseIds.value = [...outside, ...inverted]
}

function clearCaseSelection() {
  selectedCaseIds.value = []
}

function onCaseSelectionChange(keys: (string | number)[]) {
  const pageKeys = keys.map((k) => String(k))
  const pageIdSet = new Set(currentPageCaseIds.value)
  const reserved = selectedCaseIds.value.filter((id) => !pageIdSet.has(id))
  selectedCaseIds.value = [...reserved, ...pageKeys]
}

function onPageChange(page: number) {
  currentPage.value = page
}

function onPageSizeChange(size: number) {
  pageSize.value = size
  currentPage.value = 1
}

onMounted(loadCases)
onMounted(() => {
  const raw = localStorage.getItem('feishu-sheet-config')
  if (raw) {
    try {
      feishu.value = { ...feishu.value, ...(JSON.parse(raw) as Partial<FeishuSheetPayload>) }
    } catch {
      // ignore invalid local cache
    }
  }
  window.addEventListener('cases-updated', onCasesUpdated)
  window.addEventListener('batch-changed', onBatchChanged)
  window.addEventListener('generation-run-updated', onGenerationRunUpdated)
})

onUnmounted(() => {
  window.removeEventListener('cases-updated', onCasesUpdated)
  window.removeEventListener('batch-changed', onBatchChanged)
  window.removeEventListener('generation-run-updated', onGenerationRunUpdated)
})

watch(
  feishu,
  (v) => {
    localStorage.setItem('feishu-sheet-config', JSON.stringify(v))
  },
  { deep: true },
)

watch(q, () => {
  currentPage.value = 1
})
</script>

<template>
  <a-card title="评审与导出">
    <template #extra>
      <a-space>
        <span style="color:#6b7280;font-size:12px">当前批次：{{ currentBatchId || '全部' }}</span>
        <a-button size="small" @click="loadCases">刷新</a-button>
        <a-button size="small" :disabled="!currentGenerationJobId" @click="selectLatestGeneratedCases">
          选择最新生成批次
        </a-button>
        <a-button size="small" @click="selectAllFilteredCases">全选当前结果</a-button>
        <a-button size="small" @click="invertFilteredCases">反选当前结果</a-button>
        <a-button size="small" @click="clearCaseSelection">清空选择</a-button>
        <a-button type="primary" :disabled="filteredCases.length === 0" @click="exportExcel">导出Excel</a-button>
      </a-space>
    </template>

    <ErrorAlert title="操作失败" :message="error" />
    <StatusAlert
      type="info"
      :message="`当前结果 ${filteredCases.length} 条，已跨页选择 ${selectedCaseIds.length} 条；最新生成批次：${currentGenerationJobId || '暂无'}。未手动勾选时，导出和推送默认作用于当前结果全部内容。`"
    />

    <a-space style="margin-bottom: 12px">
      <a-checkbox v-model="onlyLatestGenerated" :disabled="!currentGenerationJobId">
        仅显示最新生成批次
      </a-checkbox>
    </a-space>

    <a-input-search v-model="q" placeholder="搜索 Case ID / 需求ID / 标题" class="search-input" />

    <a-table
      :data="pagedCases"
      :loading="loading"
      :pagination="{
        current: currentPage,
        pageSize,
        total: filteredCases.length,
        showTotal: true,
        showPageSize: true,
      }"
      row-key="test_case_id"
      :row-selection="{
        type: 'checkbox',
        selectedRowKeys: selectedCaseIds,
        showCheckedAll: true,
      }"
      @page-change="onPageChange"
      @page-size-change="onPageSizeChange"
      @selection-change="onCaseSelectionChange"
    >
      <template #columns>
        <a-table-column title="Case ID" data-index="test_case_id" :width="190" />
        <a-table-column title="需求ID" data-index="related_req_id" :width="180" />
        <a-table-column title="标题" data-index="title" :ellipsis="true" />
        <a-table-column title="优先级" data-index="priority" :width="100" />
        <a-table-column title="类型" data-index="dimension" :width="120" />
        <a-table-column title="质量特性" :width="140">
          <template #cell="{ record }">
            {{ getCaseQuality(record) }}
          </template>
        </a-table-column>
      </template>
    </a-table>

    <a-divider />
    <a-typography-title :heading="6">飞书 Sheet 推送</a-typography-title>
    <a-grid :cols="2" :col-gap="12" :row-gap="8">
      <a-grid-item><a-input v-model="feishu.app_id" placeholder="App ID" /></a-grid-item>
      <a-grid-item><a-input-password v-model="feishu.app_secret" placeholder="App Secret" /></a-grid-item>
      <a-grid-item><a-input v-model="feishu.tenant_access_token" placeholder="Tenant Access Token（可选）" /></a-grid-item>
      <a-grid-item><a-input v-model="feishu.base_url" placeholder="Open API Base URL" /></a-grid-item>
      <a-grid-item><a-input v-model="feishu.spreadsheet_token" placeholder="Spreadsheet Token" /></a-grid-item>
      <a-grid-item><a-input v-model="feishu.sheet_id" placeholder="Sheet ID（可选）" /></a-grid-item>
      <a-grid-item><a-input v-model="feishu.start_cell" placeholder="起始单元格，如 A1" /></a-grid-item>
      <a-grid-item><a-input v-model="feishu.sheet_title" placeholder="自动创建 Sheet 标题" /></a-grid-item>
    </a-grid>
    <a-space class="sheet-actions">
      <a-checkbox v-model="feishu.auto_create_sheet">缺失时自动创建新 Sheet</a-checkbox>
      <a-button type="primary" :loading="pushing" :disabled="filteredCases.length === 0" @click="pushSheet">
        推送到飞书Sheet
      </a-button>
    </a-space>
    <StatusAlert
      type="success"
      :message="sheetUrl ? `推送成功：${sheetUrl}` : ''"
      :top-spacing="true"
      :bottom-spacing="false"
    />
  </a-card>
</template>

<style scoped>
.search-input {
  margin-bottom: 12px;
}

.sheet-actions {
  margin-top: 12px;
}
</style>
