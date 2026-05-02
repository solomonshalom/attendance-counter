import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig, loadEnv } from 'vite';

const COUNTER_API =
	loadEnv('', process.cwd(), 'COUNTER_').COUNTER_API_URL || 'http://127.0.0.1:8765';
const COUNTER_WS = COUNTER_API.replace(/^http/, 'ws');

export default defineConfig({
	plugins: [sveltekit()],
	server: {
		proxy: {
			'/api': {
				target: COUNTER_API,
				changeOrigin: true
			},
			'/ws': {
				target: COUNTER_WS,
				ws: true,
				changeOrigin: true
			}
		}
	}
});
