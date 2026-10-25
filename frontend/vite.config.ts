/// <reference types="vitest/config" />
import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 构建产物输出到后端的 static 目录，由 FastAPI 直接托管
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, 'src') },
  },
  build: {
    outDir: '../backend/evalp/static',
    emptyOutDir: true,
    chunkSizeWarningLimit: 4000,
  },
  worker: { format: 'es' },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:7860', changeOrigin: false },
      '/auth': { target: 'http://127.0.0.1:7860', changeOrigin: false },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
})
