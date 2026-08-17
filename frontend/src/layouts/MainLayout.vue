<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useProjectStore } from '../store/projectStore'

const route = useRoute()
const router = useRouter()
const projectStore = useProjectStore()

const title = computed(() => {
  const map: Record<string, string> = {
    Dashboard: '首页',
    RequirementManagement: '导入需求',
    GenerateCases: '生成用例',
    ReviewExport: '评审与导出',
    ProjectStats: '数据统计',
    KnowledgeManagement: '知识图谱',
  }
  return map[String(route.name)] || '首页'
})

const activeMenu = computed(() => {
  const path = route.path.replace(/^\//, '')
  return path || 'dashboard'
})

function go(path: string) {
  router.push(path)
}
</script>

<template>
  <a-layout class="main-layout">
    <a-layout-header class="header">
      <div class="header-title">一体化智能测试管理平台</div>
      <div class="user-info">
        <span class="version-badge">{{ projectStore.currentProjectName }}</span>
      </div>
    </a-layout-header>

    <a-layout class="inner-layout">
      <a-layout-sider :width="186" :collapsed-width="52" :collapsed="false" class="sider" hide-trigger>
        <a-menu mode="vertical" :selected-keys="[activeMenu]" class="menu">
          <a-menu-item key="dashboard" @click="go('/dashboard')">
            首页
          </a-menu-item>
          <a-menu-item key="requirements" @click="go('/requirements')">
            导入需求
          </a-menu-item>
          <a-menu-item key="generate" @click="go('/generate')">
            生成用例
          </a-menu-item>
          <a-menu-item key="review-export" @click="go('/review-export')">
            评审与导出
          </a-menu-item>
          <a-menu-item key="stats" @click="go('/stats')">
            数据统计
          </a-menu-item>
          <a-menu-item key="knowledge-management" @click="go('/knowledge-management')">
            知识图谱
          </a-menu-item>
        </a-menu>
      </a-layout-sider>

      <a-layout-content class="content">
        <div class="content-header">一体化智能测试管理平台 / {{ title }}</div>
        <router-view />
      </a-layout-content>
    </a-layout>
  </a-layout>
</template>

<style scoped>
.main-layout {
  min-height: 100vh;
}
.header {
  height: 56px;
  padding: 0 16px;
  border-bottom: 1px solid #e5e7eb;
  background: var(--theme-header-bg);
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.header-title {
  color: #111827;
  font-size: 14px;
  font-weight: 700;
}
.left-section {
  display: flex;
  align-items: center;
}
.user-info {
  display: flex;
  align-items: center;
  gap: 10px;
}
.version-badge {
  padding: 4px 10px;
  border-radius: 999px;
  border: 1px solid #e5e7eb;
  background: #fff;
  font-size: 12px;
}
.user-dropdown {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
}
.inner-layout {
  min-height: calc(100vh - 56px);
}
.sider {
  background: var(--theme-sider-bg);
  border-right: 1px solid #e5e7eb;
  padding-top: 10px;
}
.menu {
  background: transparent;
}
.content {
  padding: 14px;
  background: var(--theme-page-bg);
}
.content-header {
  margin-bottom: 10px;
  color: #6b7280;
  font-size: 13px;
  font-weight: 600;
}
</style>
