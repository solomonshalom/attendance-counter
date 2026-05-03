<script>
// @ts-nocheck
import { onMount } from 'svelte';
import { api, previewUrl } from '../api.js';

let { open = false, camera = null, venue = null, onclose = () => {} } = $props();

/** @type {{img:{x:number,y:number}|null, world:{x:number,y:number}|null}[]} */
let pairs = $state([]);
/** @type {'idle'|'img'|'world'} */
let addMode = $state('idle');
let activeIdx = $state(-1);
let dragKind = $state(null);
let dragIdx = $state(-1);

let busy = $state(false);
let saveError = $state(null);
let lastResult = $state(null); // { homography, reprojection_error_m, points }

let camImg = $state(null);
let camRect = $state({ width: 0, height: 0 });
let worldRect = $state({ width: 0, height: 0 });
let camContainer = $state(null);
let worldContainer = $state(null);
let resizeObs;

const camId = $derived(camera?.camera_id);
const venueW = $derived(venue?.floor_plan_w_m ?? 20);
const venueH = $derived(venue?.floor_plan_h_m ?? 20);
const aspect = $derived(
	camera?.status?.frame_width && camera?.status?.frame_height
		? camera.status.frame_width / camera.status.frame_height
		: 16 / 9
);
const streamUrl = $derived(camId ? previewUrl(camId) : '');

// World canvas aspect ratio is locked to venue meters so distances are visible.
const worldAspect = $derived(venueW / venueH);

const completePairs = $derived(pairs.filter((p) => p?.img && p?.world));
const canCompute = $derived(completePairs.length >= 4);

$effect(() => {
	if (open && camId) {
		load();
	}
});

async function load() {
	pairs = [];
	addMode = 'idle';
	activeIdx = -1;
	saveError = null;
	lastResult = null;
	busy = false;
	try {
		const calib = await api.getCalibration(camId);
		if (calib?.points?.length) {
			pairs = calib.points.map((p) => ({
				img: p.img ? { x: p.img.x, y: p.img.y } : null,
				world: p.world ? { x: p.world.x, y: p.world.y } : null
			}));
			lastResult = {
				homography: calib.homography,
				reprojection_error_m: calib.reprojection_error_m,
				points: calib.points
			};
		}
	} catch (err) {
		saveError = err?.message ?? String(err);
	}
}

function clamp01(v) { return Math.max(0, Math.min(1, v)); }

function rectOf(el) { return el?.getBoundingClientRect() ?? { left: 0, top: 0, width: 1, height: 1 }; }

function imgEventToNorm(e) {
	const rect = camImg?.getBoundingClientRect() ?? rectOf(camContainer);
	const px = ('clientX' in e ? e.clientX : e.touches?.[0]?.clientX ?? 0);
	const py = ('clientY' in e ? e.clientY : e.touches?.[0]?.clientY ?? 0);
	return {
		x: clamp01((px - rect.left) / Math.max(1, rect.width)),
		y: clamp01((py - rect.top) / Math.max(1, rect.height))
	};
}

function worldEventToMeters(e) {
	const rect = rectOf(worldContainer);
	const px = ('clientX' in e ? e.clientX : e.touches?.[0]?.clientX ?? 0);
	const py = ('clientY' in e ? e.clientY : e.touches?.[0]?.clientY ?? 0);
	const fx = (px - rect.left) / Math.max(1, rect.width);
	const fy = (py - rect.top) / Math.max(1, rect.height);
	return {
		x: Math.max(0, Math.min(venueW, fx * venueW)),
		y: Math.max(0, Math.min(venueH, fy * venueH))
	};
}

function startNewPair() {
	pairs = [...pairs, { img: null, world: null }];
	activeIdx = pairs.length - 1;
	addMode = 'img';
}

function setPair(idx, kind, value) {
	pairs = pairs.map((p, i) => (i === idx ? { ...p, [kind]: value } : p));
}

function removePair(idx) {
	pairs = pairs.filter((_, i) => i !== idx);
	if (activeIdx >= pairs.length) activeIdx = pairs.length - 1;
	if (activeIdx < 0) addMode = 'idle';
}

function onCamClick(e) {
	if (dragKind) return; // ignore click that was actually a drag end
	if (addMode === 'img' && activeIdx >= 0) {
		const pt = imgEventToNorm(e);
		setPair(activeIdx, 'img', pt);
		// If world is still unset for this pair, ask for it next.
		const cur = pairs[activeIdx];
		addMode = cur?.world ? 'idle' : 'world';
	} else if (addMode === 'idle') {
		// If user clicks without an active pair, start one and capture this click as the img point.
		const pt = imgEventToNorm(e);
		pairs = [...pairs, { img: pt, world: null }];
		activeIdx = pairs.length - 1;
		addMode = 'world';
	}
}

function onWorldClick(e) {
	if (dragKind) return;
	if (addMode === 'world' && activeIdx >= 0) {
		const pt = worldEventToMeters(e);
		setPair(activeIdx, 'world', pt);
		const cur = pairs[activeIdx];
		addMode = cur?.img ? 'idle' : 'img';
	} else if (addMode === 'idle') {
		const pt = worldEventToMeters(e);
		pairs = [...pairs, { img: null, world: pt }];
		activeIdx = pairs.length - 1;
		addMode = 'img';
	}
}

function onPointDown(idx, kind) {
	return (e) => {
		e.preventDefault();
		e.stopPropagation();
		dragIdx = idx;
		dragKind = kind;
		activeIdx = idx;
		try { e.target.setPointerCapture?.(e.pointerId); } catch { /* */ }
	};
}

function onCamPointerMove(e) {
	if (dragKind !== 'img') return;
	const pt = imgEventToNorm(e);
	setPair(dragIdx, 'img', pt);
}

function onWorldPointerMove(e) {
	if (dragKind !== 'world') return;
	const pt = worldEventToMeters(e);
	setPair(dragIdx, 'world', pt);
}

function onPointerUp() {
	if (dragKind) {
		const wasKind = dragKind;
		queueMicrotask(() => {
			if (wasKind === dragKind) dragKind = null;
		});
	}
}

async function compute() {
	if (!canCompute || !camId) return;
	const valid = pairs.filter((p) => p?.img && p?.world);
	busy = true;
	saveError = null;
	try {
		const res = await api.saveCalibration(camId, valid);
		lastResult = res;
	} catch (err) {
		saveError = err?.message ?? String(err);
	} finally {
		busy = false;
	}
}

async function clearAll() {
	if (!camId) return;
	if (!confirm('Clear all calibration data for this camera?')) return;
	busy = true;
	saveError = null;
	try {
		await api.clearCalibration(camId);
		pairs = [];
		lastResult = null;
		addMode = 'idle';
		activeIdx = -1;
	} catch (err) {
		saveError = err?.message ?? String(err);
	} finally {
		busy = false;
	}
}

onMount(() => {
	resizeObs = new ResizeObserver(() => {
		if (camContainer) {
			const r = camContainer.getBoundingClientRect();
			camRect = { width: r.width, height: r.height };
		}
		if (worldContainer) {
			const r = worldContainer.getBoundingClientRect();
			worldRect = { width: r.width, height: r.height };
		}
	});
	if (camContainer) resizeObs.observe(camContainer);
	if (worldContainer) resizeObs.observe(worldContainer);
});

function onKey(e) {
	if (!open) return;
	if (e.key === 'Escape' && !busy) {
		e.preventDefault();
		onclose();
	}
}
</script>

<svelte:window onkeydown={onKey} onpointerup={onPointerUp} />

{#if open}
	<div
		class="backdrop"
		role="presentation"
		onclick={(e) => { if (e.target === e.currentTarget && !busy) onclose(); }}
	>
		<div role="dialog" aria-modal="true" aria-labelledby="cal-title">
		<div class="dialog">
			<div class="dialog-head">
				<div class="dialog-titles">
					<h2 id="cal-title">Calibrate {camera?.camera_name ?? 'camera'}</h2>
					<div class="dialog-sub">Map camera pixels to floor positions in <strong>{venue?.name}</strong>. Drop matching points on each side, ≥4 pairs.</div>
				</div>
				<button type="button" class="close-btn" aria-label="Close" onclick={onclose} disabled={busy}>
					<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none">
						<path d="M18 6L6 18M6 6l12 12" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
					</svg>
				</button>
			</div>

			<div class="cal-instructions">
				{#if addMode === 'img'}
					<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none">
						<circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2"/>
						<path d="M12 7v5l3 2" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
					</svg>
					<span>Click on the <strong>camera preview</strong> to mark this point.</span>
				{:else if addMode === 'world'}
					<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none">
						<circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2"/>
						<path d="M12 7v5l3 2" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
					</svg>
					<span>Now click on the <strong>floor plan</strong> at the same real-world location.</span>
				{:else}
					<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none">
						<path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
					</svg>
					<span>Pick four or more landmarks (corners of the rug, base of pillars, doorway corners) and mark them on both panels.</span>
				{/if}
			</div>

			<div class="cal-grid">
				<div class="cal-pane">
					<div class="cal-pane-head">
						<span class="cal-pane-title">Camera view</span>
						<span class="cal-pane-meta">{camera?.status?.frame_width || 0}×{camera?.status?.frame_height || 0}</span>
					</div>
					<div
						bind:this={camContainer}
						class="cal-canvas"
						style:aspect-ratio="{aspect}"
						class:active={addMode === 'img'}
					>
						<img bind:this={camImg} src={streamUrl} alt="Camera preview" draggable="false" />
						<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
						<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
						<svg
							class="cal-overlay"
							viewBox="0 0 {camRect.width || 1} {camRect.height || 1}"
							preserveAspectRatio="none"
							onclick={onCamClick}
							onkeydown={() => { /* canvas is mouse-only */ }}
							onpointermove={onCamPointerMove}
							role="application"
							aria-label="Camera canvas — click to mark image points"
						>
							{#each pairs as p, i (i)}
								{#if p?.img}
									<circle
										cx={p.img.x * (camRect.width || 1)}
										cy={p.img.y * (camRect.height || 1)}
										r="10"
										fill={i === activeIdx ? '#ffd633' : (p?.world ? '#28a745' : '#fff')}
										stroke="#212529"
										stroke-width="2"
										style="cursor: grab; touch-action: none;"
										role="slider"
										tabindex="0"
										aria-label={`pair ${i + 1} image point`}
										aria-valuenow={Math.round(p.img.x * 100)}
										onpointerdown={onPointDown(i, 'img')}
									/>
									<text
										x={p.img.x * (camRect.width || 1)}
										y={p.img.y * (camRect.height || 1) - 14}
										font-family="Inter, sans-serif"
										font-size="13"
										font-weight="700"
										fill="#fff"
										stroke="rgba(0,0,0,0.7)"
										stroke-width="3"
										paint-order="stroke"
										text-anchor="middle"
										pointer-events="none"
									>{i + 1}</text>
								{/if}
							{/each}
						</svg>
					</div>
				</div>

				<div class="cal-pane">
					<div class="cal-pane-head">
						<span class="cal-pane-title">Floor plan</span>
						<span class="cal-pane-meta">{venueW} × {venueH} m</span>
					</div>
					<div
						bind:this={worldContainer}
						class="cal-canvas world"
						style:aspect-ratio="{worldAspect}"
						class:active={addMode === 'world'}
					>
						<svg
							class="cal-floor"
							viewBox="0 0 {venueW} {venueH}"
							preserveAspectRatio="none"
						>
							{#each Array.from({ length: Math.floor(venueW) + 1 }) as _, i}
								<line x1={i} y1="0" x2={i} y2={venueH} stroke="#e9ecef" stroke-width="0.02" />
							{/each}
							{#each Array.from({ length: Math.floor(venueH) + 1 }) as _, i}
								<line x1="0" y1={i} x2={venueW} y2={i} stroke="#e9ecef" stroke-width="0.02" />
							{/each}
							<rect x="0" y="0" width={venueW} height={venueH} fill="none" stroke="#212529" stroke-width="0.06" />
						</svg>
						<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
						<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
						<svg
							class="cal-overlay"
							viewBox="0 0 {worldRect.width || 1} {worldRect.height || 1}"
							preserveAspectRatio="none"
							onclick={onWorldClick}
							onkeydown={() => { /* canvas is mouse-only */ }}
							onpointermove={onWorldPointerMove}
							role="application"
							aria-label="Floor plan canvas — click to mark world points"
						>
							{#each pairs as p, i (i)}
								{#if p?.world}
									<circle
										cx={(p.world.x / venueW) * (worldRect.width || 1)}
										cy={(p.world.y / venueH) * (worldRect.height || 1)}
										r="10"
										fill={i === activeIdx ? '#ffd633' : (p?.img ? '#28a745' : '#fff')}
										stroke="#212529"
										stroke-width="2"
										style="cursor: grab; touch-action: none;"
										role="slider"
										tabindex="0"
										aria-label={`pair ${i + 1} world point`}
										aria-valuenow={Math.round(p.world.x)}
										onpointerdown={onPointDown(i, 'world')}
									/>
									<text
										x={(p.world.x / venueW) * (worldRect.width || 1)}
										y={(p.world.y / venueH) * (worldRect.height || 1) - 14}
										font-family="Inter, sans-serif"
										font-size="13"
										font-weight="700"
										fill="#212529"
										stroke="#fff"
										stroke-width="3"
										paint-order="stroke"
										text-anchor="middle"
										pointer-events="none"
									>{i + 1}</text>
								{/if}
							{/each}
						</svg>
					</div>
				</div>
			</div>

			<div class="pairs-bar">
				<button class="btn-light-pill btn-sm" onclick={startNewPair} disabled={busy}>
					<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none">
						<path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
					</svg>
					Add point pair
				</button>
				<span class="pairs-count">{pairs.length} pair{pairs.length === 1 ? '' : 's'} · {completePairs.length} complete</span>
				{#if pairs.length > 0}
					<ul class="pair-list">
						{#each pairs as p, i (i)}
							<li>
								<div
									role="button"
									tabindex="0"
									class="pair-row"
									class:selected={i === activeIdx}
									class:complete={p?.img && p?.world}
									onclick={() => { activeIdx = i; addMode = !p?.img ? 'img' : (!p?.world ? 'world' : 'idle'); }}
									onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { activeIdx = i; addMode = !p?.img ? 'img' : (!p?.world ? 'world' : 'idle'); } }}
								>
									<span class="pair-num">{i + 1}</span>
									<span class="pair-meta">
										{#if p?.img}img: {Math.round(p.img.x * 100)}, {Math.round(p.img.y * 100)}{:else}img: —{/if}
										·
										{#if p?.world}world: {p.world.x.toFixed(2)}, {p.world.y.toFixed(2)} m{:else}world: —{/if}
									</span>
									<button class="icon-x" onclick={(e) => { e.stopPropagation(); removePair(i); }} aria-label="Remove pair">×</button>
								</div>
							</li>
						{/each}
					</ul>
				{/if}
			</div>

			{#if lastResult?.homography}
				<div class="result-card">
					<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none">
						<path d="M5 13l4 4L19 7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
					</svg>
					<span>
						<strong>Calibration saved.</strong>
						Mean reprojection error
						<strong>{(lastResult.reprojection_error_m ?? 0).toFixed(3)} m</strong>.
						{#if (lastResult.reprojection_error_m ?? 0) > 0.5}
							That's a bit high — re-check that points are flat on the floor and not collinear.
						{/if}
					</span>
				</div>
			{/if}

			{#if saveError}
				<div class="dialog-error">{saveError}</div>
			{/if}

			<div class="dialog-actions">
				<button type="button" class="btn-light-pill btn-danger" onclick={clearAll} disabled={busy || pairs.length === 0}>
					Clear calibration
				</button>
				<div class="spacer"></div>
				<button type="button" class="btn-light-pill" onclick={onclose} disabled={busy}>Done</button>
				<button type="button" class="btn-dark-pill" onclick={compute} disabled={busy || !canCompute}>
					{busy ? 'Computing…' : 'Compute & save'}
				</button>
			</div>
		</div>
		</div>
	</div>
{/if}

<style>
.backdrop {
	position: fixed;
	inset: 0;
	background: rgba(0, 0, 0, 0.55);
	display: flex;
	align-items: center;
	justify-content: center;
	padding: 16px;
	z-index: 1100;
}

.dialog {
	background: #fff;
	border-radius: 16px;
	padding: 18px;
	width: 100%;
	max-width: 1100px;
	max-height: calc(100vh - 32px);
	display: flex;
	flex-direction: column;
	gap: 12px;
	box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
	font-family: inherit;
	overflow-y: auto;
}

.dialog-head {
	display: flex;
	align-items: flex-start;
	justify-content: space-between;
	gap: 12px;
}

.dialog-titles {
	display: flex;
	flex-direction: column;
	gap: 2px;
}

.dialog-head h2 {
	font-family: 'Inter Tight', sans-serif;
	font-size: 20px;
	font-weight: 600;
	margin: 0;
	color: rgb(33, 37, 41);
}

.dialog-sub {
	font-size: 13px;
	color: #6c757d;
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

.cal-instructions {
	display: flex;
	align-items: center;
	gap: 8px;
	font-size: 13px;
	color: #495057;
	background: #fff8d4;
	border: 1px solid #ffe69c;
	border-radius: 12px;
	padding: 8px 12px;
}

.cal-grid {
	display: grid;
	grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
	gap: 12px;
}

@media (max-width: 900px) {
	.cal-grid {
		grid-template-columns: 1fr;
	}
}

.cal-pane {
	display: flex;
	flex-direction: column;
	gap: 6px;
	min-width: 0;
}

.cal-pane-head {
	display: flex;
	align-items: center;
	justify-content: space-between;
	font-size: 12px;
	color: #495057;
}

.cal-pane-title {
	font-weight: 600;
	font-size: 13px;
	color: #212529;
}

.cal-pane-meta {
	font-family: 'JetBrains Mono', ui-monospace, monospace;
	font-size: 11px;
	color: #6c757d;
}

.cal-canvas {
	position: relative;
	width: 100%;
	background: #000;
	border-radius: 12px;
	overflow: hidden;
	border: 2px solid rgb(215, 215, 215);
}

.cal-canvas.world {
	background: #f8f9fa;
}

.cal-canvas.active {
	border-color: #ffd633;
	box-shadow: 0 0 0 2px rgba(255, 214, 51, 0.3);
}

.cal-canvas img {
	width: 100%;
	height: 100%;
	object-fit: contain;
	display: block;
}

.cal-floor {
	position: absolute;
	inset: 0;
	width: 100%;
	height: 100%;
}

.cal-overlay {
	position: absolute;
	inset: 0;
	width: 100%;
	height: 100%;
	cursor: crosshair;
}

.pairs-bar {
	display: flex;
	flex-direction: column;
	gap: 6px;
	background: #f8f9fa;
	border: 2px solid rgb(215, 215, 215);
	border-radius: 12px;
	padding: 10px;
	max-height: 180px;
	overflow-y: auto;
}

.pairs-count {
	font-size: 12px;
	color: #6c757d;
}

.pair-list {
	list-style: none;
	margin: 0;
	padding: 0;
	display: flex;
	flex-direction: column;
	gap: 4px;
}

.pair-row {
	display: flex;
	align-items: center;
	gap: 8px;
	width: 100%;
	border: 2px solid rgb(215, 215, 215);
	background: #fff;
	border-radius: 10px;
	padding: 4px 8px;
	font-size: 12px;
	font-family: inherit;
	cursor: pointer;
	color: #212529;
}

.pair-row.selected {
	border-color: rgb(33, 37, 41);
	background: #fff8d4;
}

.pair-row.complete .pair-num {
	background: #28a745;
	color: #fff;
}

.pair-num {
	width: 22px;
	height: 22px;
	border-radius: 999px;
	background: #f1f3f5;
	color: #495057;
	display: inline-flex;
	align-items: center;
	justify-content: center;
	font-weight: 700;
	font-size: 11px;
}

.pair-row.selected .pair-num:not(.complete) {
	background: #ffd633;
	color: #212529;
}

.pair-meta {
	flex: 1;
	font-family: 'JetBrains Mono', ui-monospace, monospace;
	font-size: 11px;
	color: #495057;
	overflow: hidden;
	text-overflow: ellipsis;
	white-space: nowrap;
	text-align: left;
}

.icon-x {
	background: transparent;
	border: none;
	color: #adb5bd;
	font-size: 16px;
	cursor: pointer;
	font-family: inherit;
	padding: 0 4px;
	line-height: 1;
}

.icon-x:hover { color: #b02a37; }

.result-card {
	display: flex;
	align-items: center;
	gap: 8px;
	background: #d1f4e0;
	border: 1px solid #b5e8c8;
	color: #146c43;
	border-radius: 12px;
	padding: 10px 12px;
	font-size: 13px;
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
	gap: 8px;
	align-items: center;
}

.spacer { flex: 1; }

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

.btn-light-pill { background: #fff; color: rgb(33, 37, 41); }
.btn-dark-pill { background: rgb(33, 37, 41); color: #fff; }

.btn-light-pill:disabled,
.btn-dark-pill:disabled { opacity: 0.45; cursor: not-allowed; }

.btn-sm { padding: 4px 10px; font-size: 12px; border-radius: 10px; }

.btn-danger {
	color: #b02a37;
	border-color: #f5c2c7;
}
</style>
