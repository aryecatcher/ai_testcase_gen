import { defineStore } from 'pinia'

export const useProjectStore = defineStore('project', {
  state: () => ({
    currentProjectId: 'ai-testcase-gen',
    currentProjectName: '当前项目',
  }),
  actions: {
    setProject(id: string, name: string) {
      this.currentProjectId = id
      this.currentProjectName = name
    },
  },
})
