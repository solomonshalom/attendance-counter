<script>
// @ts-nocheck
import { api } from '../api.js';

let { camera } = $props();

let open = $state(false);
let saving = $state(false);
let error = $state(null);

let confidence = $state('');
let iou = $state('');
let imgsz = $state('');
let tracker = $state('');

// Sync local state with the latest camera props whenever the panel is closed,
// so reopening shows the current backend values (and external updates don't
// silently overwrite an in-progress edit).
$effect(() => {
	if (!open) {
		confidence = camera?.confidence ?? '';
		iou = camera?.iou ?? '';
		imgsz = camera?.imgsz ?? '';
		tracker = camera?.tracker ?? '';
	}
});

async function save() {
	saving = true;
	error = null;
	const payload = {};
	if (confidence !== '' && confidence !== null) payload.confidence = Number(confidence);
	if (iou !== '' && iou !== null) payload.iou = Number(iou);
	if (imgsz !== '' && imgsz !== null) payload.imgsz = Number(imgsz);
	if (tracker !== '' && tracker !== null) payload.tracker = String(tracker);

	try {
		await api.updateCamera(camera.camera_id, payload);
		open = false;
	} catch (err) {
		error = err?.message ?? String(err);
	} finally {
		saving = false;
	}
}

async function reset() {
	saving = true;
	error = null;
	try {
		await api.updateCamera(camera.camera_id, {
			confidence: null,
			iou: null,
			imgsz: null,
			tracker: null
		});
		confidence = '';
		iou = '';
		imgsz = '';
		tracker = '';
		open = false;
	} catch (err) {
		error = err?.message ?? String(err);
	} finally {
		saving = false;
	}
}
</script>

<div class="settings-wrap">
	<button
		type="button"
		class="settings-toggle"
		onclick={() => (open = !open)}
		aria-expanded={open}
	>
		<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none">
			<path d="M12 15a3 3 0 100-6 3 3 0 000 6z" stroke="currentColor" stroke-width="2"/>
			<path d="M19.4 15a1.7 1.7 0 00.34 1.87l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.7 1.7 0 00-1.87-.34 1.7 1.7 0 00-1.05 1.56V21a2 2 0 11-4 0v-.09A1.7 1.7 0 009 19.4a1.7 1.7 0 00-1.87.34l-.06.06a2 2 0 11-2.83-2.83l.06-.06A1.7 1.7 0 004.6 15a1.7 1.7 0 00-1.56-1.05H3a2 2 0 110-4h.09A1.7 1.7 0 004.6 9a1.7 1.7 0 00-.34-1.87l-.06-.06a2 2 0 112.83-2.83l.06.06A1.7 1.7 0 009 4.6a1.7 1.7 0 001.05-1.56V3a2 2 0 114 0v.09a1.7 1.7 0 001.05 1.56 1.7 1.7 0 001.87-.34l.06-.06a2 2 0 112.83 2.83l-.06.06A1.7 1.7 0 0019.4 9a1.7 1.7 0 001.56 1.05H21a2 2 0 110 4h-.09a1.7 1.7 0 00-1.51 1z" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>
		</svg>
		Tuning
		<svg
			xmlns="http://www.w3.org/2000/svg"
			width="12"
			height="12"
			viewBox="0 0 24 24"
			fill="none"
			class="chev"
			class:open
		>
			<path d="M6 9l6 6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
		</svg>
	</button>

	{#if open}
		<div class="settings-panel">
			<div class="settings-grid">
				<label class="settings-row">
					<span class="settings-label">Confidence</span>
					<input
						type="number"
						min="0.05"
						max="0.95"
						step="0.05"
						placeholder="0.35"
						bind:value={confidence}
						disabled={saving}
					/>
					<span class="settings-help">Higher rejects weaker detections; default 0.35.</span>
				</label>

				<label class="settings-row">
					<span class="settings-label">IoU (NMS)</span>
					<input
						type="number"
						min="0.1"
						max="0.95"
						step="0.05"
						placeholder="0.5"
						bind:value={iou}
						disabled={saving}
					/>
					<span class="settings-help">Lower allows tighter overlapping boxes in crowds.</span>
				</label>

				<label class="settings-row">
					<span class="settings-label">Image size</span>
					<input
						type="number"
						min="320"
						max="1920"
						step="32"
						placeholder="640"
						bind:value={imgsz}
						disabled={saving}
					/>
					<span class="settings-help">
						Larger detects smaller people but is slower. 640 is a good baseline; 960
						helps in big rooms.
					</span>
				</label>

				<label class="settings-row">
					<span class="settings-label">Tracker</span>
					<select bind:value={tracker} disabled={saving}>
						<option value="">Default ({'botsort_reid'})</option>
						<option value="botsort_reid">BoT-SORT + ReID (accurate)</option>
						<option value="bytetrack_fast">ByteTrack (fast)</option>
					</select>
					<span class="settings-help">
						BoT-SORT keeps identities through brief occlusions. Switch to ByteTrack
						if FPS is too low.
					</span>
				</label>
			</div>

			{#if error}
				<div class="settings-error">{error}</div>
			{/if}

			<div class="settings-actions">
				<button type="button" class="btn-light-pill" onclick={reset} disabled={saving}>
					Reset to defaults
				</button>
				<button type="button" class="btn-dark-pill" onclick={save} disabled={saving}>
					{saving ? 'Applying…' : 'Apply'}
				</button>
			</div>
			<div class="settings-note">
				Changes that affect inference (image size, tracker) restart the capture briefly.
			</div>
		</div>
	{/if}
</div>

<style>
.settings-wrap {
	display: contents;
}

.settings-toggle {
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

.settings-toggle:hover {
	background: #f8f9fa;
}

.chev {
	transition: transform 0.15s ease;
}

.chev.open {
	transform: rotate(180deg);
}

.settings-panel {
	margin-top: 10px;
	background: #f8f9fa;
	border: 2px solid rgb(215, 215, 215);
	border-radius: 12px;
	padding: 14px;
	display: flex;
	flex-direction: column;
	gap: 12px;
}

.settings-grid {
	display: grid;
	grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
	gap: 12px;
}

.settings-row {
	display: flex;
	flex-direction: column;
	gap: 4px;
}

.settings-label {
	font-weight: 600;
	font-size: 12px;
	color: #495057;
}

.settings-row input,
.settings-row select {
	border: 2px solid rgb(215, 215, 215);
	border-radius: 10px;
	padding: 6px 10px;
	font-size: 13px;
	font-family: inherit;
	background: #fff;
	color: rgb(33, 37, 41);
}

.settings-row input:focus,
.settings-row select:focus {
	outline: none;
	border-color: rgb(33, 37, 41);
}

.settings-help {
	font-size: 11px;
	color: #6c757d;
}

.settings-error {
	background: #fdeaec;
	color: #842029;
	border: 1px solid #f5c2c7;
	border-radius: 8px;
	padding: 6px 10px;
	font-size: 13px;
}

.settings-actions {
	display: flex;
	justify-content: flex-end;
	gap: 8px;
}

.settings-note {
	font-size: 12px;
	color: #6c757d;
	font-style: italic;
}

.btn-light-pill,
.btn-dark-pill {
	border: 2px solid rgb(33, 37, 41);
	border-radius: 12px;
	padding: 6px 14px;
	font-size: 13px;
	font-weight: 500;
	cursor: pointer;
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
	opacity: 0.45;
	cursor: not-allowed;
}
</style>
