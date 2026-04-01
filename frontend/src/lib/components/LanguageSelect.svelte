<script lang="ts">
import { langNames } from '$lib/languages';

interface Props {
  selected: string[];
  onchange: (codes: string[]) => void;
  disabled?: boolean;
}

let { selected, onchange, disabled = false }: Props = $props();
let open = $state(false);
let search = $state('');
let panelEl: HTMLDivElement | undefined = $state();
let triggerEl: HTMLButtonElement | undefined = $state();

// Deduplicated, sorted language entries (build once)
const allEntries: { code: string; name: string }[] = (() => {
  const seen = new Map<string, string>();
  for (const [code, name] of Object.entries(langNames)) {
    if (code === 'und') continue;
    if (!seen.has(name)) seen.set(name, code);
  }
  return Array.from(seen.entries())
    .map(([name, code]) => ({ code, name }))
    .sort((a, b) => a.name.localeCompare(b.name));
})();

// Build a code→name lookup for display
const codeToName: Record<string, string> = {};
for (const e of allEntries) codeToName[e.code] = e.name;

const filtered = $derived.by(() => {
  if (!search) return allEntries;
  const q = search.toLowerCase();
  return allEntries.filter((e) => e.name.toLowerCase().includes(q) || e.code.includes(q));
});

const selectedNames = $derived(
  selected
    .map((c) => codeToName[c] ?? c.toUpperCase())
    .sort()
    .join(', '),
);

function toggle(code: string) {
  const next = selected.includes(code) ? selected.filter((c) => c !== code) : [...selected, code];
  if (next.length > 0) onchange(next);
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    open = false;
    search = '';
  }
}

function handleClickOutside(e: MouseEvent) {
  if (
    panelEl &&
    !panelEl.contains(e.target as Node) &&
    triggerEl &&
    !triggerEl.contains(e.target as Node)
  ) {
    open = false;
    search = '';
  }
}

$effect(() => {
  if (open) {
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }
});
</script>

<div class="relative">
	<button
		bind:this={triggerEl}
		type="button"
		class="btn btn-xs btn-outline font-normal text-xs max-w-56 truncate"
		{disabled}
		onclick={() => {
			if (!disabled) open = !open;
		}}
	>
		{selectedNames || 'Select languages…'}
		<svg
			xmlns="http://www.w3.org/2000/svg"
			class="w-3 h-3 shrink-0"
			fill="none"
			viewBox="0 0 24 24"
			stroke="currentColor"
			stroke-width="2"
		>
			<path stroke-linecap="round" stroke-linejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
		</svg>
	</button>

	{#if open}
		<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
		<div
			bind:this={panelEl}
			class="absolute right-0 z-50 mt-1 w-64 rounded-lg border border-base-300 bg-base-100 shadow-xl"
			onkeydown={handleKeydown}
			role="listbox"
			aria-multiselectable="true"
			tabindex="-1"
		>
			<div class="p-2">
				<input
					type="text"
					class="input input-xs input-bordered w-full"
					placeholder="Search languages…"
					bind:value={search}
				/>
			</div>
			<div class="overflow-y-auto max-h-56 px-1 pb-2">
				{#each filtered as entry (entry.code)}
					<label
						class="flex items-center gap-2 px-2 py-1 rounded cursor-pointer hover:bg-base-200 text-xs"
					>
						<input
							type="checkbox"
							class="checkbox checkbox-xs checkbox-primary"
							checked={selected.includes(entry.code)}
							onchange={() => toggle(entry.code)}
						/>
						<span class="flex-1">{entry.name}</span>
						<span class="font-mono text-base-content/40">{entry.code}</span>
					</label>
				{/each}
				{#if filtered.length === 0}
					<p class="text-xs text-center opacity-40 py-2">No matches</p>
				{/if}
			</div>
		</div>
	{/if}
</div>
