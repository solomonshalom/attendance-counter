<script>
// @ts-nocheck
import { onMount } from 'svelte';

let { cameras = [] } = $props();

// Only sessions with a session_id contribute to the live aggregate. A camera
// without an active session may still hold the final counts of its last
// session; including them would double-count past attendance.
const activeCams = $derived(cameras.filter((c) => c?.session_id));

const aggregate = $derived.by(() => {
	const inSum = activeCams.reduce((s, c) => s + (c.in_count || 0), 0);
	const outSum = activeCams.reduce((s, c) => s + (c.out_count || 0), 0);
	return {
		in_count: inSum,
		out_count: outSum,
		inside: Math.max(0, inSum - outSum)
	};
});

let peakInside = $state(0);
let lastFingerprint = '';

$effect(() => {
	// When the set of active sessions changes (a session starts/ends), reset peak
	// to current inside — peak should reflect this combination of sessions only.
	const fingerprint = activeCams
		.map((c) => c.session_id)
		.sort()
		.join('|');
	if (fingerprint !== lastFingerprint) {
		lastFingerprint = fingerprint;
		peakInside = aggregate.inside;
	} else if (aggregate.inside > peakInside) {
		peakInside = aggregate.inside;
	}
});

const sessionCount = $derived(activeCams.length);
</script>

<section class="combined-card">
	<header class="combined-head">
		<div class="combined-title-row">
			<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none">
				<path d="M3 12h4l3-9 4 18 3-9h4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
			</svg>
			<h2 class="combined-title">Combined attendance</h2>
			<span class="combined-pill">All cameras</span>
		</div>
		{#if sessionCount === 0}
			<div class="combined-sub">No active sessions. Start a session on at least one camera to see live combined totals.</div>
		{:else}
			<div class="combined-sub">
				Aggregating {sessionCount} active session{sessionCount === 1 ? '' : 's'}
				{#if cameras.length > sessionCount}
					· {cameras.length - sessionCount} camera{cameras.length - sessionCount === 1 ? '' : 's'} idle
				{/if}
			</div>
		{/if}
	</header>

	<div class="combined-grid">
		<div class="count-card count-card-in">
			<div class="count-label">Total entered</div>
			<div class="count-value">{aggregate.in_count}</div>
			<div class="count-meta">
				<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none">
					<path d="M5 12h14M13 6l6 6-6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
				</svg>
				summed across cameras
			</div>
		</div>
		<div class="count-card count-card-out">
			<div class="count-label">Total left</div>
			<div class="count-value">{aggregate.out_count}</div>
			<div class="count-meta">
				<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none">
					<path d="M19 12H5M11 18l-6-6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
				</svg>
				summed across cameras
			</div>
		</div>
		<div class="count-card count-card-inside">
			<div class="count-label">Inside now</div>
			<div class="count-value">{aggregate.inside}</div>
			<div class="count-meta">total in − total out</div>
		</div>
		<div class="count-card count-card-peak">
			<div class="count-label">Peak combined</div>
			<div class="count-value">{peakInside}</div>
			<div class="count-meta">since current sessions started</div>
		</div>
	</div>
</section>

<style>
.combined-card {
	background: #fff;
	border: 2px solid rgb(33, 37, 41);
	border-radius: 16px;
	padding: 16px;
	margin-bottom: 16px;
	display: flex;
	flex-direction: column;
	gap: 12px;
	box-shadow: 0 1px 0 rgba(0, 0, 0, 0.04);
}

.combined-head {
	display: flex;
	flex-direction: column;
	gap: 4px;
}

.combined-title-row {
	display: flex;
	align-items: center;
	gap: 8px;
	color: rgb(33, 37, 41);
}

.combined-title {
	font-family: 'Inter Tight', sans-serif;
	font-size: 18px;
	font-weight: 600;
	margin: 0;
}

.combined-pill {
	font-size: 11px;
	background: rgb(33, 37, 41);
	color: #fff;
	border-radius: 999px;
	padding: 2px 10px;
	letter-spacing: 0.04em;
	text-transform: uppercase;
	font-weight: 600;
}

.combined-sub {
	font-size: 13px;
	color: #6c757d;
}

.combined-grid {
	display: grid;
	grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
	gap: 16px;
}

.count-card {
	background: #fff;
	border: 2px solid rgb(215, 215, 215);
	border-radius: 16px;
	padding: 20px;
}

.count-card-in {
	background: #f0f8ff;
	border-color: #cfe2ff;
}

.count-card-out {
	background: #fff5f5;
	border-color: #f5c2c7;
}

.count-card-inside {
	background: #fff8d4;
	border-color: #ffe69c;
}

.count-card-peak {
	background: rgb(33, 37, 41);
	color: #fff;
	border-color: rgb(33, 37, 41);
}

.count-label {
	font-size: 14px;
	font-weight: 500;
	opacity: 0.75;
	margin-bottom: 8px;
}

.count-value {
	font-family: 'Inter Tight', sans-serif;
	font-weight: 700;
	font-size: 56px;
	line-height: 1;
	letter-spacing: -0.02em;
	margin-bottom: 8px;
	font-variant-numeric: tabular-nums;
}

.count-meta {
	font-size: 13px;
	opacity: 0.7;
	display: flex;
	align-items: center;
	gap: 6px;
}
</style>
