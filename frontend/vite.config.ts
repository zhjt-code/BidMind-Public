import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import { pdfjsStaticAssets } from './vite-plugin-pdfjs-assets'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), pdfjsStaticAssets()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 3000,
    open: false, // 禁用自动打开浏览器
    // H: 等非系统盘上 chokidar 常不触发，导致改 TSX 后页面仍跑旧模块
    watch: {
      usePolling: true,
      interval: 300,
    },
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
