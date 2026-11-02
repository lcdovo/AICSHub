import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  base: mode === 'production' ? '/static/' : '/',
  server: { port: 5174, proxy: { '/api': 'http://localhost:8100', '/health': 'http://localhost:8100' } },
  build: { outDir: '../static', emptyOutDir: true },
}));
