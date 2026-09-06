import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  base: process.env.NODE_ENV === 'production' ? '/static/' : '/',
  server: { port: 5173, proxy: { '/api': 'http://localhost:8000', '/health': 'http://localhost:8000' } },
  build: { outDir: '../static', emptyOutDir: true },
});
