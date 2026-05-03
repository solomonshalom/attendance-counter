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
	getHealth: () => request('/api/health'),

	// ---- cameras ----
	listCameras: () => request('/api/cameras'),
	getCamera: (/** @type {string} */ id) => request(`/api/cameras/${id}`),
	createCamera: (/** @type {{name:string,source:string,kind?:string,mirror?:boolean,enabled?:boolean,autostart?:boolean,confidence?:number,iou?:number,imgsz?:number,tracker?:string,loop_video?:boolean}} */ payload) =>
		request('/api/cameras', { method: 'POST', body: JSON.stringify(payload) }),
	updateCamera: (
		/** @type {string} */ id,
		/** @type {{name?:string,source?:string,kind?:string,mirror?:boolean,enabled?:boolean,confidence?:number,iou?:number,imgsz?:number,tracker?:string,loop_video?:boolean,paused?:boolean}} */ payload
	) => request(`/api/cameras/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
	deleteCamera: (/** @type {string} */ id) =>
		request(`/api/cameras/${id}`, { method: 'DELETE' }),
	startCamera: (/** @type {string} */ id) =>
		request(`/api/cameras/${id}/start`, { method: 'POST' }),
	stopCamera: (/** @type {string} */ id) =>
		request(`/api/cameras/${id}/stop`, { method: 'POST' }),
	playback: (/** @type {string} */ id, /** @type {'play'|'pause'|'restart'} */ action) =>
		request(`/api/cameras/${id}/playback`, {
			method: 'POST',
			body: JSON.stringify({ action })
		}),

	// ---- per-camera session ----
	startSession: (/** @type {string} */ cameraId, /** @type {string} */ label = '') =>
		request(`/api/cameras/${cameraId}/session/start`, {
			method: 'POST',
			body: JSON.stringify({ label })
		}),
	stopSession: (/** @type {string} */ cameraId) =>
		request(`/api/cameras/${cameraId}/session/stop`, { method: 'POST' }),
	resetCounts: (/** @type {string} */ cameraId) =>
		request(`/api/cameras/${cameraId}/session/reset`, { method: 'POST' }),
	setLine: (
		/** @type {string} */ cameraId,
		/** @type {{x1:number,y1:number,x2:number,y2:number}} */ line
	) =>
		request(`/api/cameras/${cameraId}/line`, {
			method: 'POST',
			body: JSON.stringify(line)
		}),
	setLines: (
		/** @type {string} */ cameraId,
		/** @type {{name?:string,x1:number,y1:number,x2:number,y2:number}[]} */ lines
	) =>
		request(`/api/cameras/${cameraId}/lines`, {
			method: 'POST',
			body: JSON.stringify({ lines })
		}),
	setZones: (
		/** @type {string} */ cameraId,
		/** @type {{name?:string,role?:string,polygon:{x:number,y:number}[]}[]} */ zones
	) =>
		request(`/api/cameras/${cameraId}/zones`, {
			method: 'POST',
			body: JSON.stringify({ zones })
		}),

	// ---- sessions (cross-camera history) ----
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

	// ---- videos ----
	listVideos: () => request('/api/videos'),
	uploadVideo: async (/** @type {File} */ file, /** @type {(p:number)=>void} */ onProgress) => {
		// XHR (instead of fetch) so we get progress events for big uploads.
		return new Promise((resolve, reject) => {
			const xhr = new XMLHttpRequest();
			xhr.open('POST', `${BASE}/api/videos/upload`);
			xhr.upload.onprogress = (e) => {
				if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
			};
			xhr.onload = () => {
				if (xhr.status >= 200 && xhr.status < 300) {
					try { resolve(JSON.parse(xhr.responseText)); }
					catch (e) { reject(e); }
				} else {
					let detail = `${xhr.status} ${xhr.statusText}`;
					try {
						const body = JSON.parse(xhr.responseText);
						if (body?.detail) detail = body.detail;
					} catch { /* non-json */ }
					reject(new Error(detail));
				}
			};
			xhr.onerror = () => reject(new Error('Upload failed'));
			const fd = new FormData();
			fd.append('file', file);
			xhr.send(fd);
		});
	},
	deleteVideo: (/** @type {string} */ filename) =>
		request(`/api/videos/${encodeURIComponent(filename)}`, { method: 'DELETE' })
};

/** @param {string} cameraId */
export function previewUrl(cameraId) {
	return `${BASE}/api/cameras/${cameraId}/preview.mjpg`;
}
