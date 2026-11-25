/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

/* The app calls `${VITE_BFF_URL}/api/...`. With VITE_BFF_URL empty (the default) the calls stay on the
   page origin and the dev server proxies /api to BFF_PROXY_TARGET (the local BFF, contracts/interfaces.md). */
const bff = process.env.BFF_PROXY_TARGET ?? 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, fs: { allow: ['..'] }, proxy: { '/api': { target: bff, changeOrigin: true } } },
  preview: { port: 5173, proxy: { '/api': { target: bff, changeOrigin: true } } },
  test: {
    environment: 'jsdom',
    include: ['tests/unit/**/*.test.{ts,tsx}'],
    setupFiles: ['tests/unit/setup.ts'],
    css: false,
  },
});
