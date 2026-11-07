import { defineConfig } from 'vite';

export default defineConfig({
  base: './',
  build: { outDir: 'dist', emptyOutDir: true, chunkSizeWarningLimit: 1000 },
  server: { proxy: { '/api': 'http://127.0.0.1:8765' } },
});
