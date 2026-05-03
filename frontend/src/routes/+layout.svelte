<script>
// @ts-nocheck
// Self-hosted Inter + Inter Tight, latin subset only. UI is English-only, so
// shipping Cyrillic/Greek/Vietnamese subsets bloats the build with assets
// no client ever downloads. Weights match actual usage: body 400/500/600,
// headings (Inter Tight) 600/700.
import '@fontsource/inter/latin-400.css';
import '@fontsource/inter/latin-500.css';
import '@fontsource/inter/latin-600.css';
import '@fontsource/inter-tight/latin-600.css';
import '@fontsource/inter-tight/latin-700.css';
import '$lib/theme.css';
import { onMount } from 'svelte';
import { theme } from '$lib/stores.js';

let { children } = $props();

onMount(() => {
	// Mirror the theme store to the <html> attributes whenever it changes.
	// The initial attribute was already set synchronously by the inline script
	// in app.html — this just keeps DOM and store in sync after the user toggles.
	const unsub = theme.subscribe((value) => {
		const t = value === 'dark' ? 'dark' : 'light';
		document.documentElement.setAttribute('data-theme', t);
		document.documentElement.setAttribute('data-bs-theme', t);
	});
	return () => unsub();
});
</script>

{@render children?.()}
