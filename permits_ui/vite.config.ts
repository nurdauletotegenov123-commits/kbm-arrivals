import path from 'path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Интерфейс нарядов — раздел портала: собирается в static/permits_ui/ основного сайта,
// страница /permits/ отдаёт его index.html (permits/spa.py). Всё локально, без CDN.
export default defineConfig(() => {
  const djangoTarget = process.env.DJANGO_URL || 'http://127.0.0.1:8000';
  return {
    base: '/static/permits_ui/',
    build: {
      outDir: path.resolve(__dirname, '../static/permits_ui'),
      emptyOutDir: true,
      chunkSizeWarningLimit: 1200,
    },
    server: {
      port: 3000,
      host: '0.0.0.0',
      // разработка интерфейса: npm run dev, запросы к API и файлам — на Django
      proxy: Object.fromEntries(['/api', '/permits_scans', '/static', '/login', '/logout'].map(p => [p, { target: djangoTarget, changeOrigin: true }])),
    },
    plugins: [react()],
    resolve: { alias: { '@': path.resolve(__dirname, '.') } },
  };
});
