<script>
// @ts-nocheck
import { onMount } from 'svelte';
import { api } from '../api.js';

let { open = false, onclose = () => {}, onsubmit = async () => {} } = $props();

// kind: 'camera' (built-in/USB) | 'video' (file) | 'rtsp' (network stream)
let kind = $state('camera');

let name = $state('');
let source = $state('');
let mirror = $state(false);
let autostart = $state(true);
let loopVideo = $state(true);
let error = $state(null);
let busy = $state(false);
let nameInput = $state(null);

// Video tab state.
let videos = $state([]);
let selectedVideo = $state('');
let uploadProgress = $state(0);
let uploading = $state(false);
let uploadName = $state('');
let fileInput = $state(null);

$effect(() => {
	if (open) {
		kind = 'camera';
		name = '';
		source = '';
		mirror = false;
		autostart = true;
		loopVideo = true;
		error = null;
		busy = false;
		uploadProgress = 0;
		uploading = false;
		uploadName = '';
		selectedVideo = '';
		queueMicrotask(() => nameInput?.focus());
		refreshVideos();
	}
});

async function refreshVideos() {
	try {
		videos = await api.listVideos();
	} catch (err) {
		console.warn('Failed to list videos', err);
		videos = [];
	}
}

function setKind(k) {
	if (busy) return;
	kind = k;
	error = null;
	if (k === 'video' && selectedVideo) {
		source = selectedVideo;
	} else if (k === 'rtsp') {
		source = '';
	} else if (k === 'camera') {
		source = '';
	}
}

async function handleFile(e) {
	const file = e?.target?.files?.[0];
	if (!file) return;
	uploadName = file.name;
	uploading = true;
	uploadProgress = 0;
	error = null;
	try {
		const res = await api.uploadVideo(file, (p) => (uploadProgress = p));
		await refreshVideos();
		selectedVideo = res.path;
		source = res.path;
		if (!name) name = file.name.replace(/\.[^.]+$/, '');
	} catch (err) {
		error = err?.message ?? String(err);
	} finally {
		uploading = false;
		if (fileInput) fileInput.value = '';
	}
}

function chooseExisting(path) {
	selectedVideo = path;
	source = path;
}

function humanSize(n) {
	if (!n && n !== 0) return '';
	if (n < 1024) return `${n} B`;
	if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
	if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
	return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

async function submit(e) {
	e?.preventDefault?.();
	if (busy) return;
	error = null;
	const trimmedName = name.trim();
	let trimmedSource = source.trim();
	if (kind === 'video' && selectedVideo) trimmedSource = selectedVideo;
	if (!trimmedName) {
		error = 'Name is required.';
		return;
	}
	if (!trimmedSource) {
		error =
			kind === 'camera'
				? 'Source is required (e.g. "0" for the first camera).'
				: kind === 'rtsp'
					? 'Stream URL is required (e.g. rtsp://… or http://…).'
					: 'Choose a video file to use.';
		return;
	}
	busy = true;
	try {
		await onsubmit({
			name: trimmedName,
			source: trimmedSource,
			kind,
			mirror,
			enabled: true,
			autostart,
			loop_video: kind === 'video' ? loopVideo : undefined
		});
		onclose();
	} catch (err) {
		error = err?.message ?? String(err);
	} finally {
		busy = false;
	}
}

function handleKeydown(e) {
	if (!open) return;
	if (e.key === 'Escape' && !busy && !uploading) {
		e.preventDefault();
		onclose();
	}
}
</script>

<svelte:window onkeydown={handleKeydown} />

{#if open}
	<div
		class="backdrop"
		role="presentation"
		onclick={(e) => {
			if (e.target === e.currentTarget && !busy && !uploading) onclose();
		}}
	>
		<div role="dialog" aria-modal="true" aria-labelledby="add-camera-title">
		<form class="dialog" onsubmit={submit}>
			<div class="dialog-head">
				<h2 id="add-camera-title">Add a source</h2>
				<button
					type="button"
					class="close-btn"
					aria-label="Close"
					onclick={onclose}
					disabled={busy || uploading}
				>
					<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none">
						<path d="M18 6L6 18M6 6l12 12" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
					</svg>
				</button>
			</div>

			<div class="kind-tabs" role="tablist">
				<button
					type="button"
					class="kind-tab"
					class:active={kind === 'camera'}
					role="tab"
					aria-selected={kind === 'camera'}
					onclick={() => setKind('camera')}
					disabled={busy || uploading}
				>
					<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none">
						<rect x="3" y="6" width="14" height="12" rx="2" stroke="currentColor" stroke-width="2"/>
						<path d="M21 9l-4 3 4 3V9z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
					</svg>
					Camera
				</button>
				<button
					type="button"
					class="kind-tab"
					class:active={kind === 'video'}
					role="tab"
					aria-selected={kind === 'video'}
					onclick={() => setKind('video')}
					disabled={busy || uploading}
				>
					<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none">
						<path d="M5 3l14 9-14 9V3z" fill="currentColor"/>
					</svg>
					Video file
				</button>
				<button
					type="button"
					class="kind-tab"
					class:active={kind === 'rtsp'}
					role="tab"
					aria-selected={kind === 'rtsp'}
					onclick={() => setKind('rtsp')}
					disabled={busy || uploading}
				>
					<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none">
						<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7z" stroke="currentColor" stroke-width="2"/>
						<circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="2"/>
					</svg>
					Network stream
				</button>
			</div>

			<label class="field">
				<span class="field-label">Name</span>
				<input
					bind:this={nameInput}
					bind:value={name}
					maxlength="80"
					placeholder={kind === 'video' ? 'e.g. Sunday morning recording' : 'e.g. Main entrance'}
					disabled={busy || uploading}
					required
				/>
			</label>

			{#if kind === 'camera'}
				<label class="field">
					<span class="field-label">Camera index</span>
					<input
						bind:value={source}
						maxlength="500"
						placeholder='"0" for the first camera, "1" for the second…'
						disabled={busy}
						required
					/>
					<span class="field-help">
						Use a digit for built-in or USB cameras. macOS will prompt for camera
						permission the first time.
					</span>
				</label>

				<label class="checkbox">
					<input type="checkbox" bind:checked={mirror} disabled={busy} />
					<span>Mirror preview horizontally (selfie cameras)</span>
				</label>
			{:else if kind === 'rtsp'}
				<label class="field">
					<span class="field-label">Stream URL</span>
					<input
						bind:value={source}
						maxlength="500"
						placeholder="rtsp://user:pass@192.168.1.10:554/Streaming/Channels/101"
						disabled={busy}
						required
					/>
					<span class="field-help">
						RTSP, RTMP, or HTTP MJPEG. Most IP cameras give an RTSP URL in their
						admin panel.
					</span>
				</label>
			{:else}
				<div class="field">
					<span class="field-label">Video file</span>
					<div class="upload-row">
						<button
							type="button"
							class="btn-light-pill"
							onclick={() => fileInput?.click()}
							disabled={busy || uploading}
						>
							<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none">
								<path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
							</svg>
							Upload video
						</button>
						<input
							bind:this={fileInput}
							type="file"
							accept="video/mp4,video/quicktime,video/x-matroska,video/webm,video/x-msvideo,.mp4,.mov,.m4v,.mkv,.webm,.avi"
							onchange={handleFile}
							style="display: none"
						/>
						<span class="upload-help">
							{#if uploading}
								Uploading {uploadName}… {Math.round(uploadProgress * 100)}%
							{:else}
								MP4, MOV, MKV, WebM, AVI (up to ~500 MB)
							{/if}
						</span>
					</div>
					{#if uploading}
						<div class="progress">
							<div class="progress-bar" style:width="{Math.round(uploadProgress * 100)}%"></div>
						</div>
					{/if}
				</div>

				{#if videos.length > 0}
					<div class="field">
						<span class="field-label">Saved videos</span>
						<ul class="video-list">
							{#each videos as v (v.filename)}
								<li>
									<label class="video-row" class:active={selectedVideo === v.path}>
										<input
											type="radio"
											name="video"
											value={v.path}
											checked={selectedVideo === v.path}
											onchange={() => chooseExisting(v.path)}
											disabled={busy || uploading}
										/>
										<span class="video-name">{v.filename}</span>
										<span class="video-size">{humanSize(v.size)}</span>
									</label>
								</li>
							{/each}
						</ul>
					</div>
				{:else if !uploading}
					<div class="video-empty">
						No saved videos yet. Upload one above.
					</div>
				{/if}

				<label class="checkbox">
					<input type="checkbox" bind:checked={loopVideo} disabled={busy} />
					<span>Loop the video when it ends</span>
				</label>
			{/if}

			<label class="checkbox">
				<input type="checkbox" bind:checked={autostart} disabled={busy} />
				<span>Start the source immediately</span>
			</label>

			{#if error}
				<div class="dialog-error">{error}</div>
			{/if}

			<div class="dialog-actions">
				<button type="button" class="btn-light-pill" onclick={onclose} disabled={busy || uploading}>
					Cancel
				</button>
				<button type="submit" class="btn-dark-pill" disabled={busy || uploading}>
					{busy ? 'Adding…' : 'Add'}
				</button>
			</div>
		</form>
		</div>
	</div>
{/if}

<style>
.backdrop {
	position: fixed;
	inset: 0;
	background: rgba(0, 0, 0, 0.45);
	display: flex;
	align-items: center;
	justify-content: center;
	padding: 16px;
	z-index: 1000;
}

.dialog {
	background: #fff;
	border-radius: 16px;
	padding: 20px;
	width: 100%;
	max-width: 540px;
	display: flex;
	flex-direction: column;
	gap: 14px;
	box-shadow: 0 20px 60px rgba(0, 0, 0, 0.25);
	font-family: inherit;
	max-height: calc(100vh - 32px);
	overflow-y: auto;
}

.dialog-head {
	display: flex;
	align-items: center;
	justify-content: space-between;
	gap: 12px;
}

.dialog-head h2 {
	font-family: 'Inter Tight', sans-serif;
	font-size: 20px;
	font-weight: 600;
	margin: 0;
	color: rgb(33, 37, 41);
}

.close-btn {
	background: transparent;
	border: 1px solid #e9ecef;
	border-radius: 10px;
	padding: 4px 8px;
	cursor: pointer;
	color: #495057;
	display: inline-flex;
	align-items: center;
	font-family: inherit;
}

.close-btn:hover:not(:disabled) {
	background: #f1f3f5;
}

.close-btn:disabled {
	opacity: 0.5;
	cursor: not-allowed;
}

.kind-tabs {
	display: inline-flex;
	gap: 4px;
	padding: 4px;
	background: #f1f3f5;
	border-radius: 999px;
	width: fit-content;
	max-width: 100%;
	flex-wrap: wrap;
}

.kind-tab {
	display: inline-flex;
	align-items: center;
	gap: 6px;
	border: none;
	background: transparent;
	color: #495057;
	border-radius: 999px;
	padding: 6px 12px;
	font-size: 13px;
	font-weight: 500;
	font-family: inherit;
	cursor: pointer;
}

.kind-tab.active {
	background: rgb(33, 37, 41);
	color: #fff;
}

.kind-tab:disabled {
	opacity: 0.5;
	cursor: not-allowed;
}

.field {
	display: flex;
	flex-direction: column;
	gap: 6px;
}

.field-label {
	font-weight: 600;
	font-size: 13px;
	color: #495057;
}

.field input {
	border: 2px solid rgb(215, 215, 215);
	border-radius: 12px;
	padding: 8px 12px;
	font-size: 14px;
	font-family: inherit;
}

.field input:focus {
	outline: none;
	border-color: rgb(33, 37, 41);
}

.field input:disabled {
	background: #f8f9fa;
	color: #6c757d;
}

.field-help {
	font-size: 12px;
	color: #6c757d;
}

.checkbox {
	display: flex;
	align-items: center;
	gap: 8px;
	font-size: 14px;
	color: #495057;
	cursor: pointer;
}

.checkbox input {
	margin: 0;
	width: 16px;
	height: 16px;
}

.dialog-error {
	background: #fdeaec;
	color: #842029;
	border: 1px solid #f5c2c7;
	border-radius: 10px;
	padding: 8px 12px;
	font-size: 14px;
}

.dialog-actions {
	display: flex;
	justify-content: flex-end;
	gap: 8px;
}

.upload-row {
	display: flex;
	align-items: center;
	gap: 12px;
	flex-wrap: wrap;
}

.upload-help {
	font-size: 12px;
	color: #6c757d;
}

.progress {
	height: 6px;
	background: #f1f3f5;
	border-radius: 999px;
	overflow: hidden;
	margin-top: 4px;
}

.progress-bar {
	height: 100%;
	background: rgb(33, 37, 41);
	transition: width 0.15s ease;
}

.video-list {
	list-style: none;
	margin: 0;
	padding: 0;
	display: flex;
	flex-direction: column;
	gap: 4px;
	max-height: 180px;
	overflow-y: auto;
	border: 2px solid rgb(215, 215, 215);
	border-radius: 12px;
	padding: 4px;
}

.video-row {
	display: flex;
	align-items: center;
	gap: 8px;
	padding: 6px 10px;
	border-radius: 8px;
	cursor: pointer;
	font-size: 13px;
	color: #212529;
	transition: background 0.1s ease;
}

.video-row:hover {
	background: #f8f9fa;
}

.video-row.active {
	background: #fff8d4;
}

.video-row input[type='radio'] {
	margin: 0;
}

.video-name {
	flex: 1;
	overflow: hidden;
	text-overflow: ellipsis;
	white-space: nowrap;
	font-family: 'JetBrains Mono', ui-monospace, monospace;
	font-size: 12px;
}

.video-size {
	font-size: 11px;
	color: #6c757d;
	font-variant-numeric: tabular-nums;
}

.video-empty {
	font-size: 13px;
	color: #6c757d;
	border: 2px dashed rgb(215, 215, 215);
	border-radius: 12px;
	padding: 14px;
	text-align: center;
}

.btn-light-pill,
.btn-dark-pill {
	border: 2px solid rgb(33, 37, 41);
	border-radius: 12px;
	padding: 6px 16px;
	font-size: 14px;
	font-weight: 500;
	cursor: pointer;
	font-family: inherit;
	display: inline-flex;
	align-items: center;
	gap: 6px;
}

.btn-light-pill {
	background: #fff;
	color: rgb(33, 37, 41);
}

.btn-dark-pill {
	background: rgb(33, 37, 41);
	color: #fff;
}

.btn-light-pill:disabled,
.btn-dark-pill:disabled {
	opacity: 0.45;
	cursor: not-allowed;
}
</style>
