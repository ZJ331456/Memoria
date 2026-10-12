import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
export default defineConfig({ base: '/studio/', plugins: [vue()], server: {port: 5173, proxy: {'/api': 'http://127.0.0.1:2237'}} })
