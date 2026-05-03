<script>
// @ts-nocheck
import { onMount, onDestroy } from 'svelte';
import { previewUrl, api } from '../api.js';

let {
	cameraId,
	line,
	lines = [],
	zones = [],
	frameWidth = 1280,
	frameHeight = 720,
	cameraOpen = true,
	cameraRunning = true,
	lastError = null
} = $props();

const otherLines = $derived(
	(lines || []).filter((l, i) => i > 0 && l && (l.x1 !== line?.x1 || l.y1 !== line?.y1 || l.x2 !== line?.x2 || l.y2 !== line?.y2))
);
const polygonStrings = $derived.by(() => {
	const w = renderedSize?.width || 1;
	const h = renderedSize?.height || 1;
	return (zones || []).map((z) => {
		const pts = (z?.polygon || []).map((p) => `${(p.x ?? 0) * w},${(p.y ?? 0) * h}`).join(' ');
		return { name: z?.name ?? '', role: z?.role ?? 'observer', points: pts };
	});
});

let containerEl;
let imgEl = $state(null);
let containerSize = $state({ width: 0, height: 0 });
let imageError = $state(false);
let dragHandle = $state(null);
let editing = $state(false);
let pendingLine = $state({ x1: 0.5, y1: 0, x2: 0.5, y2: 1 });
let saving = $state(false);
let saveError = $state(null);

$effect(() => {
	if (!editing && line) {
		pendingLine = { ...line };
	}
});

const aspect = $derived(frameWidth > 0 && frameHeight > 0 ? frameWidth / frameHeight : 16 / 9);
const renderedSize = $derived.by(() => {
	const cw = containerSize.width;
	if (cw <= 0) return { width: 0, height: 0 };
	const ch = cw / aspect;
	return { width: cw, height: ch };
});

const streamUrl = $derived(cameraId ? previewUrl(cameraId) : '');

let resizeObserver;

onMount(() => {
	resizeObserver = new ResizeObserver(() => {
		if (containerEl) {
			const r = containerEl.getBoundingClientRect();
			containerSize = { width: r.width, height: r.height };
		}
	});
	resizeObserver.observe(containerEl);
});

onDestroy(() => {
	resizeObserver?.disconnect();
});

function handleImgError() {
	imageError = true;
	setTimeout(() => {
		if (imgEl && cameraOpen) {
			imageError = false;
			imgEl.src = `${streamUrl}?t=${Date.now()}`;
		}
	}, 1500);
}

function startEditing() {
	pendingLine = { ...line };
	editing = true;
	saveError = null;
}

function cancelEditing() {
	editing = false;
	dragHandle = null;
	pendingLine = { ...line };
}

async function saveLine() {
	if (!cameraId) return;
	saving = true;
	saveError = null;
	try {
		const clamped = {
			x1: clamp01(pendingLine.x1),
			y1: clamp01(pendingLine.y1),
			x2: clamp01(pendingLine.x2),
			y2: clamp01(pendingLine.y2)
		};
		await api.setLine(cameraId, clamped);
		editing = false;
		dragHandle = null;
	} catch (err) {
		saveError = err.message ?? String(err);
	} finally {
		saving = false;
	}
}

function clamp01(v) {
	return Math.max(0, Math.min(1, v));
}

function eventToNorm(e) {
	const rect = imgEl?.getBoundingClientRect() ?? containerEl.getBoundingClientRect();
	const px = ('clientX' in e) ? e.clientX : (e.touches?.[0]?.clientX ?? 0);
	const py = ('clientY' in e) ? e.clientY : (e.touches?.[0]?.clientY ?? 0);
	const x = (px - rect.left) / Math.max(1, rect.width);
	const y = (py - rect.top) / Math.max(1, rect.height);
	return { x: clamp01(x), y: clamp01(y) };
}

function onPointerDown(handle) {
	return (e) => {
		if (!editing) return;
		e.preventDefault();
		dragHandle = handle;
		try {
			e.target.setPointerCapture?.(e.pointerId);
		} catch {
			// not all browsers
		}
	};
}

function onPointerMove(e) {
	if (!editing || !dragHandle) return;
	const { x, y } = eventToNorm(e);
	pendingLine = {
		...pendingLine,
		[`x${dragHandle}`]: x,
		[`y${dragHandle}`]: y
	};
}

function onPointerUp() {
	dragHandle = null;
}

function setHorizontal() {
	pendingLine = { x1: 0.05, y1: 0.5, x2: 0.95, y2: 0.5 };
}

function setVertical() {
	pendingLine = { x1: 0.5, y1: 0.05, x2: 0.5, y2: 0.95 };
}

const linePixels = $derived.by(() => {
	const w = renderedSize.width;
	const h = renderedSize.height;
	const src = editing ? pendingLine : line;
	return {
		x1: (src?.x1 ?? 0.5) * w,
		y1: (src?.y1 ?? 0) * h,
		x2: (src?.x2 ?? 0.5) * w,
		y2: (src?.y2 ?? 1) * h
	};
});
</script>

<div class="preview-card">
	<div class="preview-header">
		<div class="preview-title">
			<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none">
				<path d="M23 7L16 12L23 17V7Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
				<rect x="1" y="5" width="15" height="14" rx="2" stroke="currentColor" stroke-width="2"/>
			</svg>
			<span>Live preview</span>
		</div>
		<div class="preview-actions">
			{#if !editing}
				<button class="btn-light-pill" onclick={startEditing} disabled={!cameraOpen}>
					<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none">
						<path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
						<path d="M18.5 2.5a2.121 2.121 0 113 3L12 15l-4 1 1-4 9.5-9.5z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
					</svg>
					Edit line
				</button>
			{:else}
				<button class="btn-light-pill" onclick={setVertical}>Vertical</button>
				<button class="btn-light-pill" onclick={setHorizontal}>Horizontal</button>
				<button class="btn-light-pill" onclick={cancelEditing} disabled={saving}>Cancel</button>
				<button class="btn-dark-pill" onclick={saveLine} disabled={saving}>
					{saving ? 'Saving…' : 'Save line'}
				</button>
			{/if}
		</div>
	</div>

	<div
		bind:this={containerEl}
		class="preview-container"
		style:aspect-ratio="{aspect}"
	>
		{#if !cameraRunning}
			<div class="preview-fallback">
				<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 24 24" fill="none">
					<rect x="3" y="6" width="14" height="12" rx="2" stroke="currentColor" stroke-width="2"/>
					<path d="M21 9l-4 3 4 3V9z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
				</svg>
				<p>Camera stopped</p>
				<small>Press <strong>Start</strong> in the camera row to begin streaming.</small>
			</div>
		{:else if !cameraOpen}
			<div class="preview-fallback">
				{#if lastError}
					<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 24 24" fill="none">
						<path d="M1 1l22 22M21 21H3a2 2 0 01-2-2V5M5 5h2l2-3h6l2 3h4a2 2 0 012 2v9.34" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
					</svg>
					<p>Camera unavailable</p>
					<small>{lastError}</small>
				{:else}
					<div class="spinner"></div>
					<p>Connecting to camera…</p>
				{/if}
			</div>
		{:else if imageError}
			<div class="preview-fallback">
				<div class="spinner"></div>
				<p>Reconnecting to camera stream…</p>
			</div>
		{:else}
			<img
				bind:this={imgEl}
				src={streamUrl}
				alt="Live counter preview"
				draggable="false"
				onerror={handleImgError}
			/>
		{/if}

		{#if cameraOpen && cameraRunning && !imageError}
			<svg
				class="overlay"
				class:editing
				viewBox="0 0 {renderedSize.width || 1} {renderedSize.height || 1}"
				preserveAspectRatio="none"
				onpointermove={onPointerMove}
				onpointerup={onPointerUp}
				onpointercancel={onPointerUp}
				role={editing ? 'application' : 'presentation'}
			>
				<!-- Read-only polygon zones -->
				{#each polygonStrings as z}
					{#if z.points}
						<polygon
							points={z.points}
							fill="rgba(40, 167, 69, 0.10)"
							stroke="rgba(40, 167, 69, 0.85)"
							stroke-width="2"
							stroke-dasharray="6 4"
						/>
					{/if}
				{/each}

				<!-- Secondary lines -->
				{#each otherLines as ol}
					<line
						x1={(ol.x1 ?? 0) * (renderedSize.width || 1)}
						y1={(ol.y1 ?? 0) * (renderedSize.height || 1)}
						x2={(ol.x2 ?? 0) * (renderedSize.width || 1)}
						y2={(ol.y2 ?? 0) * (renderedSize.height || 1)}
						stroke="rgba(255, 214, 51, 0.85)"
						stroke-width="2"
						stroke-dasharray="6 4"
					/>
				{/each}

				<line
					x1={linePixels.x1}
					y1={linePixels.y1}
					x2={linePixels.x2}
					y2={linePixels.y2}
					stroke={editing ? '#ffd633' : 'rgba(255,255,255,0.85)'}
					stroke-width={editing ? 4 : 3}
					stroke-dasharray={editing ? '8 4' : '0'}
				/>
				{#if editing}
					<circle
						cx={linePixels.x1}
						cy={linePixels.y1}
						r="14"
						fill="#ffd633"
						stroke="#212529"
						stroke-width="2"
						role="slider"
						tabindex="0"
						aria-label="Line start handle"
						aria-valuenow={Math.round(pendingLine.x1 * 100)}
						style="cursor: grab; touch-action: none;"
						onpointerdown={onPointerDown(1)}
					/>
					<circle
						cx={linePixels.x2}
						cy={linePixels.y2}
						r="14"
						fill="#ffd633"
						stroke="#212529"
						stroke-width="2"
						role="slider"
						tabindex="0"
						aria-label="Line end handle"
						aria-valuenow={Math.round(pendingLine.x2 * 100)}
						style="cursor: grab; touch-action: none;"
						onpointerdown={onPointerDown(2)}
					/>
				{/if}
			</svg>
		{/if}
	</div>

	{#if editing}
		<div class="preview-help">
			<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none">
				<path d="M13 16H12V12H11M12 8H12.01M21 12C21 16.9706 16.9706 21 12 21C7.02944 21 3 16.9706 3 12C3 7.02944 7.02944 3 12 3C16.9706 3 21 7.02944 21 12Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
			</svg>
			<span>
				Drag the yellow handles to set where the line crosses the doorway.
				People crossing this line are counted.
			</span>
		</div>
	{/if}

	{#if saveError}
		<div class="preview-error">{saveError}</div>
	{/if}
</div>

<style>
.preview-card {
	background: #fff;
	border: 2px solid rgb(215, 215, 215);
	border-radius: 16px;
	padding: 16px;
	display: flex;
	flex-direction: column;
	gap: 12px;
}

.preview-header {
	display: flex;
	align-items: center;
	justify-content: space-between;
	gap: 12px;
	flex-wrap: wrap;
}

.preview-title {
	display: flex;
	align-items: center;
	gap: 8px;
	font-weight: 600;
	font-size: 16px;
	color: #212529;
}

.preview-actions {
	display: flex;
	gap: 8px;
	flex-wrap: wrap;
}

.btn-light-pill,
.btn-dark-pill {
	border: 2px solid rgb(33, 37, 41);
	border-radius: 12px;
	padding: 6px 14px;
	font-size: 14px;
	font-weight: 500;
	cursor: pointer;
	transition: opacity 0.15s ease;
	font-family: inherit;
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
	opacity: 0.4;
	cursor: not-allowed;
}

.preview-container {
	position: relative;
	width: 100%;
	background: #000;
	border-radius: 12px;
	overflow: hidden;
}

.preview-container img {
	width: 100%;
	height: 100%;
	object-fit: contain;
	display: block;
}

.overlay {
	position: absolute;
	inset: 0;
	width: 100%;
	height: 100%;
	pointer-events: none;
}

.overlay.editing {
	pointer-events: auto;
}

.preview-fallback {
	position: absolute;
	inset: 0;
	display: flex;
	flex-direction: column;
	align-items: center;
	justify-content: center;
	gap: 12px;
	color: rgba(255, 255, 255, 0.85);
	text-align: center;
	padding: 16px;
}

.preview-fallback small {
	color: rgba(255, 255, 255, 0.7);
	max-width: 360px;
}

.preview-fallback strong {
	color: #fff;
}

.spinner {
	width: 32px;
	height: 32px;
	border: 3px solid rgba(255, 255, 255, 0.2);
	border-top-color: #fff;
	border-radius: 50%;
	animation: spin 0.9s linear infinite;
}

@keyframes spin {
	to {
		transform: rotate(360deg);
	}
}

.preview-help {
	display: flex;
	align-items: center;
	gap: 8px;
	font-size: 14px;
	color: #495057;
	background: #fff8d4;
	border-radius: 12px;
	padding: 10px 14px;
}

.preview-error {
	color: #dc3545;
	font-size: 14px;
	background: #fdeaec;
	border-radius: 8px;
	padding: 8px 12px;
}
</style>
