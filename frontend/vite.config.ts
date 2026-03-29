import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vite';

const backendPort = process.env.VITE_BACKEND_PORT || '7889';
const backendHost = process.env.VITE_BACKEND_HOST || 'localhost';
const backendUrl = process.env.VITE_BACKEND_URL || `http://${backendHost}:${backendPort}`;

export default defineConfig({
  plugins: [tailwindcss(), sveltekit()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: backendUrl,
        changeOrigin: true,
      },
      '/health': {
        target: backendUrl,
        changeOrigin: true,
      },
      '/docs': {
        target: backendUrl,
        changeOrigin: true,
      },
      '/openapi.json': {
        target: backendUrl,
        changeOrigin: true,
      },
    },
  },
});
