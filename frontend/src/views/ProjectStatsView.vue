<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { getProjectStatistics, type ProjectStatistics } from '../services/projectService'
import ErrorAlert from '../components/ErrorAlert.vue'

const loading = ref(false)
const error = ref('')
const stats = ref<ProjectStatistics>({
  testcases: { total: 0, passed: 0, failed: 0 },
  executions: { total: 0, passRate: 0 },
  integrations: { mcp: 0 },
  categorySummary: [],
})

async function loadStats() {
  loading.value = true
  error.value = ''
  try {
    stats.value = await getProjectStatistics()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

onMounted(loadStats)
</script>

<template>
  <a-card title="数据统计">
    <template #extra>
      <a-button size="small" @click="loadStats">刷新</a-button>
    </template>
    <a-spin :loading="loading">
      <ErrorAlert title="加载失败" :message="error" />
      <a-descriptions :column="2" bordered>
        <a-descriptions-item label="用例总数">{{ stats.testcases.total }}</a-descriptions-item>
        <a-descriptions-item label="执行总数">{{ stats.executions.total }}</a-descriptions-item>
        <a-descriptions-item label="知识模块">{{ stats.integrations.mcp }}</a-descriptions-item>
      </a-descriptions>
      <a-divider />
      <a-typography-title :heading="6">分类结果统计</a-typography-title>
      <a-table
        :data="stats.categorySummary"
        :pagination="false"
        row-key="分类"
        size="small"
      >
        <template #columns>
          <a-table-column title="分类" data-index="分类" />
          <a-table-column title="需求数" data-index="需求数" :width="120" />
          <a-table-column title="用例数" data-index="用例数" :width="120" />
        </template>
      </a-table>
    </a-spin>
  </a-card>
</template>
