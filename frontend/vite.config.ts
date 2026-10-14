import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  base: '/',
  plugins: [vue(), {
    name: 'chat-at-root',
    configureServer(server) {
      server.middlewares.use((request, _response, next) => {
        if (request.url?.split('?')[0] === '/') request.url = '/chat/index.html' + (request.url.includes('?') ? request.url.slice(request.url.indexOf('?')) : '')
        next()
      })
    },
  }],
  build: {rollupOptions: {input: {
    chat: fileURLToPath(new URL('./chat/index.html', import.meta.url)),
    studio: fileURLToPath(new URL('./studio/index.html', import.meta.url)),
  }}},
  server: {host: '127.0.0.1', port: 5173, proxy: {'/api': 'http://127.0.0.1:2237'}},
})
