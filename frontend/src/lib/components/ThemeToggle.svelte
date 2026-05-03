<script>
// @ts-nocheck
import { theme } from '../stores.js';

function toggle() {
	theme.update((t) => (t === 'dark' ? 'light' : 'dark'));
}

const isDark = $derived($theme === 'dark');
const label = $derived(isDark ? 'Switch to light mode' : 'Switch to dark mode');
</script>

<button
	type="button"
	class="theme-toggle"
	onclick={toggle}
	title={label}
	aria-label={label}
>
	{#if isDark}
		<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none">
			<circle cx="12" cy="12" r="4" stroke="currentColor" stroke-width="2" />
			<path
				d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"
				stroke="currentColor"
				stroke-width="2"
				stroke-linecap="round"
			/>
		</svg>
	{:else}
		<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none">
			<path
				d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z"
				stroke="currentColor"
				stroke-width="2"
				stroke-linecap="round"
				stroke-linejoin="round"
			/>
		</svg>
	{/if}
</button>

<style>
.theme-toggle {
	display: inline-flex;
	align-items: center;
	justify-content: center;
	width: 32px;
	height: 32px;
	border-radius: 999px;
	border: 1px solid #e9ecef;
	background: #fff;
	color: #495057;
	cursor: pointer;
	font-family: inherit;
	padding: 0;
	transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
}

.theme-toggle:hover {
	background: #f1f3f5;
	color: rgb(33, 37, 41);
}
</style>
