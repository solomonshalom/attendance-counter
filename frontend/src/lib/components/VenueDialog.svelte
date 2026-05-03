<script>
// @ts-nocheck
import { onMount } from 'svelte';

let { open = false, venue = null, onclose = () => {}, onsubmit = async () => {} } = $props();

let name = $state('');
let widthM = $state(20);
let heightM = $state(20);
let dedupWindow = $state(3.0);
let dedupRadius = $state(1.0);
let busy = $state(false);
let error = $state(null);
let nameInput = $state(null);

const isEdit = $derived(!!venue);

$effect(() => {
	if (open) {
		name = venue?.name ?? '';
		widthM = venue?.floor_plan_w_m ?? 20;
		heightM = venue?.floor_plan_h_m ?? 20;
		dedupWindow = venue?.dedup_window_s ?? 3.0;
		dedupRadius = venue?.dedup_radius_m ?? 1.0;
		busy = false;
		error = null;
		queueMicrotask(() => nameInput?.focus());
	}
});

async function submit(e) {
	e?.preventDefault?.();
	if (busy) return;
	const trimmedName = name.trim();
	if (!trimmedName) { error = 'Name is required.'; return; }
	if (widthM <= 0 || heightM <= 0) { error = 'Dimensions must be positive.'; return; }
	if (dedupWindow < 0.1) { error = 'Dedup window must be at least 0.1 s.'; return; }
	if (dedupRadius < 0.1) { error = 'Dedup radius must be at least 0.1 m.'; return; }

	busy = true;
	error = null;
	try {
		await onsubmit({
			name: trimmedName,
			floor_plan_w_m: Number(widthM),
			floor_plan_h_m: Number(heightM),
			dedup_window_s: Number(dedupWindow),
			dedup_radius_m: Number(dedupRadius)
		});
		onclose();
	} catch (err) {
		error = err?.message ?? String(err);
	} finally {
		busy = false;
	}
}

function onKey(e) {
	if (open && e.key === 'Escape' && !busy) {
		e.preventDefault();
		onclose();
	}
}
</script>

<svelte:window onkeydown={onKey} />

{#if open}
	<div
		class="backdrop"
		role="presentation"
		onclick={(e) => { if (e.target === e.currentTarget && !busy) onclose(); }}
	>
		<div role="dialog" aria-modal="true" aria-labelledby="venue-title">
			<form class="dialog" onsubmit={submit}>
				<div class="dialog-head">
					<h2 id="venue-title">{isEdit ? 'Edit venue' : 'Create venue'}</h2>
					<button type="button" class="close-btn" aria-label="Close" onclick={onclose} disabled={busy}>
						<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none">
							<path d="M18 6L6 18M6 6l12 12" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
						</svg>
					</button>
				</div>

				<label class="field">
					<span class="field-label">Name</span>
					<input
						bind:this={nameInput}
						bind:value={name}
						maxlength="80"
						placeholder="e.g. Main sanctuary"
						disabled={busy}
						required
					/>
				</label>

				<div class="grid-2">
					<label class="field">
						<span class="field-label">Floor plan width (m)</span>
						<input type="number" min="1" max="500" step="0.5" bind:value={widthM} disabled={busy} />
					</label>
					<label class="field">
						<span class="field-label">Floor plan height (m)</span>
						<input type="number" min="1" max="500" step="0.5" bind:value={heightM} disabled={busy} />
					</label>
				</div>

				<div class="dedup-card">
					<div class="dedup-head">Cross-camera dedupe</div>
					<div class="dedup-help">
						When two cameras in this venue see the same person crossing within a
						short time + space window, the second event is suppressed from the
						live count. Tighter windows = stricter; loosen if you have wide-angle
						cameras with overlapping coverage.
					</div>
					<div class="grid-2">
						<label class="field">
							<span class="field-label">Time window (seconds)</span>
							<input type="number" min="0.1" max="30" step="0.1" bind:value={dedupWindow} disabled={busy} />
						</label>
						<label class="field">
							<span class="field-label">Radius (meters)</span>
							<input type="number" min="0.1" max="10" step="0.1" bind:value={dedupRadius} disabled={busy} />
						</label>
					</div>
				</div>

				{#if error}
					<div class="dialog-error">{error}</div>
				{/if}

				<div class="dialog-actions">
					<button type="button" class="btn-light-pill" onclick={onclose} disabled={busy}>Cancel</button>
					<button type="submit" class="btn-dark-pill" disabled={busy}>
						{busy ? 'Saving…' : (isEdit ? 'Save changes' : 'Create venue')}
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

.grid-2 {
	display: grid;
	grid-template-columns: 1fr 1fr;
	gap: 10px;
}

.dedup-card {
	background: #f8f9fa;
	border: 2px solid rgb(215, 215, 215);
	border-radius: 12px;
	padding: 12px;
	display: flex;
	flex-direction: column;
	gap: 8px;
}

.dedup-head {
	font-weight: 600;
	font-size: 13px;
	color: #212529;
}

.dedup-help {
	font-size: 12px;
	color: #6c757d;
	line-height: 1.4;
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

.btn-light-pill,
.btn-dark-pill {
	border: 2px solid rgb(33, 37, 41);
	border-radius: 12px;
	padding: 6px 16px;
	font-size: 14px;
	font-weight: 500;
	cursor: pointer;
	font-family: inherit;
}

.btn-light-pill { background: #fff; color: rgb(33, 37, 41); }
.btn-dark-pill { background: rgb(33, 37, 41); color: #fff; }

.btn-light-pill:disabled,
.btn-dark-pill:disabled { opacity: 0.45; cursor: not-allowed; }
</style>
