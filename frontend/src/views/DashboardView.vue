<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { IconApps, IconDesktop, IconFile, IconThunderbolt } from '@arco-design/web-vue/es/icon'
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

const activeCategoryCount = computed(() => stats.value.categorySummary.filter((item) => item.用例数 > 0).length)

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
  <a-spin :loading="loading" tip="加载中...">
    <ErrorAlert title="加载失败" :message="error" />
    <div class="overview-grid">
      <div class="overview-card">
        <div class="overview-head"><icon-file /> 功能用例</div>
        <div class="overview-value">{{ stats.testcases.total }}</div>
        <div class="overview-sub">当前批次已生成用例总量</div>
      </div>
      <div class="overview-card">
        <div class="overview-head"><icon-desktop /> AI测试用例生成</div>
        <div class="overview-value">{{ stats.testcases.total }}</div>
        <div class="overview-sub">已入库用例总量</div>
      </div>
      <div class="overview-card">
        <div class="overview-head"><icon-thunderbolt /> 质量分类</div>
        <div class="overview-value">{{ activeCategoryCount }}</div>
        <div class="overview-sub">当前批次已有用例的质量特性分类数</div>
      </div>
      <div class="overview-card">
        <div class="overview-head"><icon-apps /> 知识图谱模块</div>
        <div class="overview-value">{{ stats.integrations.mcp }}</div>
        <div class="overview-sub">已识别模块数</div>
      </div>
    </div>
  </a-spin>
</template>

<style scoped>
.overview-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}
.overview-card {
  border-radius: 12px;
  border: 1px solid #e5e7eb;
  background: #fff;
  padding: 14px;
}
.overview-head {
  display: flex;
  gap: 6px;
  align-items: center;
  color: #6b7280;
  font-size: 13px;
  font-weight: 700;
}
.overview-value {
  margin-top: 8px;
  font-size: 28px;
  font-weight: 800;
}
.overview-sub {
  margin-top: 6px;
  color: #6b7280;
  font-size: 12px;
}
</style>
