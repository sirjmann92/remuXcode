<script lang="ts">
import { getAppLogs, getConfig, updateConfig } from '$lib/api';
import type { AppLogEntry, AppLogLevel } from '$lib/types';

const ALL_LEVELS: AppLogLevel[] = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'];

// Levels at or above the given severity, e.g. 'WARNING' -> ['WARNING', 'ERROR', 'CRITICAL']
function levelsAtOrAbove(level: AppLogLevel): AppLogLevel[] {
  return ALL_LEVELS.slice(ALL_LEVELS.indexOf(level));
}

let entries: AppLogEntry[] = $state([]);
let loadError = $state('');
let initialLoad = $state(true);
let search = $state('');
let filterLevel = $state(new Set<AppLogLevel>(levelsAtOrAbove('INFO')));
let logEl = $state<HTMLElement | undefined>(undefined);
let autoScroll = $state(true);

let effectiveLevel: AppLogLevel = $state('INFO');
let savingLevel = $state(false);
let saveMsg = $state('');

const filteredEntries = $derived(
  entries.filter(
    (e) =>
      filterLevel.has(e.level) &&
      (!search || e.message.toLowerCase().includes(search.toLowerCase())),
  ),
);

function toggleLevel(l: AppLogLevel) {
  const next = new Set(filterLevel);
  next.has(l) ? next.delete(l) : next.add(l);
  filterLevel = next;
}

function onLogScroll() {
  if (!logEl) return;
  autoScroll = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 24;
}

$effect(() => {
  filteredEntries;
  if (autoScroll && logEl) {
    logEl.scrollTop = logEl.scrollHeight;
  }
});

async function fetchLogs() {
  try {
    const res = await getAppLogs(2000);
    entries = res.entries;
    loadError = '';
  } catch (e) {
    loadError = e instanceof Error ? e.message : 'Failed to load logs';
  } finally {
    initialLoad = false;
  }
}

$effect(() => {
  fetchLogs();
  const id = setInterval(fetchLogs, 5000);
  return () => clearInterval(id);
});

$effect(() => {
  getConfig()
    .then((cfg) => {
      effectiveLevel = cfg.log_level;
      // Default the display filter to match what's actually being captured
      filterLevel = new Set(levelsAtOrAbove(cfg.log_level));
    })
    .catch(() => {});
});

async function changeLevel(level: AppLogLevel) {
  const previousLevel = effectiveLevel;
  effectiveLevel = level;
  savingLevel = true;
  saveMsg = '';
  try {
    await updateConfig({ log_level: level });
    // Bring the display filter along so the change is visible immediately,
    // instead of silently requiring the user to also flip filter chips.
    filterLevel = new Set(levelsAtOrAbove(level));
    saveMsg = 'Saved';
    setTimeout(() => {
      saveMsg = '';
    }, 1500);
  } catch (e) {
    effectiveLevel = previousLevel;
    saveMsg = e instanceof Error ? e.message : 'Save failed';
  } finally {
    savingLevel = false;
  }
}

function downloadLog() {
  if (!entries.length) return;
  const lines = entries
    .map((e) => `${new Date(e.ts * 1000).toISOString()} ${e.level} [${e.logger}] ${e.message}`)
    .join('\n');
  const blob = new Blob([lines], { type: 'text/plain' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'remuxcode.log';
  a.click();
  URL.revokeObjectURL(url);
}

function formatTs(ts: number): string {
  return new Date(ts * 1000).toLocaleString([], {
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

const levelClass: Record<AppLogLevel, string> = {
  DEBUG: 'text-base-content/60',
  INFO: 'text-info/70',
  WARNING: 'text-warning/80',
  ERROR: 'text-error/80',
  CRITICAL: 'text-error font-semibold',
};
</script>

<div class="space-y-4">
  {#if saveMsg}
    <div class="toast toast-top toast-end z-50">
      <div class="alert {saveMsg === 'Saved' ? 'alert-success' : 'alert-error'} alert-sm py-2">
        <span class="text-xs">{saveMsg}</span>
      </div>
    </div>
  {/if}

  <!-- Effective log level -->
  <div class="flex flex-col sm:flex-row sm:items-center gap-3 justify-between">
    <div class="flex items-center gap-2 flex-wrap">
      <span class="text-xs font-medium">Log Level</span>
      <select
        class="select select-xs select-bordered w-28 font-mono"
        value={effectiveLevel}
        disabled={savingLevel}
        onchange={(e) => changeLevel(e.currentTarget.value as AppLogLevel)}
      >
        {#each ['DEBUG', 'INFO', 'WARNING', 'ERROR'] as l}
          <option value={l}>{l}</option>
        {/each}
      </select>
      <span class="text-xs text-base-content/60">controls what's captured going forward — the filters below change what you're viewing</span>
    </div>
    <button class="btn btn-ghost btn-xs gap-1 opacity-75 hover:opacity-100" onclick={downloadLog} title="Download full log">
      <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
        <path stroke-linecap="round" stroke-linejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 12 12 16.5m0 0L7.5 12m4.5 4.5V3" />
      </svg>
      Download
    </button>
  </div>

  <!-- Filters + Search -->
  <div class="flex flex-col sm:flex-row gap-3">
    <div class="join">
      {#each ALL_LEVELS as l}
        <button
          class="join-item btn btn-xs {filterLevel.has(l) ? 'btn-primary' : 'btn-ghost border-base-content/10 opacity-50'}"
          onclick={() => toggleLevel(l)}
        >
          {l}
        </button>
      {/each}
    </div>
    <div class="relative flex-1">
      <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-base-content/75" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
        <path stroke-linecap="round" stroke-linejoin="round" d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z" />
      </svg>
      <input
        type="text"
        placeholder="Search messages…"
        class="input input-sm input-bordered w-full pl-9 {search ? 'pr-8' : ''}"
        bind:value={search}
      />
      {#if search}
        <button
          class="absolute right-2 top-1/2 -translate-y-1/2 btn btn-ghost btn-xs btn-circle text-base-content/85"
          onclick={() => (search = '')}
          title="Clear search"
          aria-label="Clear search"
        >
          <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="M6 18 18 6M6 6l12 12" />
          </svg>
        </button>
      {/if}
    </div>
  </div>

  <!-- Log entries -->
  <div
    bind:this={logEl}
    onscroll={onLogScroll}
    class="h-[calc(100vh-16rem)] overflow-y-auto font-mono text-xs bg-base-300/40 rounded-lg p-3 space-y-0.5 border border-base-content/5"
  >
    {#if initialLoad}
      <p class="text-base-content/75 text-center py-6">Loading…</p>
    {:else if loadError}
      <p class="text-error/80 text-center py-6">{loadError}</p>
    {:else if filteredEntries.length === 0}
      <p class="text-base-content/75 text-center py-6">No log entries</p>
    {:else}
      {#each filteredEntries as entry, i (i)}
        <div class="flex gap-2 leading-relaxed">
          <span class="shrink-0 text-base-content/75">{formatTs(entry.ts)}</span>
          <span class="shrink-0 w-16 {levelClass[entry.level]}">{entry.level}</span>
          <span class="shrink-0 w-28 text-base-content/60 truncate" title={entry.logger}>{entry.logger}</span>
          <span class="text-base-content/95 break-all whitespace-pre-wrap">{entry.message}</span>
        </div>
      {/each}
    {/if}
  </div>
</div>
