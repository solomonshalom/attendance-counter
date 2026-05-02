import { writable, derived } from 'svelte/store';

/** @typedef {{
 *   session_id: string|null,
 *   session_label: string,
 *   session_started_at: number|null,
 *   in_count: number,
 *   out_count: number,
 *   inside: number,
 *   peak_inside: number,
 *   line: { x1: number, y1: number, x2: number, y2: number },
 *   status: {
 *     running: boolean,
 *     model_loaded: boolean,
 *     camera_open: boolean,
 *     fps: number,
 *     last_error: string|null,
 *     frame_width: number,
 *     frame_height: number,
 *     device: string,
 *     model_name: string,
 *     last_frame_at: number
 *   }
 * }} CounterState */

/** @type {import('svelte/store').Writable<CounterState|null>} */
export const counterState = writable(null);

/** @type {import('svelte/store').Writable<'connecting'|'connected'|'disconnected'>} */
export const connectionStatus = writable('connecting');

/**
 * @typedef {{ id: number, session_id: string, ts: number, kind: 'in'|'out', tracker_id: number|null }} CrossingEvent
 * @type {import('svelte/store').Writable<CrossingEvent[]>}
 */
export const recentEvents = writable([]);

const MAX_RECENT_EVENTS = 50;

/** @param {CrossingEvent} event */
export function pushEvent(event) {
	recentEvents.update((events) => {
		const next = [event, ...events];
		return next.slice(0, MAX_RECENT_EVENTS);
	});
}

export function clearEvents() {
	recentEvents.set([]);
}

export const isSessionActive = derived(counterState, ($state) =>
	Boolean($state?.session_id)
);
