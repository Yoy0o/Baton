import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// 开发时把接口、桥接 SDK 和 WebSocket 代理到 Agent 服务
const AGENT = process.env.BATON_AGENT_URL || 'http://localhost:8000';

export default defineConfig({
  base: '/workbench/',
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': AGENT,
      '/sdk': AGENT,
      '/ws': { target: AGENT.replace(/^http/, 'ws'), ws: true },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    chunkSizeWarningLimit: 3000,
  },
});
