<script>
// @ts-nocheck
import { api } from '../api.js';
import { dropCameraData, showCombined } from '../stores.js';
import AddCameraDialog from './AddCameraDialog.svelte';

let { cameras = [] } = $props();

const canCombine = $derived(cameras.length >= 2);

let dialogOpen = $state(false);
let busyId = $state(null);
let actionError = $state(null);

function badge(cam) {
	const s = cam?.status ?? {};
	if (s.last_error && !s.running) return { text: 'Error', cls: 'badge-err' };
	if (s.running && s.camera_open) return { text: 'Running', cls: 'badge-ok' };
	if (cam?.enabled && !s.running && !s.last_error) return { text: 'Starting…', cls: 'badge-warn' };
	if (!cam?.enabled) return { text: 'Stopped', cls: 'badge-muted' };
	return { text: 'Idle', cls: 'badge-muted' };
}

async function withAction(id, fn) {
	actionError = null;
	busyId = id;
	try {
		await fn();
	} catch (err) {
		actionError = err?.message ?? String(err);
	} finally {
		busyId = null;
	}
}

async function startCam(cam) {
	await withAction(cam.camera_id, () => api.startCamera(cam.camera_id));
}

async function stopCam(cam) {
	await withAction(cam.camera_id, () => api.stopCamera(cam.camera_id));
}

async function deleteCam(cam) {
	if (!confirm(`Delete camera "${cam.camera_name}"? Past sessions will be kept.`)) return;
	await withAction(cam.camera_id, async () => {
		await api.deleteCamera(cam.camera_id);
		dropCameraData(cam.camera_id);
	});
}

async function submitNewCamera(payload) {
	await api.createCamera(payload);
}
</script>

<div class="cameras-card">
	<div class="cameras-head">
		<div class="cameras-head-text">
			<div class="cameras-title">
				<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none">
					<rect x="3" y="6" width="14" height="12" rx="2" stroke="currentColor" stroke-width="2"/>
					<path d="M21 9l-4 3 4 3V9z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
				</svg>
				Cameras
				<span class="cam-count">{cameras.length}</span>
			</div>
			<div class="cameras-sub">
				Add and manage cameras here. Each camera runs independently and can have its own counting session.
			</div>
		</div>
		<div class="cameras-head-actions">
			{#if canCombine}
				<label class="toggle" title="Aggregate In/Out across all cameras with active sessions">
					<input
						type="checkbox"
						checked={$showCombined}
						onchange={(e) => showCombined.set(e.currentTarget.checked)}
					/>
					<span class="toggle-track">
						<span class="toggle-thumb"></span>
					</span>
					<span class="toggle-label">Combine totals</span>
				</label>
			{/if}
			<button class="btn-primary-pill" onclick={() => (dialogOpen = true)}>
				<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none">
					<path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
				</svg>
				Add camera
			</button>
		</div>
	</div>

	{#if actionError}
		<div class="cameras-error">{actionError}</div>
	{/if}

	{#if cameras.length === 0}
		<div class="cameras-empty">
			<p>No cameras yet. Add one to begin.</p>
		</div>
	{:else}
		<ul class="cameras-list">
			{#each cameras as cam (cam.camera_id)}
				{@const b = badge(cam)}
				{@const busy = busyId === cam.camera_id}
				<li class="cam-row">
					<div class="cam-info">
						<div class="cam-name-row">
							<span class="cam-name">{cam.camera_name}</span>
							{#if cam.kind === 'video'}
								<span class="badge-pill badge-muted">Video</span>
							{:else if cam.kind === 'rtsp'}
								<span class="badge-pill badge-muted">RTSP</span>
							{/if}
							<span class="badge-pill {b.cls}">{b.text}</span>
							{#if cam.session_id}
								<span class="badge-pill badge-active">● Session live</span>
							{/if}
						</div>
						<div class="cam-meta">
							<span class="cam-source">
								{#if cam.kind === 'video'}
									file <code>{(cam.source ?? '').split('/').pop()}</code>
								{:else}
									source <code>{cam.source}</code>
								{/if}
							</span>
							{#if cam.status?.fps && cam.status.running}
								· {cam.status.fps.toFixed(1)} FPS
							{/if}
							{#if cam.status?.frame_width && cam.status.camera_open}
								· {cam.status.frame_width}×{cam.status.frame_height}
							{/if}
							{#if cam.status?.device && cam.status.running}
								· {cam.status.device.toUpperCase()}
							{/if}
							{#if cam.status?.tracker && cam.status.running}
								· {cam.status.tracker}
							{/if}
						</div>
						{#if cam.status?.last_error}
							<div class="cam-err">{cam.status.last_error}</div>
						{/if}
					</div>
					<div class="cam-actions">
						{#if cam.status?.running || cam.enabled}
							<button class="btn-light-pill" onclick={() => stopCam(cam)} disabled={busy}>
								<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none">
									<rect x="6" y="6" width="12" height="12" rx="1.5" fill="currentColor"/>
								</svg>
								Stop
							</button>
						{:else}
							<button class="btn-primary-pill" onclick={() => startCam(cam)} disabled={busy}>
								<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none">
									<path d="M5 3l14 9-14 9V3z" fill="currentColor"/>
								</svg>
								Start
							</button>
						{/if}
						<button
							class="icon-btn icon-btn-danger"
							title="Delete camera"
							onclick={() => deleteCam(cam)}
							disabled={busy}
							aria-label="Delete camera"
						>
							<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none">
								<path d="M19 7L18.13 19.14C18.06 20.19 17.19 21 16.14 21H7.86C6.81 21 5.94 20.19 5.87 19.14L5 7M10 11v6M14 11v6M15 7V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
							</svg>
						</button>
					</div>
				</li>
			{/each}
		</ul>
	{/if}
</div>

<AddCameraDialog open={dialogOpen} onclose={() => (dialogOpen = false)} onsubmit={submitNewCamera} />

<style>
.cameras-card {
	background: #fff;
	border: 2px solid rgb(215, 215, 215);
	border-radius: 16px;
	padding: 16px 20px;
	margin-bottom: 16px;
	display: flex;
	flex-direction: column;
	gap: 12px;
}

.cameras-head {
	display: flex;
	justify-content: space-between;
	align-items: flex-start;
	gap: 16px;
	flex-wrap: wrap;
}

.cameras-head-text {
	display: flex;
	flex-direction: column;
	gap: 4px;
	flex: 1 1 280px;
}

.cameras-title {
	display: flex;
	align-items: center;
	gap: 8px;
	font-weight: 600;
	font-size: 16px;
	color: rgb(33, 37, 41);
}

.cam-count {
	display: inline-flex;
	align-items: center;
	font-size: 12px;
	background: rgb(33, 37, 41);
	color: #fff;
	border-radius: 999px;
	padding: 1px 8px;
	font-variant-numeric: tabular-nums;
}

.cameras-sub {
	font-size: 13px;
	color: #6c757d;
}

.cameras-empty {
	border: 2px dashed rgb(215, 215, 215);
	border-radius: 12px;
	padding: 18px;
	text-align: center;
	color: #6c757d;
	font-size: 14px;
}

.cameras-empty p {
	margin: 0;
}

.cameras-error {
	background: #fdeaec;
	color: #842029;
	border: 1px solid #f5c2c7;
	border-radius: 10px;
	padding: 8px 12px;
	font-size: 14px;
}

.cameras-list {
	list-style: none;
	margin: 0;
	padding: 0;
	display: flex;
	flex-direction: column;
	gap: 8px;
}

.cam-row {
	display: flex;
	justify-content: space-between;
	align-items: flex-start;
	gap: 12px;
	padding: 12px;
	border: 2px solid #f1f3f5;
	border-radius: 12px;
	flex-wrap: wrap;
}

.cam-info {
	flex: 1 1 220px;
	display: flex;
	flex-direction: column;
	gap: 4px;
}

.cam-name-row {
	display: flex;
	align-items: center;
	gap: 8px;
	flex-wrap: wrap;
}

.cam-name {
	font-weight: 600;
	color: rgb(33, 37, 41);
}

.cam-meta {
	font-size: 13px;
	color: #6c757d;
}

.cam-source code {
	background: #f1f3f5;
	border-radius: 6px;
	padding: 1px 6px;
	color: #495057;
	font-family: 'JetBrains Mono', ui-monospace, monospace;
	font-size: 12px;
}

.cam-err {
	color: #842029;
	font-size: 12px;
	background: #fdeaec;
	padding: 4px 8px;
	border-radius: 8px;
	margin-top: 4px;
}

.cam-actions {
	display: flex;
	align-items: center;
	gap: 6px;
}

.badge-pill {
	display: inline-flex;
	align-items: center;
	gap: 4px;
	font-size: 12px;
	font-weight: 500;
	padding: 2px 10px;
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
}

.badge-active {
	background: rgb(33, 37, 41);
	color: #fff;
	border-color: rgb(33, 37, 41);
}

.btn-primary-pill,
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

.btn-primary-pill:disabled,
.btn-light-pill:disabled {
	opacity: 0.45;
	cursor: not-allowed;
}

.icon-btn {
	background: transparent;
	border: 1px solid #e9ecef;
	border-radius: 10px;
	padding: 6px 8px;
	cursor: pointer;
	color: #495057;
	display: inline-flex;
	align-items: center;
	font-family: inherit;
}

.icon-btn:hover:not(:disabled) {
	background: #f1f3f5;
}

.icon-btn:disabled {
	opacity: 0.45;
	cursor: not-allowed;
}

.icon-btn-danger {
	color: #dc3545;
	border-color: #f5c2c7;
}

.icon-btn-danger:hover:not(:disabled) {
	background: #fdeaec;
}

.cameras-head-actions {
	display: flex;
	align-items: center;
	gap: 12px;
	flex-wrap: wrap;
}

.toggle {
	display: inline-flex;
	align-items: center;
	gap: 8px;
	cursor: pointer;
	user-select: none;
	font-size: 13px;
	color: #495057;
}

.toggle input {
	position: absolute;
	opacity: 0;
	pointer-events: none;
}

.toggle-track {
	position: relative;
	width: 36px;
	height: 20px;
	background: #ced4da;
	border-radius: 999px;
	transition: background 0.15s ease;
}

.toggle-thumb {
	position: absolute;
	top: 2px;
	left: 2px;
	width: 16px;
	height: 16px;
	background: #fff;
	border-radius: 999px;
	transition: transform 0.15s ease;
	box-shadow: 0 1px 2px rgba(0, 0, 0, 0.2);
}

.toggle input:checked + .toggle-track {
	background: rgb(33, 37, 41);
}

.toggle input:checked + .toggle-track .toggle-thumb {
	transform: translateX(16px);
}

.toggle input:focus-visible + .toggle-track {
	outline: 2px solid rgb(33, 37, 41);
	outline-offset: 2px;
}

.toggle-label {
	font-weight: 500;
}
</style>
