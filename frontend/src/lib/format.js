/** @param {number|null|undefined} ts seconds since epoch (Python time.time()). */
export function formatTime(ts) {
	if (!ts) return '—';
	const d = new Date(ts * 1000);
	return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

/** @param {number|null|undefined} ts */
export function formatDateTime(ts) {
	if (!ts) return '—';
	const d = new Date(ts * 1000);
	return d.toLocaleString([], {
		month: 'short',
		day: 'numeric',
		hour: '2-digit',
		minute: '2-digit'
	});
}

/** @param {number|null|undefined} startTs @param {number|null|undefined} endTs */
export function formatDuration(startTs, endTs) {
	if (!startTs) return '—';
	const end = endTs ? endTs * 1000 : Date.now();
	const seconds = Math.max(0, Math.floor((end - startTs * 1000) / 1000));
	const h = Math.floor(seconds / 3600);
	const m = Math.floor((seconds % 3600) / 60);
	const s = seconds % 60;
	if (h > 0) return `${h}h ${m}m`;
	if (m > 0) return `${m}m ${s}s`;
	return `${s}s`;
}
