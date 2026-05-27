import path from 'path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
    const apiBase = process.env.VITE_API_BASE_URL || 'http://localhost:8000';
    return {
      server: {
        port: 3000,
        host: '0.0.0.0',
        proxy: {
          '/research-runs': { target: apiBase, changeOrigin: true },
          '/api/v1':        { target: apiBase, changeOrigin: true },
          '/health':        { target: apiBase, changeOrigin: true },
        }
      },
      plugins: [react()],
      resolve: {
        alias: {
          '@': path.resolve(__dirname, 'src'),
        }
      }
    };
});
