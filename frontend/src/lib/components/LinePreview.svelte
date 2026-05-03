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

// ------------------------------------------------------------------ container

let containerEl;
let imgEl = $state(null);
let containerSize = $state({ width: 0, height: 0 });
let imageError = $state(false);

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
onDestroy(() => resizeObserver?.disconnect());

function handleImgError() {
	imageError = true;
	setTimeout(() => {
		if (imgEl && cameraOpen) {
			imageError = false;
			imgEl.src = `${streamUrl}?t=${Date.now()}`;
		}
	}, 1500);
}

function clamp01(v) { return Math.max(0, Math.min(1, v)); }

function eventToNorm(e) {
	const rect = imgEl?.getBoundingClientRect() ?? containerEl.getBoundingClientRect();
	const px = ('clientX' in e) ? e.clientX : (e.touches?.[0]?.clientX ?? 0);
	const py = ('clientY' in e) ? e.clientY : (e.touches?.[0]?.clientY ?? 0);
	const x = (px - rect.left) / Math.max(1, rect.width);
	const y = (py - rect.top) / Math.max(1, rect.height);
	return { x: clamp01(x), y: clamp01(y) };
}

// ------------------------------------------------------------------ mode

let mode = $state('view'); // 'view' | 'line' | 'zones'
let saving = $state(false);
let saveError = $state(null);

function startLineEdit() {
	pendingLine = { ...(line || { x1: 0.5, y1: 0, x2: 0.5, y2: 1 }) };
	mode = 'line';
	saveError = null;
}

function startZonesEdit() {
	// Deep-copy current zones for local editing.
	pendingZones = (zones || []).map((z) => ({
		name: z?.name ?? 'zone',
		role: z?.role ?? 'observer',
		polygon: (z?.polygon || []).map((p) => ({ x: p.x, y: p.y }))
	}));
	selectedZoneIdx = pendingZones.length > 0 ? 0 : -1;
	drawingMode = pendingZones.length === 0; // start drawing if there are no zones yet
	mode = 'zones';
	saveError = null;
}

function cancelEdit() {
	mode = 'view';
	dragHandle = null;
	zoneDrag = null;
	drawingMode = false;
	saveError = null;
}

// ------------------------------------------------------------------ line editor

let pendingLine = $state({ x1: 0.5, y1: 0, x2: 0.5, y2: 1 });
let dragHandle = $state(null);

$effect(() => {
	// Keep pendingLine in sync with the latest prop when not editing.
	if (mode !== 'line' && line) {
		pendingLine = { ...line };
	}
});

const linePixels = $derived.by(() => {
	const w = renderedSize.width;
	const h = renderedSize.height;
	const src = mode === 'line' ? pendingLine : line;
	return {
		x1: (src?.x1 ?? 0.5) * w,
		y1: (src?.y1 ?? 0) * h,
		x2: (src?.x2 ?? 0.5) * w,
		y2: (src?.y2 ?? 1) * h
	};
});

function lineHandleDown(handle) {
	return (e) => {
		if (mode !== 'line') return;
		e.preventDefault();
		dragHandle = handle;
		try { e.target.setPointerCapture?.(e.pointerId); } catch { /* */ }
	};
}

function setLineVertical() { pendingLine = { x1: 0.5, y1: 0.05, x2: 0.5, y2: 0.95 }; }
function setLineHorizontal() { pendingLine = { x1: 0.05, y1: 0.5, x2: 0.95, y2: 0.5 }; }

async function saveLine() {
	if (!cameraId) return;
	saving = true;
	saveError = null;
	try {
		await api.setLine(cameraId, {
			x1: clamp01(pendingLine.x1),
			y1: clamp01(pendingLine.y1),
			x2: clamp01(pendingLine.x2),
			y2: clamp01(pendingLine.y2)
		});
		mode = 'view';
		dragHandle = null;
	} catch (err) {
		saveError = err.message ?? String(err);
	} finally {
		saving = false;
	}
}

// ------------------------------------------------------------------ zone editor

let pendingZones = $state([]);
let selectedZoneIdx = $state(-1);
let drawingMode = $state(false); // when true, clicks on the canvas add vertices to the selected zone
/** @type {null | {kind:'vertex'|'whole', zoneIdx:number, vertexIdx?:number, lastX?:number, lastY?:number}} */
let zoneDrag = $state(null);

const ROLE_OPTIONS = [
	{ value: 'interior', label: 'Interior (room body)' },
	{ value: 'entry', label: 'Entry (lobby/foyer)' },
	{ value: 'exit', label: 'Exit (vestibule)' },
	{ value: 'observer', label: 'Observer (watch only)' }
];

function addZone() {
	const idx = pendingZones.length;
	pendingZones = [
		...pendingZones,
		{ name: `Zone ${idx + 1}`, role: 'interior', polygon: [] }
	];
	selectedZoneIdx = idx;
	drawingMode = true;
}

function removeSelectedZone() {
	if (selectedZoneIdx < 0) return;
	pendingZones = pendingZones.filter((_, i) => i !== selectedZoneIdx);
	selectedZoneIdx = pendingZones.length > 0 ? Math.min(selectedZoneIdx, pendingZones.length - 1) : -1;
	drawingMode = false;
}

function selectZone(idx) {
	selectedZoneIdx = idx;
	drawingMode = false;
}

function setZoneName(idx, name) {
	pendingZones = pendingZones.map((z, i) => (i === idx ? { ...z, name } : z));
}

function setZoneRole(idx, role) {
	pendingZones = pendingZones.map((z, i) => (i === idx ? { ...z, role } : z));
}

function presetFullFrame() {
	if (selectedZoneIdx < 0) return;
	pendingZones = pendingZones.map((z, i) =>
		i === selectedZoneIdx
			? {
					...z,
					polygon: [
						{ x: 0.02, y: 0.02 },
						{ x: 0.98, y: 0.02 },
						{ x: 0.98, y: 0.98 },
						{ x: 0.02, y: 0.98 }
					]
				}
			: z
	);
	drawingMode = false;
}

function presetRect() {
	if (selectedZoneIdx < 0) return;
	pendingZones = pendingZones.map((z, i) =>
		i === selectedZoneIdx
			? {
					...z,
					polygon: [
						{ x: 0.2, y: 0.2 },
						{ x: 0.8, y: 0.2 },
						{ x: 0.8, y: 0.8 },
						{ x: 0.2, y: 0.8 }
					]
				}
			: z
	);
	drawingMode = false;
}

function clearVertices() {
	if (selectedZoneIdx < 0) return;
	pendingZones = pendingZones.map((z, i) =>
		i === selectedZoneIdx ? { ...z, polygon: [] } : z
	);
	drawingMode = true;
}

function onCanvasClick(e) {
	if (mode !== 'zones' || !drawingMode || selectedZoneIdx < 0) return;
	if (zoneDrag) return; // ignore click that was actually a drag end
	const { x, y } = eventToNorm(e);
	const z = pendingZones[selectedZoneIdx];
	if (!z) return;
	// Auto-close: if user clicks within ~12px of the first vertex AND we have ≥3 vertices, finish.
	if (z.polygon.length >= 3) {
		const first = z.polygon[0];
		const w = renderedSize.width || 1;
		const h = renderedSize.height || 1;
		const dx = (first.x - x) * w;
		const dy = (first.y - y) * h;
		if (Math.hypot(dx, dy) < 14) {
			drawingMode = false;
			return;
		}
	}
	const next = { ...z, polygon: [...z.polygon, { x, y }] };
	pendingZones = pendingZones.map((zz, i) => (i === selectedZoneIdx ? next : zz));
}

function onVertexPointerDown(zoneIdx, vertexIdx) {
	return (e) => {
		if (mode !== 'zones') return;
		e.preventDefault();
		e.stopPropagation();
		zoneDrag = { kind: 'vertex', zoneIdx, vertexIdx };
		try { e.target.setPointerCapture?.(e.pointerId); } catch { /* */ }
	};
}

function onZoneBodyPointerDown(zoneIdx) {
	return (e) => {
		if (mode !== 'zones') return;
		e.preventDefault();
		e.stopPropagation();
		selectedZoneIdx = zoneIdx;
		drawingMode = false;
		const { x, y } = eventToNorm(e);
		zoneDrag = { kind: 'whole', zoneIdx, lastX: x, lastY: y };
		try { e.target.setPointerCapture?.(e.pointerId); } catch { /* */ }
	};
}

function removeVertex(zoneIdx, vertexIdx) {
	const z = pendingZones[zoneIdx];
	if (!z || z.polygon.length <= 3) return; // keep at least 3 to remain a polygon
	const next = { ...z, polygon: z.polygon.filter((_, i) => i !== vertexIdx) };
	pendingZones = pendingZones.map((zz, i) => (i === zoneIdx ? next : zz));
}

async function saveZones() {
	if (!cameraId) return;
	// Validate.
	for (const [i, z] of pendingZones.entries()) {
		if (!z.polygon || z.polygon.length < 3) {
			saveError = `"${z.name || `Zone ${i + 1}`}" needs at least 3 points.`;
			return;
		}
	}
	saving = true;
	saveError = null;
	try {
		await api.setZones(
			cameraId,
			pendingZones.map((z) => ({
				name: z.name,
				role: z.role,
				polygon: z.polygon.map((p) => ({ x: clamp01(p.x), y: clamp01(p.y) }))
			}))
		);
		mode = 'view';
		zoneDrag = null;
		drawingMode = false;
	} catch (err) {
		saveError = err.message ?? String(err);
	} finally {
		saving = false;
	}
}

// ------------------------------------------------------------------ unified pointer move/up

function onCanvasPointerMove(e) {
	// Line editor.
	if (mode === 'line' && dragHandle) {
		const { x, y } = eventToNorm(e);
		pendingLine = { ...pendingLine, [`x${dragHandle}`]: x, [`y${dragHandle}`]: y };
		return;
	}
	// Zone editor.
	if (mode === 'zones' && zoneDrag) {
		const { x, y } = eventToNorm(e);
		if (zoneDrag.kind === 'vertex') {
			pendingZones = pendingZones.map((z, i) => {
				if (i !== zoneDrag.zoneIdx) return z;
				const polygon = z.polygon.map((p, j) => (j === zoneDrag.vertexIdx ? { x, y } : p));
				return { ...z, polygon };
			});
		} else if (zoneDrag.kind === 'whole') {
			const dx = x - (zoneDrag.lastX ?? x);
			const dy = y - (zoneDrag.lastY ?? y);
			pendingZones = pendingZones.map((z, i) => {
				if (i !== zoneDrag.zoneIdx) return z;
				const polygon = z.polygon.map((p) => ({
					x: clamp01(p.x + dx),
					y: clamp01(p.y + dy)
				}));
				return { ...z, polygon };
			});
			zoneDrag = { ...zoneDrag, lastX: x, lastY: y };
		}
	}
}

function onCanvasPointerUp() {
	dragHandle = null;
	// Defer clearing zoneDrag so the click handler that follows doesn't fire.
	if (zoneDrag) {
		const wasDrag = zoneDrag;
		zoneDrag = null;
		// Use a microtask so the trailing click event doesn't get treated as a vertex drop.
		queueMicrotask(() => {
			if (wasDrag === zoneDrag) zoneDrag = null;
		});
	}
}

// ------------------------------------------------------------------ derived display data

const otherLines = $derived(
	(lines || []).filter((l, i) => i > 0 && l)
);

const polygonStrings = $derived.by(() => {
	const w = renderedSize?.width || 1;
	const h = renderedSize?.height || 1;
	const src = mode === 'zones' ? pendingZones : (zones || []);
	return src.map((z, idx) => {
		const pts = (z?.polygon || []).map((p) => `${(p.x ?? 0) * w},${(p.y ?? 0) * h}`).join(' ');
		return {
			idx,
			name: z?.name ?? '',
			role: z?.role ?? 'observer',
			points: pts,
			vertices: (z?.polygon || []).map((p) => ({ x: (p.x ?? 0) * w, y: (p.y ?? 0) * h })),
			isSelected: mode === 'zones' && idx === selectedZoneIdx
		};
	});
});

const selectedZone = $derived(selectedZoneIdx >= 0 ? pendingZones[selectedZoneIdx] : null);
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
			{#if mode === 'view'}
				<button class="btn-light-pill" onclick={startLineEdit} disabled={!cameraOpen}>
					<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none">
						<path d="M5 12h14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
					</svg>
					Edit line
				</button>
				<button class="btn-light-pill" onclick={startZonesEdit} disabled={!cameraOpen}>
					<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none">
						<path d="M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>
					</svg>
					Edit zones
					{#if (zones?.length ?? 0) > 0}
						<span class="zone-pill">{zones.length}</span>
					{/if}
				</button>
			{:else if mode === 'line'}
				<button class="btn-light-pill" onclick={setLineVertical}>Vertical</button>
				<button class="btn-light-pill" onclick={setLineHorizontal}>Horizontal</button>
				<button class="btn-light-pill" onclick={cancelEdit} disabled={saving}>Cancel</button>
				<button class="btn-dark-pill" onclick={saveLine} disabled={saving}>
					{saving ? 'Saving…' : 'Save line'}
				</button>
			{:else if mode === 'zones'}
				<button class="btn-light-pill" onclick={cancelEdit} disabled={saving}>Cancel</button>
				<button class="btn-dark-pill" onclick={saveZones} disabled={saving}>
					{saving ? 'Saving…' : 'Save zones'}
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
				class:editing={mode !== 'view'}
				viewBox="0 0 {renderedSize.width || 1} {renderedSize.height || 1}"
				preserveAspectRatio="none"
				onpointermove={onCanvasPointerMove}
				onpointerup={onCanvasPointerUp}
				onpointercancel={onCanvasPointerUp}
				onclick={onCanvasClick}
				role={mode !== 'view' ? 'application' : 'presentation'}
			>
				<!-- Zones (read-only when not editing; interactive in zones mode) -->
				{#each polygonStrings as z}
					{#if z.points}
						<polygon
							points={z.points}
							fill={z.isSelected ? 'rgba(255, 214, 51, 0.18)' : 'rgba(40, 167, 69, 0.10)'}
							stroke={z.isSelected ? '#ffd633' : 'rgba(40, 167, 69, 0.85)'}
							stroke-width={z.isSelected ? 3 : 2}
							stroke-dasharray={z.isSelected ? '0' : '6 4'}
							style={mode === 'zones' ? 'cursor: move;' : ''}
							role={mode === 'zones' ? 'button' : 'presentation'}
							aria-label={z.name || 'zone'}
							onpointerdown={mode === 'zones' ? onZoneBodyPointerDown(z.idx) : null}
						/>
					{/if}
					{#if z.isSelected}
						{#each z.vertices as v, vi}
							<circle
								cx={v.x}
								cy={v.y}
								r="9"
								fill="#ffd633"
								stroke="#212529"
								stroke-width="2"
								style="cursor: grab; touch-action: none;"
								role="slider"
								tabindex="0"
								aria-label={`vertex ${vi + 1}`}
								aria-valuenow={Math.round(v.x)}
								onpointerdown={onVertexPointerDown(z.idx, vi)}
								ondblclick={() => removeVertex(z.idx, vi)}
							/>
						{/each}
					{/if}
					<!-- Zone label -->
					{#if z.points && z.vertices.length > 0}
						<text
							x={z.vertices[0].x + 8}
							y={z.vertices[0].y - 8}
							font-family="Inter, sans-serif"
							font-size="13"
							font-weight="600"
							fill={z.isSelected ? '#ffd633' : '#fff'}
							stroke="rgba(0,0,0,0.6)"
							stroke-width="3"
							paint-order="stroke"
							pointer-events="none"
						>{z.name}</text>
					{/if}
				{/each}

				<!-- Secondary lines -->
				{#if mode !== 'line'}
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
				{/if}

				<!-- Primary line -->
				<line
					x1={linePixels.x1}
					y1={linePixels.y1}
					x2={linePixels.x2}
					y2={linePixels.y2}
					stroke={mode === 'line' ? '#ffd633' : 'rgba(255,255,255,0.85)'}
					stroke-width={mode === 'line' ? 4 : 3}
					stroke-dasharray={mode === 'line' ? '8 4' : '0'}
				/>
				{#if mode === 'line'}
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
						onpointerdown={lineHandleDown(1)}
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
						onpointerdown={lineHandleDown(2)}
					/>
				{/if}
			</svg>
		{/if}
	</div>

	{#if mode === 'line'}
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

	{#if mode === 'zones'}
		<div class="zones-panel">
			<div class="zones-list">
				<div class="zones-list-head">
					<span>Zones</span>
					<button class="btn-light-pill btn-sm" onclick={addZone}>
						<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none">
							<path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
						</svg>
						Add zone
					</button>
				</div>
				{#if pendingZones.length === 0}
					<div class="zones-empty">No zones. Click <strong>Add zone</strong> to draw one.</div>
				{:else}
					<ul class="zone-rows">
						{#each pendingZones as z, idx (idx)}
							<li>
								<button
									type="button"
									class="zone-row"
									class:selected={idx === selectedZoneIdx}
									onclick={() => selectZone(idx)}
								>
									<span class="zone-color"></span>
									<span class="zone-row-name">{z.name || `Zone ${idx + 1}`}</span>
									<span class="zone-row-meta">{z.role} · {z.polygon.length} pts</span>
								</button>
							</li>
						{/each}
					</ul>
				{/if}
			</div>

			{#if selectedZone}
				<div class="zone-edit">
					<label class="zone-field">
						<span class="zone-field-label">Name</span>
						<input
							type="text"
							value={selectedZone.name}
							maxlength="80"
							oninput={(e) => setZoneName(selectedZoneIdx, e.currentTarget.value)}
						/>
					</label>
					<label class="zone-field">
						<span class="zone-field-label">Role</span>
						<select
							value={selectedZone.role}
							onchange={(e) => setZoneRole(selectedZoneIdx, e.currentTarget.value)}
						>
							{#each ROLE_OPTIONS as opt}
								<option value={opt.value}>{opt.label}</option>
							{/each}
						</select>
					</label>

					<div class="zone-controls">
						<button class="btn-light-pill btn-sm" onclick={() => (drawingMode = !drawingMode)}>
							{drawingMode ? 'Stop drawing' : 'Draw vertices'}
						</button>
						<button class="btn-light-pill btn-sm" onclick={presetRect}>Rectangle preset</button>
						<button class="btn-light-pill btn-sm" onclick={presetFullFrame}>Full-frame preset</button>
						<button class="btn-light-pill btn-sm" onclick={clearVertices}>Clear vertices</button>
						<button class="btn-light-pill btn-sm zone-danger" onclick={removeSelectedZone}>
							Delete zone
						</button>
					</div>

					<div class="zone-help">
						{#if drawingMode}
							<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none">
								<circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2"/>
								<path d="M12 7v5l3 2" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
							</svg>
							<span>Click on the preview to add vertices. Click near the first vertex (or press <strong>Stop drawing</strong>) to close the polygon.</span>
						{:else}
							<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none">
								<path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
							</svg>
							<span>Drag the yellow handles to refine. Drag inside the polygon to move it. Double-click a handle to remove that vertex.</span>
						{/if}
					</div>
				</div>
			{/if}
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
	opacity: 0.4;
	cursor: not-allowed;
}

.btn-sm {
	padding: 4px 10px;
	font-size: 12px;
	border-radius: 10px;
}

.zone-pill {
	display: inline-flex;
	align-items: center;
	margin-left: 4px;
	background: rgb(33, 37, 41);
	color: #fff;
	font-size: 11px;
	border-radius: 999px;
	padding: 0 8px;
	min-width: 18px;
	height: 18px;
	justify-content: center;
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
	to { transform: rotate(360deg); }
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

/* ----- zones panel ----- */
.zones-panel {
	display: grid;
	grid-template-columns: minmax(0, 1fr) minmax(0, 1.2fr);
	gap: 12px;
	background: #f8f9fa;
	border: 2px solid rgb(215, 215, 215);
	border-radius: 12px;
	padding: 12px;
}

@media (max-width: 720px) {
	.zones-panel {
		grid-template-columns: 1fr;
	}
}

.zones-list {
	display: flex;
	flex-direction: column;
	gap: 8px;
	min-width: 0;
}

.zones-list-head {
	display: flex;
	align-items: center;
	justify-content: space-between;
	font-weight: 600;
	font-size: 13px;
	color: #495057;
}

.zones-empty {
	border: 2px dashed rgb(215, 215, 215);
	border-radius: 10px;
	padding: 12px;
	font-size: 13px;
	color: #6c757d;
	text-align: center;
}

.zone-rows {
	list-style: none;
	margin: 0;
	padding: 0;
	display: flex;
	flex-direction: column;
	gap: 4px;
}

.zone-row {
	display: flex;
	align-items: center;
	gap: 8px;
	width: 100%;
	border: 2px solid rgb(215, 215, 215);
	background: #fff;
	border-radius: 10px;
	padding: 6px 10px;
	font-size: 13px;
	font-family: inherit;
	cursor: pointer;
	color: #212529;
}

.zone-row.selected {
	border-color: rgb(33, 37, 41);
	background: #fff8d4;
}

.zone-color {
	width: 12px;
	height: 12px;
	border-radius: 999px;
	background: #28a745;
	flex-shrink: 0;
}

.zone-row.selected .zone-color {
	background: #ffd633;
}

.zone-row-name {
	flex: 1;
	font-weight: 500;
	overflow: hidden;
	text-overflow: ellipsis;
	white-space: nowrap;
	text-align: left;
}

.zone-row-meta {
	font-size: 11px;
	color: #6c757d;
	font-variant-numeric: tabular-nums;
}

.zone-edit {
	display: flex;
	flex-direction: column;
	gap: 8px;
	min-width: 0;
}

.zone-field {
	display: flex;
	flex-direction: column;
	gap: 4px;
}

.zone-field-label {
	font-weight: 600;
	font-size: 12px;
	color: #495057;
}

.zone-field input,
.zone-field select {
	border: 2px solid rgb(215, 215, 215);
	background: #fff;
	color: rgb(33, 37, 41);
	border-radius: 10px;
	padding: 6px 10px;
	font-size: 13px;
	font-family: inherit;
}

.zone-field input:focus,
.zone-field select:focus {
	outline: none;
	border-color: rgb(33, 37, 41);
}

.zone-controls {
	display: flex;
	flex-wrap: wrap;
	gap: 6px;
}

.zone-danger {
	color: #b02a37;
	border-color: #f5c2c7;
}

.zone-help {
	display: flex;
	align-items: flex-start;
	gap: 6px;
	font-size: 12px;
	color: #495057;
	background: #fff8d4;
	border-radius: 10px;
	padding: 8px 10px;
}
</style>
