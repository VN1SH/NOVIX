import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://novix-backend:8000',
        changeOrigin: true
      },
      '/ws': {
        target: 'ws://novix-backend:8000',
        ws: true
      }
    }
  }
})
