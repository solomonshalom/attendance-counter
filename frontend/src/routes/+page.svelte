<script>
// @ts-nocheck

import { onMount, onDestroy } from 'svelte';
import {
	cameras as camerasStore,
	connectionStatus,
	showCombined
} from '$lib/stores.js';
import { counterClient } from '$lib/ws.js';
import { api } from '$lib/api.js';
import { formatDateTime, formatDuration } from '$lib/format.js';
import CamerasSection from '$lib/components/CamerasSection.svelte';
import CameraCard from '$lib/components/CameraCard.svelte';
import CombinedTotals from '$lib/components/CombinedTotals.svelte';
import ThemeToggle from '$lib/components/ThemeToggle.svelte';
import VenuesSection from '$lib/components/VenuesSection.svelte';

let sessions = $state([]);
let sessionsLoading = $state(false);
let now = $state(Date.now());

let copyrightYear = new Date().getFullYear();

const cams = $derived($camerasStore);
const cameraNames = $derived(
	Object.fromEntries(cams.map((c) => [c.camera_id, c.camera_name]))
);
const activeSessionId = $derived(cams.find((c) => c.session_id)?.session_id ?? null);

let nowTimer;
onMount(() => {
	counterClient.connect();
	loadSessions();
	nowTimer = setInterval(() => (now = Date.now()), 1000);
});

onDestroy(() => {
	counterClient.disconnect();
	clearInterval(nowTimer);
});

async function loadSessions() {
	sessionsLoading = true;
	try {
		sessions = await api.listSessions(50);
	} catch (err) {
		console.warn('Failed to load sessions', err);
	} finally {
		sessionsLoading = false;
	}
}

// Refresh history when any camera's session_id changes (start or stop).
let lastSessionFingerprint = '';
$effect(() => {
	const fp = cams.map((c) => `${c.camera_id}:${c.session_id ?? ''}`).join('|');
	if (fp !== lastSessionFingerprint) {
		lastSessionFingerprint = fp;
		loadSessions();
	}
});

async function deleteSession(id) {
	if (!confirm('Delete this session and all its events?')) return;
	try {
		await api.deleteSession(id);
		await loadSessions();
	} catch (err) {
		alert(err?.message ?? String(err));
	}
}

function exportSession(id) {
	window.open(api.exportSessionUrl(id), '_blank');
}

function statusBadge() {
	if ($connectionStatus === 'connected') return { text: 'Connected', cls: 'badge-ok' };
	if ($connectionStatus === 'connecting') return { text: 'Connecting…', cls: 'badge-warn' };
	return { text: 'Offline', cls: 'badge-err' };
}

const badge = $derived(statusBadge());
</script>

<section class="py-4 py-xl-5">
	<div class="container" style="max-width: 1100px;">
		<div class="p-3 p-lg-4">
			<!-- Header -->
			<div class="dash-header">
				<h1 class="dash-title">
					<span class="dash-emoji">⛪</span>
					Attendance Counter
				</h1>
				<div class="dash-meta">
					<span class="badge-pill {badge.cls}">{badge.text}</span>
					<span class="badge-pill badge-muted">{cams.length} camera{cams.length === 1 ? '' : 's'}</span>
					<ThemeToggle />
				</div>
			</div>
			<p class="dash-sub">
				Real-time counting of people entering and exiting your venue, powered by
				YOLO and on-device computer vision. No images leave this Mac.
			</p>

			<!-- Venues (multi-camera dedupe via spatial calibration) -->
			<VenuesSection cameras={cams} />

			<!-- Cameras management section -->
			<CamerasSection cameras={cams} />

			<!-- Combined totals (opt-in, only meaningful with multiple cameras) -->
			{#if $showCombined && cams.length >= 2}
				<CombinedTotals cameras={cams} />
			{/if}

			<!-- Per-camera cards -->
			{#if cams.length === 0}
				<div class="empty-state">
					<p>Add a camera above to start counting.</p>
				</div>
			{:else}
				{#each cams as cam (cam.camera_id)}
					<CameraCard camera={cam} />
				{/each}
			{/if}

			<!-- History -->
			<div class="history-card">
				<div class="history-header">
					<span>Past sessions</span>
					<button class="btn-light-pill" onclick={loadSessions} disabled={sessionsLoading}>
						<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none">
							<path d="M4 4v5h5M20 20v-5h-5M19.94 11A8 8 0 005 6.34M4.06 13A8 8 0 0019 17.66" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
						</svg>
						Refresh
					</button>
				</div>
				{#if sessions.length === 0}
					<div class="history-empty">
						<p>No past sessions yet. Start one above.</p>
					</div>
				{:else}
					<div class="history-table-wrap">
						<table class="history-table">
							<thead>
								<tr>
									<th>Session</th>
									<th>Camera</th>
									<th>Started</th>
									<th>Duration</th>
									<th class="num">In</th>
									<th class="num">Out</th>
									<th class="num">Peak</th>
									<th class="actions">Actions</th>
								</tr>
							</thead>
							<tbody>
								{#each sessions as s (s.id)}
									<tr class:active={s.id === activeSessionId}>
										<td>
											{#if s.id === activeSessionId}
												<span class="dot-inline"></span>
											{/if}
											{s.label || '—'}
										</td>
										<td>{s.camera_id ? (cameraNames[s.camera_id] ?? '—') : '—'}</td>
										<td>{formatDateTime(s.started_at)}</td>
										<td>{formatDuration(s.started_at, s.ended_at)}</td>
										<td class="num">{s.in_count}</td>
										<td class="num">{s.out_count}</td>
										<td class="num">{s.peak_inside}</td>
										<td class="actions">
											<button
												class="icon-btn"
												title="Export CSV"
												onclick={() => exportSession(s.id)}
											>
												<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none">
													<path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
												</svg>
											</button>
											{#if s.id !== activeSessionId}
												<button
													class="icon-btn icon-btn-danger"
													title="Delete"
													onclick={() => deleteSession(s.id)}
												>
													<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none">
														<path d="M19 7L18.13 19.14C18.06 20.19 17.19 21 16.14 21H7.86C6.81 21 5.94 20.19 5.87 19.14L5 7M10 11v6M14 11v6M15 7V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
													</svg>
												</button>
											{/if}
										</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				{/if}
			</div>

			<!-- Footer -->
			<div class="dash-footer">
				<div></div>
				<div>Copyright © {copyrightYear}</div>
			</div>
		</div>
	</div>
</section>

<style>
.dash-header {
	display: flex;
	align-items: center;
	justify-content: space-between;
	gap: 12px;
	flex-wrap: wrap;
	margin-bottom: 8px;
}

.dash-title {
	font-family: 'Inter Tight', sans-serif;
	font-weight: 600;
	font-size: 32px;
	margin: 0;
	color: rgb(33, 37, 41);
	display: flex;
	align-items: center;
	gap: 8px;
}

.dash-emoji {
	font-weight: normal;
}

.dash-meta {
	display: flex;
	gap: 8px;
	flex-wrap: wrap;
	align-items: center;
}

@media (max-width: 640px) {
	.dash-meta {
		position: fixed;
		bottom: 0;
		left: 0;
		right: 0;
		margin: 0;
		padding: 10px 16px calc(10px + env(safe-area-inset-bottom)) 16px;
		background: rgba(255, 255, 255, 0.95);
		backdrop-filter: blur(8px);
		-webkit-backdrop-filter: blur(8px);
		border-top: 1px solid rgb(215, 215, 215);
		box-shadow: 0 -2px 8px rgba(0, 0, 0, 0.04);
		justify-content: center;
		z-index: 100;
	}

	.dash-header {
		justify-content: flex-start;
	}

	/* Reserve space so the floating bar doesn't cover page-bottom content. */
	:global(body) {
		padding-bottom: calc(64px + env(safe-area-inset-bottom));
	}
}

.dash-sub {
	color: #495057;
	font-size: 16px;
	margin-bottom: 24px;
}

.badge-pill {
	display: inline-flex;
	align-items: center;
	gap: 6px;
	font-size: 13px;
	font-weight: 500;
	padding: 4px 12px;
	border-radius: 999px;
	border: 1px solid transparent;
}

.badge-ok {
	background: #d1f4e0;
	color: #146c43;
	border-color: #b5e8c8;
}

.badge-warn {
	background: #fff3cd;
	color: #856404;
	border-color: #ffeeba;
}

.badge-err {
	background: #fdeaec;
	color: #b02a37;
	border-color: #f5c2c7;
}

.badge-muted {
	background: #f1f3f5;
	color: #495057;
	border-color: #e9ecef;
	font-family: 'JetBrains Mono', ui-monospace, monospace;
	font-size: 12px;
}

.empty-state {
	background: #fff;
	border: 2px dashed rgb(215, 215, 215);
	border-radius: 16px;
	padding: 48px 24px;
	text-align: center;
	color: #6c757d;
	margin-bottom: 16px;
}

.empty-state p {
	margin: 0;
	font-size: 15px;
}

.btn-light-pill {
	display: inline-flex;
	align-items: center;
	gap: 6px;
	border-radius: 12px;
	padding: 6px 14px;
	font-size: 14px;
	font-weight: 500;
	cursor: pointer;
	border: 2px solid rgb(33, 37, 41);
	background: #fff;
	color: rgb(33, 37, 41);
	font-family: inherit;
}

.btn-light-pill:disabled {
	opacity: 0.45;
	cursor: not-allowed;
}

.history-card {
	background: #fff;
	border: 2px solid rgb(215, 215, 215);
	border-radius: 16px;
	padding: 16px;
	margin-bottom: 24px;
}

.history-header {
	display: flex;
	justify-content: space-between;
	align-items: center;
	font-weight: 600;
	font-size: 16px;
	margin-bottom: 12px;
}

.history-empty {
	border: 2px dashed rgb(215, 215, 215);
	border-radius: 12px;
	padding: 24px;
	text-align: center;
	color: #6c757d;
}

.history-table-wrap {
	overflow-x: auto;
}

.history-table {
	width: 100%;
	border-collapse: collapse;
	font-size: 14px;
}

.history-table th,
.history-table td {
	padding: 10px 12px;
	text-align: left;
	border-bottom: 1px solid #f1f3f5;
}

.history-table th {
	font-weight: 600;
	color: #495057;
	font-size: 12px;
	text-transform: uppercase;
	letter-spacing: 0.04em;
}

.history-table td.num,
.history-table th.num {
	text-align: right;
	font-variant-numeric: tabular-nums;
}

.history-table td.actions,
.history-table th.actions {
	text-align: right;
	white-space: nowrap;
}

.history-table tr.active td {
	background: #fff8d4;
}

.dot-inline {
	display: inline-block;
	width: 8px;
	height: 8px;
	border-radius: 999px;
	background: #28a745;
	margin-right: 6px;
	vertical-align: middle;
}

.icon-btn {
	background: transparent;
	border: 1px solid #e9ecef;
	border-radius: 8px;
	padding: 4px 8px;
	cursor: pointer;
	color: #495057;
	margin-left: 4px;
	display: inline-flex;
	align-items: center;
}

.icon-btn:hover {
	background: #f1f3f5;
}

.icon-btn-danger {
	color: #dc3545;
	border-color: #f5c2c7;
}

.icon-btn-danger:hover {
	background: #fdeaec;
}

.dash-footer {
	display: flex;
	justify-content: space-between;
	align-items: center;
	gap: 12px;
	padding-top: 16px;
	border-top: 1px solid #e9ecef;
	color: #6c757d;
	font-size: 13px;
	flex-wrap: wrap;
}
</style>
