<script>
// @ts-nocheck

import { onMount, onDestroy } from 'svelte';
import { counterState, connectionStatus, recentEvents, isSessionActive, clearEvents } from '$lib/stores.js';
import { counterClient } from '$lib/ws.js';
import { api } from '$lib/api.js';
import { formatTime, formatDateTime, formatDuration } from '$lib/format.js';
import LinePreview from '$lib/components/LinePreview.svelte';

let sessions = $state([]);
let sessionsLoading = $state(false);
let sessionLabel = $state('');
let actionError = $state(null);
let actionLoading = $state(false);
let now = $state(Date.now());

let copyrightYear = new Date().getFullYear();

const status = $derived($counterState?.status ?? null);
const session = $derived($counterState ?? null);

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

async function withAction(fn) {
	actionError = null;
	actionLoading = true;
	try {
		await fn();
	} catch (err) {
		actionError = err.message ?? String(err);
	} finally {
		actionLoading = false;
	}
}

const startSession = () =>
	withAction(async () => {
		await api.startSession(sessionLabel.trim());
		sessionLabel = '';
		clearEvents();
		await loadSessions();
	});

const stopSession = () =>
	withAction(async () => {
		await api.stopSession();
		await loadSessions();
	});

const resetCounts = () =>
	withAction(async () => {
		await api.resetCounts();
		clearEvents();
	});

async function deleteSession(id) {
	if (!confirm('Delete this session and all its events?')) return;
	await withAction(async () => {
		await api.deleteSession(id);
		await loadSessions();
	});
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
					{#if status?.device}
						<span class="badge-pill badge-muted">
							{status.model_name || 'model'} · {status.device.toUpperCase()}
						</span>
					{/if}
					{#if status?.fps}
						<span class="badge-pill badge-muted">{status.fps.toFixed(1)} FPS</span>
					{/if}
				</div>
			</div>
			<p class="dash-sub">
				Real-time counting of people entering and exiting your venue, powered by
				YOLO and on-device computer vision. No images leave this Mac.
			</p>

			{#if status?.last_error}
				<div class="alert-card">
					<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none">
						<path d="M12 9V11M12 15H12.01M5.07183 19H18.9282C20.4678 19 21.4301 17.3333 20.6603 16L13.7321 4C12.9623 2.66667 11.0378 2.66667 10.268 4L3.33978 16C2.56998 17.3333 3.53223 19 5.07183 19Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
					</svg>
					<div>
						<strong>Counter error:</strong> {status.last_error}
					</div>
				</div>
			{/if}

			<!-- Count cards -->
			<div class="count-grid">
				<div class="count-card count-card-in">
					<div class="count-label">Entered</div>
					<div class="count-value">{session?.in_count ?? 0}</div>
					<div class="count-meta">
						<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none">
							<path d="M5 12h14M13 6l6 6-6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
						</svg>
						across the line
					</div>
				</div>
				<div class="count-card count-card-out">
					<div class="count-label">Left</div>
					<div class="count-value">{session?.out_count ?? 0}</div>
					<div class="count-meta">
						<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none">
							<path d="M19 12H5M11 18l-6-6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
						</svg>
						across the line
					</div>
				</div>
				<div class="count-card count-card-inside">
					<div class="count-label">Inside now</div>
					<div class="count-value">{session?.inside ?? 0}</div>
					<div class="count-meta">in − out</div>
				</div>
				<div class="count-card count-card-peak">
					<div class="count-label">Peak attendance</div>
					<div class="count-value">{session?.peak_inside ?? 0}</div>
					<div class="count-meta">most ever inside</div>
				</div>
			</div>

			<!-- Session controls -->
			<div class="control-card">
				<div class="control-info">
					{#if $isSessionActive}
						<div class="status-dot status-active" aria-hidden="true"></div>
						<div>
							<div class="control-title">
								Session live
								{#if session.session_label}
									· <span class="control-label">{session.session_label}</span>
								{/if}
							</div>
							<div class="control-sub">
								Started {formatTime(session.session_started_at)} ·
								{formatDuration(session.session_started_at, null)} elapsed
							</div>
						</div>
					{:else}
						<div class="status-dot status-idle" aria-hidden="true"></div>
						<div>
							<div class="control-title">Idle</div>
							<div class="control-sub">Start a session to begin counting attendance.</div>
						</div>
					{/if}
				</div>

				<div class="control-actions">
					{#if !$isSessionActive}
						<input
							type="text"
							class="control-input"
							placeholder="Session name (optional)"
							bind:value={sessionLabel}
							maxlength="200"
						/>
						<button
							class="btn-primary-pill"
							onclick={startSession}
							disabled={actionLoading || !status?.model_loaded}
						>
							<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none">
								<path d="M5 3l14 9-14 9V3z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="currentColor"/>
							</svg>
							Start session
						</button>
					{:else}
						<button class="btn-light-pill" onclick={resetCounts} disabled={actionLoading}>
							Reset counts
						</button>
						<button class="btn-danger-pill" onclick={stopSession} disabled={actionLoading}>
							<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none">
								<rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor"/>
							</svg>
							End session
						</button>
					{/if}
				</div>
			</div>

			{#if actionError}
				<div class="alert-card alert-error">{actionError}</div>
			{/if}

			<!-- Live preview + Event feed -->
			<div class="row-grid">
				<div class="col-main">
					<LinePreview
						line={session?.line ?? { x1: 0.5, y1: 0, x2: 0.5, y2: 1 }}
						frameWidth={status?.frame_width || 1280}
						frameHeight={status?.frame_height || 720}
						cameraOpen={status?.camera_open ?? false}
					/>
				</div>
				<div class="col-side">
					<div class="feed-card">
						<div class="feed-header">
							<span>Recent crossings</span>
							<span class="feed-count">{$recentEvents.length}</span>
						</div>
						{#if $recentEvents.length === 0}
							<div class="feed-empty">
								<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none">
									<circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2"/>
									<path d="M12 7v5l3 2" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
								</svg>
								<p>Waiting for someone to cross the line.</p>
							</div>
						{:else}
							<ul class="feed-list">
								{#each $recentEvents as ev (ev.id)}
									<li class="feed-item">
										<span class="feed-kind feed-kind-{ev.kind}">
											{#if ev.kind === 'in'}
												<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none">
													<path d="M5 12h14M13 6l6 6-6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
												</svg>
												Entered
											{:else}
												<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none">
													<path d="M19 12H5M11 18l-6-6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
												</svg>
												Left
											{/if}
										</span>
										<span class="feed-time">{formatTime(ev.ts)}</span>
										<span class="feed-track">#{ev.tracker_id ?? '?'}</span>
									</li>
								{/each}
							</ul>
						{/if}
					</div>
				</div>
			</div>

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
									<tr class:active={s.id === session?.session_id}>
										<td>
											{#if s.id === session?.session_id}
												<span class="dot-inline"></span>
											{/if}
											{s.label || '—'}
										</td>
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
											{#if s.id !== session?.session_id}
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
				<div>
					{#if status}
						<span class="footer-meta">
							<span>Camera:</span> {status.camera_open ? 'open' : 'closed'} ·
							<span>Model:</span> {status.model_loaded ? 'loaded' : 'loading'} ·
							<span>Frame:</span> {status.frame_width || '?'}×{status.frame_height || '?'}
						</span>
					{/if}
				</div>
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

.alert-card {
	display: flex;
	gap: 10px;
	align-items: flex-start;
	padding: 12px 16px;
	background: #fdeaec;
	color: #842029;
	border: 2px solid #f5c2c7;
	border-radius: 12px;
	margin-bottom: 16px;
}

.alert-error {
	margin-top: 12px;
}

/* Count cards */
.count-grid {
	display: grid;
	grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
	gap: 16px;
	margin-bottom: 24px;
}

.count-card {
	background: #fff;
	border: 2px solid rgb(215, 215, 215);
	border-radius: 16px;
	padding: 20px;
}

.count-card-in {
	background: #f0f8ff;
	border-color: #cfe2ff;
}

.count-card-out {
	background: #fff5f5;
	border-color: #f5c2c7;
}

.count-card-inside {
	background: #fff8d4;
	border-color: #ffe69c;
}

.count-card-peak {
	background: rgb(33, 37, 41);
	color: #fff;
	border-color: rgb(33, 37, 41);
}

.count-label {
	font-size: 14px;
	font-weight: 500;
	opacity: 0.75;
	margin-bottom: 8px;
}

.count-value {
	font-family: 'Inter Tight', sans-serif;
	font-weight: 700;
	font-size: 56px;
	line-height: 1;
	letter-spacing: -0.02em;
	margin-bottom: 8px;
}

.count-meta {
	font-size: 13px;
	opacity: 0.7;
	display: flex;
	align-items: center;
	gap: 6px;
}

/* Session control */
.control-card {
	display: flex;
	justify-content: space-between;
	gap: 16px;
	padding: 16px 20px;
	background: #fff;
	border: 2px solid rgb(215, 215, 215);
	border-radius: 16px;
	margin-bottom: 16px;
	flex-wrap: wrap;
}

.control-info {
	display: flex;
	align-items: center;
	gap: 12px;
	flex: 1 1 280px;
}

.status-dot {
	width: 12px;
	height: 12px;
	border-radius: 999px;
	flex-shrink: 0;
}

.status-active {
	background: #28a745;
	box-shadow: 0 0 0 0 rgba(40, 167, 69, 0.6);
	animation: pulse 1.6s infinite;
}

.status-idle {
	background: #adb5bd;
}

@keyframes pulse {
	0% {
		box-shadow: 0 0 0 0 rgba(40, 167, 69, 0.6);
	}
	70% {
		box-shadow: 0 0 0 12px rgba(40, 167, 69, 0);
	}
	100% {
		box-shadow: 0 0 0 0 rgba(40, 167, 69, 0);
	}
}

.control-title {
	font-weight: 600;
	font-size: 16px;
}

.control-label {
	font-weight: 500;
	color: #495057;
}

.control-sub {
	color: #6c757d;
	font-size: 13px;
}

.control-actions {
	display: flex;
	gap: 8px;
	flex-wrap: wrap;
	align-items: center;
}

.control-input {
	border: 2px solid rgb(215, 215, 215);
	border-radius: 12px;
	padding: 6px 12px;
	font-size: 14px;
	min-width: 180px;
	font-family: inherit;
}

.btn-primary-pill,
.btn-light-pill,
.btn-dark-pill,
.btn-danger-pill {
	display: inline-flex;
	align-items: center;
	gap: 6px;
	border-radius: 12px;
	padding: 6px 16px;
	font-size: 14px;
	font-weight: 500;
	cursor: pointer;
	border: 2px solid rgb(33, 37, 41);
	transition: opacity 0.15s ease;
	font-family: inherit;
}

.btn-primary-pill {
	background: rgb(33, 37, 41);
	color: #fff;
}

.btn-light-pill {
	background: #fff;
	color: rgb(33, 37, 41);
}

.btn-dark-pill {
	background: rgb(33, 37, 41);
	color: #fff;
}

.btn-danger-pill {
	background: #dc3545;
	color: #fff;
	border-color: #dc3545;
}

.btn-primary-pill:disabled,
.btn-light-pill:disabled,
.btn-dark-pill:disabled,
.btn-danger-pill:disabled {
	opacity: 0.45;
	cursor: not-allowed;
}

/* Layout */
.row-grid {
	display: grid;
	grid-template-columns: minmax(0, 2fr) minmax(0, 1fr);
	gap: 16px;
	margin-bottom: 24px;
}

@media (max-width: 900px) {
	.row-grid {
		grid-template-columns: 1fr;
	}
}

/* Feed */
.feed-card {
	background: #fff;
	border: 2px solid rgb(215, 215, 215);
	border-radius: 16px;
	padding: 16px;
	display: flex;
	flex-direction: column;
	height: 100%;
	min-height: 320px;
}

.feed-header {
	display: flex;
	justify-content: space-between;
	align-items: center;
	font-weight: 600;
	font-size: 16px;
	margin-bottom: 12px;
}

.feed-count {
	font-size: 12px;
	background: rgb(33, 37, 41);
	color: #fff;
	border-radius: 999px;
	padding: 2px 8px;
}

.feed-empty {
	flex: 1;
	display: flex;
	flex-direction: column;
	align-items: center;
	justify-content: center;
	color: #adb5bd;
	gap: 8px;
	border: 2px dashed rgb(215, 215, 215);
	border-radius: 12px;
	padding: 16px;
}

.feed-empty p {
	margin: 0;
	font-size: 14px;
}

.feed-list {
	list-style: none;
	margin: 0;
	padding: 0;
	overflow-y: auto;
	max-height: 380px;
	flex: 1;
}

.feed-item {
	display: grid;
	grid-template-columns: auto 1fr auto;
	align-items: center;
	gap: 8px;
	padding: 8px 0;
	border-bottom: 1px solid #f1f3f5;
	font-size: 14px;
}

.feed-item:last-child {
	border-bottom: none;
}

.feed-kind {
	display: inline-flex;
	align-items: center;
	gap: 4px;
	font-weight: 500;
	font-size: 13px;
	padding: 2px 8px;
	border-radius: 999px;
}

.feed-kind-in {
	background: #d1ecf1;
	color: #0c5460;
}

.feed-kind-out {
	background: #f8d7da;
	color: #721c24;
}

.feed-time {
	color: #6c757d;
	font-family: 'JetBrains Mono', ui-monospace, monospace;
	font-size: 12px;
	text-align: right;
}

.feed-track {
	color: #adb5bd;
	font-family: 'JetBrains Mono', ui-monospace, monospace;
	font-size: 12px;
	min-width: 30px;
	text-align: right;
}

/* History */
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

/* Footer */
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

.footer-meta span {
	color: #495057;
	font-weight: 500;
}
</style>
