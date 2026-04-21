import { defineStore } from 'pinia'

export const useThemeStore = defineStore('theme', {
  state: () => ({
    mode: (localStorage.getItem('theme-mode') as 'light' | 'black') || 'light',
  }),
  getters: {
    isBlack: (state) => state.mode === 'black',
  },
  actions: {
    initializeTheme() {
      document.documentElement.setAttribute('data-theme', this.mode)
    },
    toggleTheme() {
      this.mode = this.mode === 'light' ? 'black' : 'light'
      localStorage.setItem('theme-mode', this.mode)
      this.initializeTheme()
    },
  },
})

