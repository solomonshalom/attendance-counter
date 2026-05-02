/**
 * Wrapper for counter REST API. All requests go through Vite's proxy in dev,
 * or the same origin in production (when frontend and counter are co-deployed).
 */

const BASE = '';

/** @param {Response} res */
async function ensureOk(res) {
	if (!res.ok) {
		let detail = res.statusText;
		try {
			const body = await res.json();
			if (body?.detail) detail = body.detail;
		} catch {
			// non-JSON body
		}
		throw new Error(`${res.status} ${detail}`);
	}
	return res;
}

/** @param {string} path @param {RequestInit} [init] */
async function request(path, init) {
	const res = await fetch(`${BASE}${path}`, {
		headers: { 'Content-Type': 'application/json' },
		...init
	});
	await ensureOk(res);
	if (res.status === 204) return null;
	return res.json();
}

export const api = {
	getState: () => request('/api/state'),
	getHealth: () => request('/api/health'),

	startSession: (/** @type {string} */ label = '') =>
		request('/api/session/start', {
			method: 'POST',
			body: JSON.stringify({ label })
		}),
	stopSession: () => request('/api/session/stop', { method: 'POST' }),
	resetCounts: () => request('/api/session/reset', { method: 'POST' }),

	listSessions: (/** @type {number} */ limit = 100) =>
		request(`/api/sessions?limit=${limit}`),
	getSession: (/** @type {string} */ id) => request(`/api/sessions/${id}`),
	updateSession: (/** @type {string} */ id, /** @type {string} */ label) =>
		request(`/api/sessions/${id}`, {
			method: 'PATCH',
			body: JSON.stringify({ label })
		}),
	deleteSession: (/** @type {string} */ id) =>
		request(`/api/sessions/${id}`, { method: 'DELETE' }),
	listEvents: (/** @type {string} */ id, /** @type {number} */ limit = 200) =>
		request(`/api/sessions/${id}/events?limit=${limit}`),
	exportSessionUrl: (/** @type {string} */ id) =>
		`${BASE}/api/sessions/${id}/export.csv`,

	setLine: (/** @type {{x1:number,y1:number,x2:number,y2:number}} */ line) =>
		request('/api/line', {
			method: 'POST',
			body: JSON.stringify(line)
		})
};

export const PREVIEW_URL = `${BASE}/api/preview.mjpg`;
