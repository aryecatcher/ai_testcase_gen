<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { getKgSummary, type KgModuleSummary, type KgSummary } from '../api/backend'
import ErrorAlert from '../components/ErrorAlert.vue'
import StatusAlert from '../components/StatusAlert.vue'

const loading = ref(false)
const summary = ref<KgSummary>({ modules: [] })
const modules = ref<KgModuleSummary[]>([])
const error = ref('')
const drawerVisible = ref(false)
const activeModule = ref<KgModuleSummary | null>(null)

const totals = computed(() => ({
  modules: modules.value.length,
  rules: modules.value.reduce((sum, item) => sum + Number(item.rules_count || 0), 0),
  scenarios: modules.value.reduce((sum, item) => sum + Number(item.scenarios_count || 0), 0),
  methods: modules.value.reduce((sum, item) => sum + Number(item.methods_count || 0), 0),
}))

async function loadSummary() {
  loading.value = true
  error.value = ''
  try {
    summary.value = await getKgSummary()
    modules.value = summary.value.modules || []
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

function getModuleName(item: KgModuleSummary): string {
  return String(item.module || item.name || '未命名模块')
}

function showModuleDetail(item: KgModuleSummary) {
  activeModule.value = item
  drawerVisible.value = true
}

function asLines(items: unknown): string[] {
  if (!Array.isArray(items)) return []
  return items.map((item) => {
    if (typeof item === 'string') return item
    if (item && typeof item === 'object') {
      const obj = item as Record<string, unknown>
      const name = String(obj.name || obj.title || obj.id || '').trim()
      const logic = String(obj.logic || obj.description || obj.content || '').trim()
      return [name, logic].filter(Boolean).join(' - ')
    }
    return String(item)
  })
}

onMounted(loadSummary)
</script>

<template>
  <a-card title="知识图谱">
    <template #extra>
      <a-button size="small" @click="loadSummary">刷新</a-button>
    </template>
    <ErrorAlert title="加载失败" :message="error" />
    <StatusAlert
      type="info"
      title="场景扩展"
      message="生成用例时，系统会结合知识图谱中的业务场景库自动扩展关联场景。例如登录模块可联想到忘记密码、第三方登录、异常登录、失败模式等子场景，帮助减少测试遗漏。"
    />
    <a-spin :loading="loading">
      <a-descriptions :column="4" bordered style="margin-bottom: 12px">
        <a-descriptions-item label="模块数">{{ totals.modules }}</a-descriptions-item>
        <a-descriptions-item label="规则总数">{{ totals.rules }}</a-descriptions-item>
        <a-descriptions-item label="场景总数">{{ totals.scenarios }}</a-descriptions-item>
        <a-descriptions-item label="方法总数">{{ totals.methods }}</a-descriptions-item>
      </a-descriptions>
      <a-table :data="modules" row-key="id" :pagination="{ pageSize: 10 }">
        <template #columns>
          <a-table-column title="模块" :width="220">
            <template #cell="{ record }">
              {{ getModuleName(record) }}
            </template>
          </a-table-column>
          <a-table-column title="规则数" data-index="rules_count" :width="120" />
          <a-table-column title="场景数" data-index="scenarios_count" :width="120" />
          <a-table-column title="方法数" data-index="methods_count" :width="120" />
          <a-table-column title="模板数" data-index="templates_count" :width="120" />
          <a-table-column title="失败模式" data-index="failure_modes_count" :width="120" />
          <a-table-column title="操作" :width="120">
            <template #cell="{ record }">
              <a-button type="text" @click="showModuleDetail(record)">查看详情</a-button>
            </template>
          </a-table-column>
        </template>
      </a-table>
    </a-spin>

    <a-drawer v-model:visible="drawerVisible" :width="720" :footer="false">
      <template #title>{{ activeModule ? getModuleName(activeModule) : '模块详情' }}</template>
      <a-space direction="vertical" fill size="medium">
        <a-descriptions v-if="activeModule" :column="3" bordered>
          <a-descriptions-item label="规则数">{{ activeModule.rules_count || 0 }}</a-descriptions-item>
          <a-descriptions-item label="场景数">{{ activeModule.scenarios_count || 0 }}</a-descriptions-item>
          <a-descriptions-item label="方法数">{{ activeModule.methods_count || 0 }}</a-descriptions-item>
          <a-descriptions-item label="模板数">{{ activeModule.templates_count || 0 }}</a-descriptions-item>
          <a-descriptions-item label="失败模式">{{ activeModule.failure_modes_count || 0 }}</a-descriptions-item>
          <a-descriptions-item label="全局模块">{{ activeModule.is_global ? '是' : '否' }}</a-descriptions-item>
        </a-descriptions>

        <a-card size="small" title="规则约束">
          <a-empty v-if="asLines(activeModule?.rules).length === 0" description="暂无规则" />
          <ul v-else class="kg-list">
            <li v-for="(line, idx) in asLines(activeModule?.rules)" :key="`rule-${idx}`">{{ line }}</li>
          </ul>
        </a-card>

        <a-card size="small" title="业务场景">
          <a-empty v-if="asLines(activeModule?.scenarios).length === 0" description="暂无场景" />
          <ul v-else class="kg-list">
            <li v-for="(line, idx) in asLines(activeModule?.scenarios)" :key="`scenario-${idx}`">{{ line }}</li>
          </ul>
        </a-card>

        <a-card size="small" title="测试方法">
          <a-empty v-if="asLines(activeModule?.methods).length === 0" description="暂无方法" />
          <ul v-else class="kg-list">
            <li v-for="(line, idx) in asLines(activeModule?.methods)" :key="`method-${idx}`">{{ line }}</li>
          </ul>
        </a-card>

        <a-card size="small" title="用例模板">
          <a-empty v-if="asLines(activeModule?.templates).length === 0" description="暂无模板" />
          <ul v-else class="kg-list">
            <li v-for="(line, idx) in asLines(activeModule?.templates)" :key="`template-${idx}`">{{ line }}</li>
          </ul>
        </a-card>

        <a-card size="small" title="失败模式">
          <a-empty v-if="asLines(activeModule?.failure_modes).length === 0" description="暂无失败模式" />
          <ul v-else class="kg-list">
            <li v-for="(line, idx) in asLines(activeModule?.failure_modes)" :key="`failure-${idx}`">{{ line }}</li>
          </ul>
        </a-card>
      </a-space>
    </a-drawer>
  </a-card>
</template>

<style scoped>
.kg-list {
  margin: 0;
  padding-left: 18px;
  line-height: 1.7;
}
</style>
