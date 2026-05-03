<script>
// @ts-nocheck
import { api } from '../api.js';

let { camera } = $props();

let busy = $state(false);
let actionError = $state(null);

const status = $derived(camera?.status ?? null);
const isVideo = $derived(camera?.kind === 'video' || status?.is_video_file);
const paused = $derived(camera?.paused === true);
const totalFrames = $derived(status?.video_total_frames ?? 0);
const position = $derived(status?.video_position ?? 0);
const fps = $derived(status?.video_fps || 0);
const progress = $derived(totalFrames > 0 ? position / totalFrames : 0);
const totalSeconds = $derived(totalFrames > 0 && fps > 0 ? totalFrames / fps : 0);
const positionSeconds = $derived(fps > 0 ? position / fps : 0);

function formatTime(seconds) {
	if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
	const m = Math.floor(seconds / 60);
	const s = Math.floor(seconds % 60);
	return `${m}:${String(s).padStart(2, '0')}`;
}

async function action(name) {
	busy = true;
	actionError = null;
	try {
		await api.playback(camera.camera_id, name);
	} catch (err) {
		actionError = err?.message ?? String(err);
	} finally {
		busy = false;
	}
}

async function toggleLoop() {
	busy = true;
	actionError = null;
	try {
		await api.updateCamera(camera.camera_id, { loop_video: !camera?.loop_video });
	} catch (err) {
		actionError = err?.message ?? String(err);
	} finally {
		busy = false;
	}
}
</script>

{#if isVideo}
	<div class="playback-bar">
		<div class="pb-controls">
			{#if paused}
				<button class="pb-btn" title="Play" onclick={() => action('play')} disabled={busy}>
					<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none">
						<path d="M5 3l14 9-14 9V3z" fill="currentColor"/>
					</svg>
					Play
				</button>
			{:else}
				<button class="pb-btn" title="Pause" onclick={() => action('pause')} disabled={busy}>
					<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none">
						<rect x="6" y="5" width="4" height="14" rx="1" fill="currentColor"/>
						<rect x="14" y="5" width="4" height="14" rx="1" fill="currentColor"/>
					</svg>
					Pause
				</button>
			{/if}
			<button class="pb-btn" title="Restart from beginning" onclick={() => action('restart')} disabled={busy}>
				<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none">
					<path d="M3 12a9 9 0 109-9v4M3 8V3h5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
				</svg>
				Restart
			</button>
			<label class="pb-loop">
				<input type="checkbox" checked={!!camera?.loop_video} onchange={toggleLoop} disabled={busy} />
				<span>Loop</span>
			</label>
		</div>
		<div class="pb-meta">
			{#if totalFrames > 0}
				<div class="pb-track">
					<div class="pb-track-fill" style:width="{(progress * 100).toFixed(1)}%"></div>
				</div>
				<span class="pb-time">{formatTime(positionSeconds)} / {formatTime(totalSeconds)}</span>
			{:else}
				<span class="pb-time">Buffering…</span>
			{/if}
		</div>
	</div>
	{#if actionError}
		<div class="pb-error">{actionError}</div>
	{/if}
{/if}

<style>
.playback-bar {
	display: flex;
	align-items: center;
	justify-content: space-between;
	gap: 12px;
	padding: 8px 12px;
	background: #f8f9fa;
	border: 1px solid #e9ecef;
	border-radius: 12px;
	flex-wrap: wrap;
}

.pb-controls {
	display: inline-flex;
	align-items: center;
	gap: 6px;
	flex-wrap: wrap;
}

.pb-btn {
	display: inline-flex;
	align-items: center;
	gap: 4px;
	border: 1px solid #e9ecef;
	background: #fff;
	color: #495057;
	border-radius: 999px;
	padding: 4px 12px;
	font-size: 12px;
	font-weight: 500;
	font-family: inherit;
	cursor: pointer;
}

.pb-btn:hover:not(:disabled) {
	background: #f1f3f5;
	color: rgb(33, 37, 41);
}

.pb-btn:disabled {
	opacity: 0.5;
	cursor: not-allowed;
}

.pb-loop {
	display: inline-flex;
	align-items: center;
	gap: 4px;
	font-size: 12px;
	color: #6c757d;
	cursor: pointer;
}

.pb-loop input {
	margin: 0;
}

.pb-meta {
	display: flex;
	align-items: center;
	gap: 10px;
	flex: 1 1 200px;
	min-width: 180px;
}

.pb-track {
	flex: 1;
	height: 4px;
	background: #e9ecef;
	border-radius: 999px;
	overflow: hidden;
}

.pb-track-fill {
	height: 100%;
	background: rgb(33, 37, 41);
	transition: width 0.2s linear;
}

.pb-time {
	font-family: 'JetBrains Mono', ui-monospace, monospace;
	font-size: 12px;
	color: #495057;
	white-space: nowrap;
	font-variant-numeric: tabular-nums;
}

.pb-error {
	margin-top: 6px;
	background: #fdeaec;
	color: #842029;
	border: 1px solid #f5c2c7;
	border-radius: 8px;
	padding: 6px 10px;
	font-size: 13px;
}
</style>
