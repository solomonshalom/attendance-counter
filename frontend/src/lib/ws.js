/**
 * WebSocket client with automatic reconnect and exponential backoff.
 * Falls back to long-polling /api/cameras if the WS handshake repeatedly fails.
 */

import { browser } from '$app/environment';
import {
	connectionStatus,
	pushEvent,
	replaceCameras,
	upsertCamera
} from './stores.js';
import { api } from './api.js';

const RECONNECT_BASE_MS = 500;
const RECONNECT_MAX_MS = 15000;
const POLL_INTERVAL_MS = 2000;

class CounterClient {
	/** @type {WebSocket|null} */
	socket = null;
	/** @type {number|null} */
	reconnectTimer = null;
	/** @type {number|null} */
	pollTimer = null;
	failureCount = 0;
	stopped = false;

	connect() {
		if (!browser) return;
		this.stopped = false;
		this._openSocket();
	}

	disconnect() {
		this.stopped = true;
		if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
		if (this.pollTimer) clearTimeout(this.pollTimer);
		this.reconnectTimer = null;
		this.pollTimer = null;
		if (this.socket) {
			try {
				this.socket.close();
			} catch {
				// ignore
			}
			this.socket = null;
		}
		connectionStatus.set('disconnected');
	}

	_openSocket() {
		if (this.stopped) return;
		connectionStatus.set('connecting');

		const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
		const url = `${proto}//${window.location.host}/ws`;
		let socket;
		try {
			socket = new WebSocket(url);
		} catch (err) {
			console.warn('WebSocket construction failed', err);
			this._scheduleReconnect();
			this._startPolling();
			return;
		}
		this.socket = socket;

		socket.addEventListener('open', () => {
			this.failureCount = 0;
			connectionStatus.set('connected');
			this._stopPolling();
		});

		socket.addEventListener('message', (e) => {
			try {
				const msg = JSON.parse(e.data);
				if (msg.type === 'state' && msg.data) {
					upsertCamera(msg.data);
				} else if (msg.type === 'event' && msg.data && msg.camera_id) {
					pushEvent(msg.camera_id, msg.data);
				} else if (msg.type === 'cameras' && Array.isArray(msg.data)) {
					replaceCameras(msg.data);
				}
			} catch (err) {
				console.warn('Bad WS message', err);
			}
		});

		socket.addEventListener('close', () => {
			this.socket = null;
			if (this.stopped) return;
			connectionStatus.set('disconnected');
			this._scheduleReconnect();
			this._startPolling();
		});

		socket.addEventListener('error', () => {
			// 'close' will follow; handle reconnect there.
		});
	}

	_scheduleReconnect() {
		if (this.stopped) return;
		this.failureCount += 1;
		const delay = Math.min(
			RECONNECT_MAX_MS,
			RECONNECT_BASE_MS * 2 ** Math.min(this.failureCount, 6)
		);
		if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
		this.reconnectTimer = window.setTimeout(() => this._openSocket(), delay);
	}

	_startPolling() {
		if (this.pollTimer) return;
		const tick = async () => {
			try {
				const list = await api.listCameras();
				if (Array.isArray(list)) replaceCameras(list);
			} catch {
				// keep retrying silently
			}
			if (!this.stopped) {
				this.pollTimer = window.setTimeout(tick, POLL_INTERVAL_MS);
			}
		};
		this.pollTimer = window.setTimeout(tick, POLL_INTERVAL_MS);
	}

	_stopPolling() {
		if (this.pollTimer) {
			clearTimeout(this.pollTimer);
			this.pollTimer = null;
		}
	}
}

export const counterClient = new CounterClient();
