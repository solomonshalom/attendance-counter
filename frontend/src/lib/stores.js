import { writable, derived } from 'svelte/store';
import { browser } from '$app/environment';

/** @typedef {{ x1: number, y1: number, x2: number, y2: number }} Line */

/** @typedef {{
 *   running: boolean,
 *   model_loaded: boolean,
 *   camera_open: boolean,
 *   fps: number,
 *   last_error: string|null,
 *   frame_width: number,
 *   frame_height: number,
 *   device: string,
 *   model_name: string,
 *   last_frame_at: number
 * }} CameraStatus */

/** @typedef {{
 *   camera_id: string,
 *   camera_name: string,
 *   source: string,
 *   mirror: boolean,
 *   enabled: boolean,
 *   session_id: string|null,
 *   session_label: string,
 *   session_started_at: number|null,
 *   in_count: number,
 *   out_count: number,
 *   inside: number,
 *   peak_inside: number,
 *   line: Line,
 *   status: CameraStatus
 * }} CameraState */

/** Map<camera_id, CameraState>. Re-emits on every change.
 *  @type {import('svelte/store').Writable<Record<string, CameraState>>} */
export const cameraStates = writable({});

/** Ordered list of camera_ids reflecting the manager's create-order.
 *  @type {import('svelte/store').Writable<string[]>} */
export const cameraOrder = writable([]);

/** Derived: ordered camera state objects. */
export const cameras = derived(
	[cameraStates, cameraOrder],
	([$states, $order]) => {
		const seen = new Set();
		const out = [];
		for (const id of $order) {
			if ($states[id]) {
				out.push($states[id]);
				seen.add(id);
			}
		}
		// Tail: any camera in $states not yet in order (race-protected).
		for (const id of Object.keys($states)) {
			if (!seen.has(id)) out.push($states[id]);
		}
		return out;
	}
);

/** @type {import('svelte/store').Writable<'connecting'|'connected'|'disconnected'>} */
export const connectionStatus = writable('connecting');

/**
 * @typedef {{ id: number, session_id: string, ts: number, kind: 'in'|'out', tracker_id: number|null }} CrossingEvent
 */

/** Map<camera_id, CrossingEvent[]>.
 *  @type {import('svelte/store').Writable<Record<string, CrossingEvent[]>>} */
export const eventsByCamera = writable({});

const MAX_RECENT_EVENTS = 50;

/** @param {string} cameraId @param {CrossingEvent} event */
export function pushEvent(cameraId, event) {
	if (!cameraId) return;
	eventsByCamera.update((m) => {
		const prev = m[cameraId] ?? [];
		const next = [event, ...prev].slice(0, MAX_RECENT_EVENTS);
		return { ...m, [cameraId]: next };
	});
}

/** @param {string} cameraId */
export function clearEventsForCamera(cameraId) {
	if (!cameraId) return;
	eventsByCamera.update((m) => ({ ...m, [cameraId]: [] }));
}

/** @param {string} cameraId */
export function dropCameraData(cameraId) {
	if (!cameraId) return;
	cameraStates.update((m) => {
		if (!(cameraId in m)) return m;
		const next = { ...m };
		delete next[cameraId];
		return next;
	});
	eventsByCamera.update((m) => {
		if (!(cameraId in m)) return m;
		const next = { ...m };
		delete next[cameraId];
		return next;
	});
	cameraOrder.update((arr) => arr.filter((id) => id !== cameraId));
}

/** @param {CameraState} cam */
export function upsertCamera(cam) {
	if (!cam?.camera_id) return;
	cameraStates.update((m) => ({ ...m, [cam.camera_id]: cam }));
	cameraOrder.update((arr) => (arr.includes(cam.camera_id) ? arr : [...arr, cam.camera_id]));
}

/** A writable store that mirrors localStorage so user choices persist.
 *  Falls back to in-memory when running outside the browser.
 *  @template T
 *  @param {string} key @param {T} initial
 *  @returns {import('svelte/store').Writable<T>} */
function persistedStore(key, initial) {
	let start = initial;
	if (browser) {
		try {
			const raw = localStorage.getItem(key);
			if (raw !== null) start = JSON.parse(raw);
		} catch {
			// corrupted — ignore and use default
		}
	}
	const store = writable(start);
	if (browser) {
		store.subscribe((value) => {
			try {
				localStorage.setItem(key, JSON.stringify(value));
			} catch {
				// quota or disabled — ignore
			}
		});
	}
	return store;
}

/** Whether the dashboard combines counts across all cameras with active sessions. */
export const showCombined = persistedStore('counter.showCombined', false);

/** Color theme: 'light' or 'dark'. Stored as plain string (not JSON) so the
 *  inline script in app.html can read it without parsing. Defaults to system
 *  preference on first load.
 *  @type {import('svelte/store').Writable<'light'|'dark'>} */
export const theme = (() => {
	/** @type {'light'|'dark'} */
	let initial = 'light';
	if (browser) {
		try {
			const saved = localStorage.getItem('counter.theme');
			if (saved === 'dark' || saved === 'light') {
				initial = saved;
			} else if (window.matchMedia?.('(prefers-color-scheme: dark)').matches) {
				initial = 'dark';
			}
		} catch {
			// ignore
		}
	}
	/** @type {import('svelte/store').Writable<'light'|'dark'>} */
	const store = writable(initial);
	if (browser) {
		store.subscribe((value) => {
			try {
				if (value === 'dark' || value === 'light') {
					localStorage.setItem('counter.theme', value);
				}
			} catch {
				// quota or disabled — ignore
			}
		});
	}
	return store;
})();

/** Replace the entire camera list (e.g., after a "cameras" snapshot from WS).
 *  @param {CameraState[]} list */
export function replaceCameras(list) {
	const order = list.map((c) => c.camera_id);
	const map = Object.fromEntries(list.map((c) => [c.camera_id, c]));
	cameraStates.set(map);
	cameraOrder.set(order);

	// Drop event histories for cameras that are gone.
	eventsByCamera.update((m) => {
		/** @type {Record<string, CrossingEvent[]>} */
		const next = {};
		for (const id of order) {
			if (m[id]) next[id] = m[id];
		}
		return next;
	});
}
