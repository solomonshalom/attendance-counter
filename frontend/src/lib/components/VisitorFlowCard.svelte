<script>
// @ts-nocheck
/**
 * Visitor analytics for a venue, powered by P9's persistent identity layer.
 *
 * Shows total people seen, first-timers vs returning, opted-out count, and
 * exposes admin actions: purge-expired (manual retention sweep) and
 * purge-venue (emergency wipe with double-confirmation).
 *
 * Polls /api/venues/{id}/people/stats on mount + every 30 s while visible.
 */

import { onMount, onDestroy } from 'svelte';
import { api } from '../api.js';
import { formatTime } from '../format.js';

let { venue } = $props();

let stats = $state(null);
let loading = $state(false);
let error = $state(null);
let lastFetch = $state(0);
let purgeBusy = $state(false);

let pollHandle = null;

async function refresh() {
	if (!venue?.id) return;
	loading = true;
	try {
		stats = await api.getVenuePeopleStats(venue.id);
		error = null;
		lastFetch = Date.now();
	} catch (err) {
		error = err?.message ?? String(err);
	} finally {
		loading = false;
	}
}

onMount(() => {
	refresh();
	pollHandle = setInterval(refresh, 30_000);
});

onDestroy(() => {
	if (pollHandle) clearInterval(pollHandle);
});

async function runPurgeExpired() {
	purgeBusy = true;
	error = null;
	try {
		const res = await api.purgeExpiredFaces();
		await refresh();
		error = `Purged ${res.people_purged} expired records.`;
	} catch (err) {
		error = err?.message ?? String(err);
	} finally {
		purgeBusy = false;
	}
}

async function runPurgeAll() {
	if (!venue?.id) return;
	const confirm1 = window.confirm(
		`Permanently delete ALL face data for "${venue.name}"?\n\nThis cannot be undone.`
	);
	if (!confirm1) return;
	const confirm2 = window.prompt(
		'Type the venue name to confirm deletion:'
	);
	if (confirm2 !== venue.name) {
		error = 'Confirmation did not match. Cancelled.';
		return;
	}
	purgeBusy = true;
	error = null;
	try {
		const res = await api.purgeVenueFaceData(venue.id);
		await refresh();
		error = `Purged ${res.people_purged} records.`;
	} catch (err) {
		error = err?.message ?? String(err);
	} finally {
		purgeBusy = false;
	}
}
</script>

<section class="visitor-flow">
	<header>
		<h3>Visitor flow</h3>
		<button class="link" onclick={refresh} disabled={loading}>
			{loading ? 'Refreshing…' : 'Refresh'}
		</button>
	</header>

	{#if !stats}
		<p class="muted">{error ?? 'Loading…'}</p>
	{:else}
		<dl class="grid">
			<div class="stat">
				<dt>Total people</dt>
				<dd>{stats.total_people}</dd>
			</div>
			<div class="stat">
				<dt>First-timers</dt>
				<dd>{stats.first_timers}</dd>
			</div>
			<div class="stat">
				<dt>Returning</dt>
				<dd>{stats.returning}</dd>
			</div>
			<div class="stat">
				<dt>Opted out</dt>
				<dd>{stats.opted_out}</dd>
			</div>
		</dl>

		<p class="muted last-activity">
			{stats.last_activity > 0
				? `Last activity: ${formatTime(stats.last_activity)}`
				: 'No activity yet.'}
		</p>

		<details class="admin">
			<summary>Privacy controls</summary>
			<div class="admin-actions">
				<button onclick={runPurgeExpired} disabled={purgeBusy}>
					{purgeBusy ? 'Working…' : 'Purge expired'}
				</button>
				<button class="danger" onclick={runPurgeAll} disabled={purgeBusy}>
					Wipe all face data for this venue
				</button>
			</div>
			<p class="muted small">
				Embeddings are AES-256-GCM encrypted at rest under a per-venue master
				key. Default retention 30 days; expired rows are securely overwritten
				before deletion. Wipe-all also overwrites the storage blocks and
				removes the venue's do-not-store list.
			</p>
		</details>
	{/if}

	{#if error}
		<p class="error">{error}</p>
	{/if}
</section>

<style>
	.visitor-flow {
		display: flex;
		flex-direction: column;
		gap: 0.6rem;
		padding: 1rem;
		border: 1px solid var(--color-border, rgba(0, 0, 0, 0.08));
		border-radius: 0.5rem;
		background: var(--color-surface, white);
	}

	header {
		display: flex;
		justify-content: space-between;
		align-items: center;
	}
	h3 {
		margin: 0;
		font-size: 1rem;
	}
	.link {
		background: none;
		border: none;
		color: var(--color-link, #2563eb);
		cursor: pointer;
		font-size: 0.85rem;
		padding: 0;
	}
	.link:disabled {
		opacity: 0.5;
		cursor: default;
	}

	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(7rem, 1fr));
		gap: 0.75rem;
		margin: 0;
	}
	.stat {
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
	}
	.stat dt {
		font-size: 0.75rem;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--color-text-muted, #6b7280);
	}
	.stat dd {
		font-size: 1.5rem;
		font-weight: 600;
		font-variant-numeric: tabular-nums;
		margin: 0;
	}

	.muted {
		color: var(--color-text-muted, #6b7280);
		font-size: 0.85rem;
		margin: 0;
	}
	.small {
		font-size: 0.78rem;
	}
	.last-activity {
		font-size: 0.78rem;
	}

	.admin {
		border-top: 1px solid var(--color-border, rgba(0, 0, 0, 0.08));
		padding-top: 0.6rem;
	}
	.admin summary {
		cursor: pointer;
		font-size: 0.85rem;
		font-weight: 500;
	}
	.admin-actions {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		margin: 0.6rem 0;
	}
	.admin button {
		padding: 0.4rem 0.75rem;
		border-radius: 0.375rem;
		border: 1px solid var(--color-border, rgba(0, 0, 0, 0.15));
		background: var(--color-surface, white);
		cursor: pointer;
		font-size: 0.85rem;
	}
	.admin button.danger {
		border-color: #fca5a5;
		color: #b91c1c;
	}
	.admin button:disabled {
		opacity: 0.5;
		cursor: default;
	}
	.error {
		margin: 0;
		padding: 0.5rem 0.75rem;
		border-radius: 0.375rem;
		background: #fee2e2;
		color: #991b1b;
		font-size: 0.85rem;
	}
</style>
