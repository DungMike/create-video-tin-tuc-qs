import path from 'node:path'

import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig(({ mode }) => {
  const rootDir = path.resolve(__dirname, '..')
  const env = loadEnv(mode, rootDir, '')
  const frontendHost = env.FRONTEND_HOST || '127.0.0.1'
  const frontendPort = Number(env.FRONTEND_PORT || 5173)
  const backendHost = env.WEB_HOST || '127.0.0.1'
  const backendPort = Number(env.WEB_PORT || 5000)

  return {
    envDir: rootDir,
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    server: {
      host: frontendHost,
      port: frontendPort,
      proxy: {
        '/api': `http://${backendHost}:${backendPort}`,
        '/media': `http://${backendHost}:${backendPort}`,
      },
    },
    preview: {
      host: frontendHost,
      port: frontendPort,
    },
    build: {
      outDir: 'dist',
      emptyOutDir: true,
    },
  }
})
