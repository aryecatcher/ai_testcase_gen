import { createRouter, createWebHistory } from 'vue-router'
import MainLayout from '../layouts/MainLayout.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      component: MainLayout,
      redirect: '/dashboard',
      children: [
        { path: 'dashboard', name: 'Dashboard', component: () => import('../views/DashboardView.vue') },
        { path: 'requirements', name: 'RequirementManagement', component: () => import('../views/RequirementManagementView.vue') },
        { path: 'generate', name: 'GenerateCases', component: () => import('../views/GenerateCasesView.vue') },
        { path: 'review-export', name: 'ReviewExport', component: () => import('../views/ReviewExportView.vue') },
        { path: 'stats', name: 'ProjectStats', component: () => import('../views/ProjectStatsView.vue') },
        { path: 'knowledge-management', name: 'KnowledgeManagement', component: () => import('../views/KnowledgeManagementView.vue') },
      ],
    },
  ],
})

export default router
