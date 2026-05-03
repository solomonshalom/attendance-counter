<script>
// @ts-nocheck
/**
 * Compact per-camera health pill: green / amber / red dot + label
 * driven by status fields from the WebSocket camera state.
 *
 * Reads:
 *   - status.running, status.model_loaded, status.camera_open
 *   - status.fps, status.last_frame_at, status.last_error
 *   - status.inference_latency_p50_ms / _p95_ms (set by P7 metrics)
 *
 * The traffic-light decision is intentionally simple: any blocker → red,
 * any warning → amber, otherwise green. Keeps operator scanning fast.
 */

const TARGET_FPS_MIN = 5; // below this we show "low fps" amber

let { camera } = $props();

let healthState = $derived.by(() => {
	if (!camera?.enabled) return { color: 'muted', label: 'Disabled' };
	const s = camera?.status ?? {};
	if (!s.model_loaded) return { color: 'red', label: 'Model loading' };
	if (!s.camera_open) return { color: 'red', label: 'Camera offline' };
	if (s.last_error) return { color: 'amber', label: 'Recent error' };
	if (typeof s.fps === 'number' && s.fps < TARGET_FPS_MIN)
		return { color: 'amber', label: `${s.fps.toFixed(1)} fps low` };
	if (s.last_frame_at) {
		const age = Date.now() / 1000 - s.last_frame_at;
		if (age > 30) return { color: 'red', label: `No frame ${age.toFixed(0)}s` };
		if (age > 10) return { color: 'amber', label: 'Stale frame' };
	}
	return { color: 'green', label: `${(s.fps || 0).toFixed(1)} fps` };
});

let tooltipText = $derived.by(() => {
	const s = camera?.status ?? {};
	const parts = [];
	if (s.device) parts.push(`device: ${s.device}`);
	if (s.model_name) parts.push(`model: ${s.model_name}`);
	if (s.tracker) parts.push(`tracker: ${s.tracker}`);
	if (s.frame_width) parts.push(`frame: ${s.frame_width}×${s.frame_height}`);
	if (s.last_error) parts.push(`error: ${s.last_error}`);
	return parts.join(' · ');
});
</script>

<div class="health" data-color={healthState.color} title={tooltipText}>
	<span class="dot" aria-hidden="true"></span>
	<span class="label">{healthState.label}</span>
	{#if camera?.status?.inference_latency_p50_ms}
		<span class="latency" title="inference latency p50 / p95">
			{Math.round(camera.status.inference_latency_p50_ms)}ms
			<span class="lat-p95">/ {Math.round(camera.status.inference_latency_p95_ms || 0)}ms</span>
		</span>
	{/if}
</div>

<style>
	.health {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		padding: 0.18rem 0.5rem;
		border-radius: 999px;
		font-size: 0.8rem;
		font-weight: 500;
		background: var(--color-surface, rgba(0, 0, 0, 0.04));
		color: var(--color-text-muted, #555);
		white-space: nowrap;
	}
	.dot {
		width: 0.5rem;
		height: 0.5rem;
		border-radius: 50%;
		flex-shrink: 0;
	}
	[data-color='green'] .dot {
		background: #22c55e;
	}
	[data-color='amber'] .dot {
		background: #f59e0b;
	}
	[data-color='red'] .dot {
		background: #ef4444;
	}
	[data-color='muted'] .dot {
		background: #9ca3af;
	}
	[data-color='green'] {
		color: #15803d;
	}
	[data-color='amber'] {
		color: #b45309;
	}
	[data-color='red'] {
		color: #b91c1c;
	}

	.latency {
		opacity: 0.65;
		font-variant-numeric: tabular-nums;
		font-size: 0.72rem;
	}
	.lat-p95 {
		opacity: 0.6;
	}
</style>
