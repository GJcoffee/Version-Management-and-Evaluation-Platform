/// <reference types="vitest/config" />
import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 开发代理的后端端口：EVALP_BACKEND_PORT（默认 7860）；host 可用 EVALP_BACKEND_HOST 覆盖
const backend = `http://${process.env.EVALP_BACKEND_HOST ?? '127.0.0.1'}:${process.env.EVALP_BACKEND_PORT ?? '7860'}`

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
      '/api': { target: backend, changeOrigin: false },
      '/auth': { target: backend, changeOrigin: false },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
})
