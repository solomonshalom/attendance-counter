<script>
// @ts-nocheck
import { onMount } from 'svelte';
import { api } from '../api.js';
import { venues } from '../stores.js';
import VenueDialog from './VenueDialog.svelte';
import VisitorFlowCard from './VisitorFlowCard.svelte';

let { cameras = [] } = $props();

let dialogOpen = $state(false);
let editingVenue = $state(null);
let actionError = $state(null);
let busy = $state(false);

onMount(refresh);

async function refresh() {
	try {
		const list = await api.listVenues();
		venues.set(list);
	} catch (err) {
		console.warn('venues fetch failed', err);
	}
}

async function submitVenue(payload) {
	if (editingVenue) {
		await api.updateVenue(editingVenue.id, payload);
	} else {
		await api.createVenue(payload);
	}
	await refresh();
}

function openCreate() {
	editingVenue = null;
	dialogOpen = true;
}

function openEdit(v) {
	editingVenue = v;
	dialogOpen = true;
}

async function deleteVenue(v) {
	if (!confirm(`Delete venue "${v.name}"? Cameras assigned to it will be detached.`)) return;
	busy = true;
	actionError = null;
	try {
		await api.deleteVenue(v.id);
		await refresh();
	} catch (err) {
		actionError = err?.message ?? String(err);
	} finally {
		busy = false;
	}
}

function camerasInVenue(venueId) {
	return cameras.filter((c) => c.venue_id === venueId);
}

function calibratedCount(venueId) {
	return camerasInVenue(venueId).filter((c) => c.calibrated).length;
}
</script>

{#if $venues.length > 0 || cameras.length >= 2}
	<div class="venues-card">
		<div class="venues-head">
			<div class="venues-head-text">
				<div class="venues-title">
					<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none">
						<path d="M3 12l9-9 9 9M5 10v10h14V10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
					</svg>
					Venues
					<span class="venue-count">{$venues.length}</span>
				</div>
				<div class="venues-sub">
					Group cameras that watch the same room. Calibrate each one to a shared
					floor plan and we'll dedupe people seen by overlapping cameras using
					their world position.
				</div>
			</div>
			<div class="venues-head-actions">
				<button class="btn-primary-pill" onclick={openCreate}>
					<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none">
						<path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
					</svg>
					New venue
				</button>
			</div>
		</div>

		{#if actionError}
			<div class="venues-error">{actionError}</div>
		{/if}

		{#if $venues.length === 0}
			<div class="venues-empty">
				No venues yet. Create one to start tying cameras together for cross-camera dedupe.
			</div>
		{:else}
			<ul class="venue-list">
				{#each $venues as v (v.id)}
					{@const cams = camerasInVenue(v.id)}
					{@const calCount = calibratedCount(v.id)}
					<li class="venue-row">
						<div class="venue-row-main">
							<div class="venue-info">
								<div class="venue-name-row">
									<span class="venue-name">{v.name}</span>
									<span class="venue-pill">{v.floor_plan_w_m} × {v.floor_plan_h_m} m</span>
								</div>
								<div class="venue-meta">
									{cams.length} camera{cams.length === 1 ? '' : 's'}
									· {calCount}/{cams.length} calibrated
									· dedupe ≤ {v.dedup_window_s}s, {v.dedup_radius_m}m
								</div>
							</div>
							<div class="venue-actions">
								<button class="btn-light-pill btn-sm" onclick={() => openEdit(v)} disabled={busy}>
									<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none">
										<path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7M18.5 2.5a2.121 2.121 0 113 3L12 15l-4 1 1-4 9.5-9.5z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
									</svg>
									Edit
								</button>
								<button class="icon-btn icon-btn-danger" title="Delete venue" onclick={() => deleteVenue(v)} disabled={busy} aria-label="Delete venue">
									<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none">
										<path d="M19 7L18.13 19.14C18.06 20.19 17.19 21 16.14 21H7.86C6.81 21 5.94 20.19 5.87 19.14L5 7M10 11v6M14 11v6M15 7V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
									</svg>
								</button>
							</div>
						</div>
						<div class="venue-row-flow">
							<VisitorFlowCard venue={v} />
						</div>
					</li>
				{/each}
			</ul>
		{/if}
	</div>

	<VenueDialog
		open={dialogOpen}
		venue={editingVenue}
		onclose={() => { dialogOpen = false; editingVenue = null; }}
		onsubmit={submitVenue}
	/>
{/if}

<style>
.venues-card {
	background: #fff;
	border: 2px solid rgb(215, 215, 215);
	border-radius: 16px;
	padding: 16px 20px;
	margin-bottom: 16px;
	display: flex;
	flex-direction: column;
	gap: 12px;
}

.venues-head {
	display: flex;
	justify-content: space-between;
	align-items: flex-start;
	gap: 16px;
	flex-wrap: wrap;
}

.venues-head-text {
	flex: 1 1 280px;
	display: flex;
	flex-direction: column;
	gap: 4px;
}

.venues-title {
	display: flex;
	align-items: center;
	gap: 8px;
	font-weight: 600;
	font-size: 16px;
	color: rgb(33, 37, 41);
}

.venue-count {
	display: inline-flex;
	align-items: center;
	font-size: 12px;
	background: rgb(33, 37, 41);
	color: #fff;
	border-radius: 999px;
	padding: 1px 8px;
	font-variant-numeric: tabular-nums;
}

.venues-sub {
	font-size: 13px;
	color: #6c757d;
	line-height: 1.4;
}

.venues-head-actions {
	display: flex;
	align-items: center;
	gap: 8px;
}

.venues-empty {
	border: 2px dashed rgb(215, 215, 215);
	border-radius: 12px;
	padding: 18px;
	text-align: center;
	color: #6c757d;
	font-size: 14px;
}

.venues-error {
	background: #fdeaec;
	color: #842029;
	border: 1px solid #f5c2c7;
	border-radius: 10px;
	padding: 8px 12px;
	font-size: 14px;
}

.venue-list {
	list-style: none;
	margin: 0;
	padding: 0;
	display: flex;
	flex-direction: column;
	gap: 8px;
}

.venue-row {
	display: flex;
	flex-direction: column;
	gap: 12px;
	padding: 12px;
	border: 2px solid #f1f3f5;
	border-radius: 12px;
}

.venue-row-main {
	display: flex;
	justify-content: space-between;
	align-items: flex-start;
	gap: 12px;
	flex-wrap: wrap;
}

.venue-row-flow {
	border-top: 1px dashed #e9ecef;
	padding-top: 12px;
}

.venue-info {
	flex: 1 1 220px;
	display: flex;
	flex-direction: column;
	gap: 4px;
}

.venue-name-row {
	display: flex;
	align-items: center;
	gap: 8px;
	flex-wrap: wrap;
}

.venue-name {
	font-weight: 600;
	color: rgb(33, 37, 41);
}

.venue-pill {
	font-size: 11px;
	background: #f1f3f5;
	color: #495057;
	border-radius: 999px;
	padding: 2px 10px;
	font-family: 'JetBrains Mono', ui-monospace, monospace;
}

.venue-meta {
	font-size: 13px;
	color: #6c757d;
}

.venue-actions {
	display: flex;
	align-items: center;
	gap: 6px;
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

.btn-primary-pill { background: rgb(33, 37, 41); color: #fff; }
.btn-light-pill { background: #fff; color: rgb(33, 37, 41); }

.btn-sm { padding: 4px 10px; font-size: 12px; border-radius: 10px; }

.btn-primary-pill:disabled,
.btn-light-pill:disabled { opacity: 0.45; cursor: not-allowed; }

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
</style>
