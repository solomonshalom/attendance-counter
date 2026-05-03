<script>
// @ts-nocheck
import { api } from '../api.js';
import { eventsByCamera, clearEventsForCamera, venues } from '../stores.js';
import { formatTime, formatDuration } from '../format.js';
import LinePreview from './LinePreview.svelte';
import PlaybackControls from './PlaybackControls.svelte';
import SettingsPanel from './SettingsPanel.svelte';
import CalibrationDialog from './CalibrationDialog.svelte';

let { camera } = $props();

let sessionLabel = $state('');
let actionError = $state(null);
let actionLoading = $state(false);
let calibOpen = $state(false);
let venueBusy = $state(false);

const cameraId = $derived(camera?.camera_id);
const status = $derived(camera?.status ?? null);
const events = $derived($eventsByCamera[cameraId] ?? []);
const venue = $derived(($venues ?? []).find((v) => v.id === camera?.venue_id) ?? null);

async function changeVenue(e) {
	const value = e.currentTarget.value || null;
	venueBusy = true;
	actionError = null;
	try {
		await api.assignCameraToVenue(cameraId, value);
	} catch (err) {
		actionError = err?.message ?? String(err);
	} finally {
		venueBusy = false;
	}
}

function openCalibration() {
	if (!venue) {
		actionError = 'Add this camera to a venue first.';
		return;
	}
	calibOpen = true;
}

async function withAction(fn) {
	actionError = null;
	actionLoading = true;
	try {
		await fn();
	} catch (err) {
		actionError = err?.message ?? String(err);
	} finally {
		actionLoading = false;
	}
}

const canStartSession = $derived(
	camera?.enabled && status?.running && status?.model_loaded && status?.camera_open
);

function startSession() {
	withAction(async () => {
		await api.startSession(cameraId, sessionLabel.trim());
		sessionLabel = '';
		clearEventsForCamera(cameraId);
	});
}

function stopSession() {
	withAction(() => api.stopSession(cameraId));
}

function resetCounts() {
	withAction(async () => {
		await api.resetCounts(cameraId);
		clearEventsForCamera(cameraId);
	});
}

function startSessionDisabledReason() {
	if (!camera?.enabled) return 'Start the camera first.';
	if (!status?.running) return 'Camera is starting…';
	if (!status?.model_loaded) return 'Loading detection model…';
	if (!status?.camera_open) return 'Waiting for camera signal…';
	return '';
}
</script>

<section class="camera-card">
	<header class="cam-header">
		<div class="cam-headline">
			<div class="cam-headline-text">
				{#if camera?.session_id}
					<div class="status-dot status-active" aria-hidden="true"></div>
				{:else}
					<div class="status-dot status-idle" aria-hidden="true"></div>
				{/if}
				<div>
					<div class="cam-title-row">
						<h2 class="cam-title">{camera?.camera_name}</h2>
						<span class="cam-source-pill"><code>{camera?.source}</code></span>
					</div>
					{#if camera?.session_id}
						<div class="cam-sub">
							Session live{camera.session_label ? ` — ${camera.session_label}` : ''}
							· started {formatTime(camera.session_started_at)}
							· {formatDuration(camera.session_started_at, null)} elapsed
						</div>
					{:else if !camera?.enabled}
						<div class="cam-sub">Camera stopped. Start it from the Cameras section above.</div>
					{:else if status?.last_error}
						<div class="cam-sub cam-sub-error">{status.last_error}</div>
					{:else}
						<div class="cam-sub">Idle. Start a session to begin counting.</div>
					{/if}
				</div>
			</div>

			<div class="cam-actions-row">
				{#if !camera?.session_id}
					<input
						type="text"
						class="cam-input"
						placeholder="Session name (optional)"
						bind:value={sessionLabel}
						maxlength="200"
						disabled={!canStartSession || actionLoading}
					/>
					<button
						class="btn-primary-pill"
						onclick={startSession}
						disabled={!canStartSession || actionLoading}
						title={canStartSession ? 'Start session' : startSessionDisabledReason()}
					>
						<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none">
							<path d="M5 3l14 9-14 9V3z" fill="currentColor"/>
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
			<div class="cam-action-error">{actionError}</div>
		{/if}

		<PlaybackControls {camera} />

		<div class="cam-tools">
			<SettingsPanel {camera} />

			{#if $venues.length > 0}
				<label class="venue-picker">
					<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none">
						<path d="M3 12l9-9 9 9M5 10v10h14V10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
					</svg>
					<select value={camera?.venue_id ?? ''} onchange={changeVenue} disabled={venueBusy}>
						<option value="">No venue</option>
						{#each $venues as v (v.id)}
							<option value={v.id}>{v.name}</option>
						{/each}
					</select>
				</label>

				{#if camera?.venue_id}
					<button class="calibrate-btn" class:calibrated={camera?.calibrated} onclick={openCalibration} disabled={venueBusy}>
						<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none">
							{#if camera?.calibrated}
								<path d="M5 13l4 4L19 7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
							{:else}
								<path d="M3 3l3 9-3 9 18-9z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
							{/if}
						</svg>
						{camera?.calibrated ? 'Calibrated' : 'Calibrate'}
					</button>
				{/if}
			{/if}
		</div>
	</header>

	<div class="counts-grid">
		<div class="count-card count-card-in">
			<div class="count-label">Entered</div>
			<div class="count-value">{camera?.in_count ?? 0}</div>
			<div class="count-meta">
				<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none">
					<path d="M5 12h14M13 6l6 6-6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
				</svg>
				across the line
			</div>
		</div>
		<div class="count-card count-card-out">
			<div class="count-label">Left</div>
			<div class="count-value">{camera?.out_count ?? 0}</div>
			<div class="count-meta">
				<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none">
					<path d="M19 12H5M11 18l-6-6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
				</svg>
				across the line
			</div>
		</div>
		<div class="count-card count-card-inside">
			<div class="count-label">Inside now</div>
			<div class="count-value">{camera?.inside ?? 0}</div>
			<div class="count-meta">in − out</div>
		</div>
		<div class="count-card count-card-peak">
			<div class="count-label">Peak attendance</div>
			<div class="count-value">{camera?.peak_inside ?? 0}</div>
			<div class="count-meta">most ever inside</div>
		</div>
	</div>

	<div class="row-grid">
		<div class="col-main">
			<LinePreview
				cameraId={cameraId}
				line={camera?.line ?? { x1: 0.5, y1: 0, x2: 0.5, y2: 1 }}
				lines={camera?.lines ?? []}
				zones={camera?.zones ?? []}
				frameWidth={status?.frame_width || 1280}
				frameHeight={status?.frame_height || 720}
				cameraOpen={status?.camera_open ?? false}
				cameraRunning={status?.running ?? false}
				lastError={status?.last_error}
			/>
		</div>
		<div class="col-side">
			<div class="feed-card">
				<div class="feed-header">
					<span>Recent crossings</span>
					<span class="feed-count">{events.length}</span>
				</div>
				{#if events.length === 0}
					<div class="feed-empty">
						<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none">
							<circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2"/>
							<path d="M12 7v5l3 2" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
						</svg>
						<p>Waiting for someone to cross the line.</p>
					</div>
				{:else}
					<ul class="feed-list">
						{#each events as ev (ev.id)}
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
</section>

<CalibrationDialog
	open={calibOpen}
	camera={camera}
	venue={venue}
	onclose={() => (calibOpen = false)}
/>

<style>
.camera-card {
	background: #fff;
	border: 2px solid rgb(215, 215, 215);
	border-radius: 16px;
	padding: 16px;
	margin-bottom: 16px;
	display: flex;
	flex-direction: column;
	gap: 16px;
}

.cam-header {
	display: flex;
	flex-direction: column;
	gap: 8px;
}

.cam-headline {
	display: flex;
	justify-content: space-between;
	align-items: flex-start;
	gap: 12px;
	flex-wrap: wrap;
}

.cam-headline-text {
	display: flex;
	align-items: flex-start;
	gap: 12px;
	flex: 1 1 280px;
}

.status-dot {
	width: 12px;
	height: 12px;
	border-radius: 999px;
	margin-top: 7px;
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
	0% { box-shadow: 0 0 0 0 rgba(40, 167, 69, 0.6); }
	70% { box-shadow: 0 0 0 12px rgba(40, 167, 69, 0); }
	100% { box-shadow: 0 0 0 0 rgba(40, 167, 69, 0); }
}

.cam-title-row {
	display: flex;
	align-items: center;
	gap: 8px;
	flex-wrap: wrap;
}

.cam-title {
	font-family: 'Inter Tight', sans-serif;
	font-size: 18px;
	font-weight: 600;
	margin: 0;
	color: rgb(33, 37, 41);
}

.cam-source-pill code {
	background: #f1f3f5;
	border-radius: 6px;
	padding: 1px 6px;
	color: #495057;
	font-family: 'JetBrains Mono', ui-monospace, monospace;
	font-size: 12px;
}

.cam-sub {
	font-size: 13px;
	color: #6c757d;
	margin-top: 4px;
}

.cam-sub-error {
	color: #842029;
}

.cam-actions-row {
	display: flex;
	align-items: center;
	gap: 8px;
	flex-wrap: wrap;
}

.cam-input {
	border: 2px solid rgb(215, 215, 215);
	border-radius: 12px;
	padding: 6px 12px;
	font-size: 14px;
	min-width: 180px;
	font-family: inherit;
}

.cam-input:disabled {
	background: #f8f9fa;
	color: #6c757d;
}

.cam-action-error {
	color: #842029;
	background: #fdeaec;
	border: 1px solid #f5c2c7;
	border-radius: 10px;
	padding: 8px 12px;
	font-size: 14px;
}

.cam-tools {
	display: flex;
	flex-wrap: wrap;
	gap: 8px;
	align-items: center;
}

.venue-picker {
	display: inline-flex;
	align-items: center;
	gap: 6px;
	border: 1px solid #e9ecef;
	background: #fff;
	color: #495057;
	border-radius: 12px;
	padding: 4px 10px;
	font-size: 13px;
	font-weight: 500;
}

.venue-picker select {
	border: none;
	background: transparent;
	font-family: inherit;
	font-size: 13px;
	color: rgb(33, 37, 41);
	padding: 2px 0;
	cursor: pointer;
	outline: none;
}

.calibrate-btn {
	display: inline-flex;
	align-items: center;
	gap: 6px;
	border: 1px solid #e9ecef;
	background: #fff;
	color: #495057;
	border-radius: 12px;
	padding: 6px 12px;
	font-size: 13px;
	font-weight: 500;
	font-family: inherit;
	cursor: pointer;
}

.calibrate-btn:hover:not(:disabled) {
	background: #f8f9fa;
}

.calibrate-btn.calibrated {
	background: #d1f4e0;
	color: #146c43;
	border-color: #b5e8c8;
}

.calibrate-btn:disabled {
	opacity: 0.5;
	cursor: not-allowed;
}

.btn-primary-pill,
.btn-light-pill,
.btn-danger-pill {
	display: inline-flex;
	align-items: center;
	gap: 6px;
	border-radius: 12px;
	padding: 6px 14px;
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

.btn-danger-pill {
	background: #dc3545;
	color: #fff;
	border-color: #dc3545;
}

.btn-primary-pill:disabled,
.btn-light-pill:disabled,
.btn-danger-pill:disabled {
	opacity: 0.45;
	cursor: not-allowed;
}

.counts-grid {
	display: grid;
	grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
	gap: 16px;
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

.row-grid {
	display: grid;
	grid-template-columns: minmax(0, 2fr) minmax(0, 1fr);
	gap: 16px;
}

@media (max-width: 900px) {
	.row-grid {
		grid-template-columns: 1fr;
	}
}

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
</style>
