<script lang="ts">
import { page } from '$app/stores';
import { cancelAllPending, cancelRunning, deleteFinished, getJobs } from '$lib/api';
import JobCard from '$lib/components/JobCard.svelte';
import type { Job, JobStatus, JobsCounts } from '$lib/types';

let jobs: Job[] = $state([]);
let initialLoad = $state(true);
let loadingMore = $state(false);
let autoRefreshEnabled = $state(true);
let total = $state(0);
let hasMore = $state(false);
let requestId = 0;
const PAGE_SIZE = 100;

let counts: JobsCounts = $state({
  all: 0,
  pending: 0,
  running: 0,
  completed: 0,
  failed: 0,
  cancelled: 0,
});

const validFilters: Array<JobStatus | 'all'> = [
  'all',
  'running',
  'pending',
  'completed',
  'failed',
  'cancelled',
];
const urlFilter = $page.url.searchParams.get('filter') ?? 'all';
let filter: JobStatus | 'all' = $state(
  validFilters.includes(urlFilter as JobStatus | 'all') ? (urlFilter as JobStatus | 'all') : 'all',
);
let search: string = $state('');
let jobTypeFilter: string = $state('all');
let mediaTypeFilter: string = $state('all');
let dateFrom: string = $state('');
let dateTo: string = $state('');
let showFilters = $state(false);

let loadError = $state(false);

const hasActiveFilters = $derived(
  jobTypeFilter !== 'all' || mediaTypeFilter !== 'all' || dateFrom !== '' || dateTo !== '',
);

function clearFilters() {
  jobTypeFilter = 'all';
  mediaTypeFilter = 'all';
  dateFrom = '';
  dateTo = '';
}

async function fetchJobs(reset = true) {
  const rid = ++requestId;
  if (reset && initialLoad) {
    // Only show loading spinner on the very first load
  } else if (!reset) {
    loadingMore = true;
  }

  try {
    const res = await getJobs({
      limit: PAGE_SIZE,
      offset: reset ? 0 : jobs.length,
      status: filter,
      search: search.trim() || undefined,
      phase: jobTypeFilter !== 'all' ? jobTypeFilter : undefined,
      media_type: mediaTypeFilter !== 'all' ? mediaTypeFilter : undefined,
      date_from: dateFrom || undefined,
      date_to: dateTo || undefined,
    });

    if (rid !== requestId) return;

    if (reset) {
      jobs = res.jobs;
      autoRefreshEnabled = true;
    } else {
      const seen = new Set(jobs.map((j) => j.id));
      const append = res.jobs.filter((j) => !seen.has(j.id));
      jobs = [...jobs, ...append];
      autoRefreshEnabled = false;
    }

    total = res.total ?? jobs.length;
    hasMore = Boolean(res.has_more);
    if (res.counts) {
      counts = res.counts;
    }
    loadError = false;
  } catch {
    loadError = true;
  } finally {
    initialLoad = false;
    loadingMore = false;
  }
}

// Single reactive effect: re-fetch when filter or search changes,
// and poll on a 5-second interval for background updates.
let searchDebounce: ReturnType<typeof setTimeout> | null = null;
$effect(() => {
  // Track reactive deps
  const _filter = filter;
  const q = search;
  const _jt = jobTypeFilter;
  const _mt = mediaTypeFilter;
  const _df = dateFrom;
  const _dt = dateTo;
  // Debounce search, immediate for filter changes
  if (searchDebounce) clearTimeout(searchDebounce);
  searchDebounce = setTimeout(
    () => {
      fetchJobs(true);
    },
    q ? 250 : 0,
  );

  // Set up polling interval
  const id = setInterval(() => {
    if (autoRefreshEnabled) {
      fetchJobs(true);
    }
  }, 5000);

  return () => {
    if (searchDebounce) clearTimeout(searchDebounce);
    clearInterval(id);
  };
});

async function loadMore() {
  if (!hasMore || loadingMore) return;
  await fetchJobs(false);
}

let confirmDeleteOpen = $state(false);

async function handleStopCurrent() {
  try {
    await cancelRunning();
  } catch {
    // ignore
  }
  await fetchJobs(true);
}

async function handleClearPending() {
  try {
    await cancelAllPending();
  } catch {
    // ignore
  }
  await fetchJobs(true);
}

async function handleDeleteCompleted() {
  try {
    await deleteFinished();
  } catch {
    // ignore
  }
  confirmDeleteOpen = false;
  await fetchJobs(true);
}

const filters: { value: JobStatus | 'all'; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'running', label: 'Running' },
  { value: 'pending', label: 'Pending' },
  { value: 'completed', label: 'Completed' },
  { value: 'failed', label: 'Failed' },
  { value: 'cancelled', label: 'Cancelled' },
];
</script>

<svelte:head>
  <title>Jobs · remuXcode</title>
</svelte:head>

<div class="space-y-4">
  <!-- Filters + Search -->
  <div class="flex flex-col sm:flex-row gap-3">
    <div class="join">
      {#each filters as f}
        <button
          class="join-item btn btn-sm {filter === f.value ? 'btn-primary' : 'btn-ghost border-base-content/10'}"
          onclick={() => (filter = f.value)}
        >
          {f.label}
          {#if counts[f.value] > 0}
            <span class="badge badge-xs {filter === f.value ? 'badge-primary-content/20' : 'badge-ghost'} ml-1">{counts[f.value]}</span>
          {/if}
        </button>
      {/each}
    </div>
    <div class="relative flex-1">
      <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-base-content/30" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
        <path stroke-linecap="round" stroke-linejoin="round" d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z" />
      </svg>
      <input
        type="text"
        placeholder="Search files…"
        class="input input-sm input-bordered w-full pl-9"
        bind:value={search}
      />
    </div>
    <button
      class="btn btn-sm btn-ghost gap-1 {showFilters || hasActiveFilters ? 'text-primary' : 'text-base-content/40'}"
      onclick={() => (showFilters = !showFilters)}
      title="Toggle filters"
    >
      <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
        <path stroke-linecap="round" stroke-linejoin="round" d="M12 3c2.755 0 5.455.232 8.083.678.533.09.917.556.917 1.096v1.044a2.25 2.25 0 0 1-.659 1.591l-5.432 5.432a2.25 2.25 0 0 0-.659 1.591v2.927a2.25 2.25 0 0 1-1.244 2.013L9.75 21v-6.568a2.25 2.25 0 0 0-.659-1.591L3.659 7.409A2.25 2.25 0 0 1 3 5.818V4.774c0-.54.384-1.006.917-1.096A48.32 48.32 0 0 1 12 3Z" />
      </svg>
      Filters
      {#if hasActiveFilters}
        <span class="badge badge-xs badge-primary"></span>
      {/if}
    </button>
  </div>

  <!-- Advanced Filters -->
  {#if showFilters}
    <div class="flex flex-wrap items-end gap-3 px-1">
      <label class="form-control w-auto">
        <span class="label-text text-xs text-base-content/40 pb-0.5">Worker</span>
        <select class="select select-xs select-bordered font-mono w-28" bind:value={jobTypeFilter}>
          <option value="all">All</option>
          <option value="video">Video</option>
          <option value="audio">Audio</option>
          <option value="cleanup">Cleanup</option>
        </select>
      </label>
      <label class="form-control w-auto">
        <span class="label-text text-xs text-base-content/40 pb-0.5">Media</span>
        <select class="select select-xs select-bordered font-mono w-28" bind:value={mediaTypeFilter}>
          <option value="all">All</option>
          <option value="movie">Movies</option>
          <option value="episode">Episodes</option>
        </select>
      </label>
      <label class="form-control w-auto">
        <span class="label-text text-xs text-base-content/40 pb-0.5">From</span>
        <input type="date" class="input input-xs input-bordered font-mono w-36" bind:value={dateFrom} />
      </label>
      <label class="form-control w-auto">
        <span class="label-text text-xs text-base-content/40 pb-0.5">To</span>
        <input type="date" class="input input-xs input-bordered font-mono w-36" bind:value={dateTo} />
      </label>
      {#if hasActiveFilters}
        <button class="btn btn-xs btn-ghost text-base-content/40" onclick={clearFilters}>Clear</button>
      {/if}
    </div>
  {/if}

  <div class="flex items-center justify-between">
    <div class="text-sm text-base-content/50">
      Showing {jobs.length} of {total} job{total !== 1 ? 's' : ''}
    </div>
    <div class="flex items-center gap-2">
      {#if counts.running > 0}
        <button class="btn btn-sm btn-ghost text-warning hover:bg-warning/10" onclick={handleStopCurrent}>
          <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="M5.25 7.5A2.25 2.25 0 0 1 7.5 5.25h9a2.25 2.25 0 0 1 2.25 2.25v9a2.25 2.25 0 0 1-2.25 2.25h-9a2.25 2.25 0 0 1-2.25-2.25v-9Z" />
          </svg>
          Stop Current ({counts.running})
        </button>
      {/if}
      {#if counts.pending > 0}
        <button class="btn btn-sm btn-ghost text-warning hover:bg-warning/10" onclick={handleClearPending}>
          <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="M6 18 18 6M6 6l12 12" />
          </svg>
          Clear Pending ({counts.pending})
        </button>
      {/if}
      {#if counts.completed + counts.failed + counts.cancelled > 0}
        <button class="btn btn-sm btn-ghost text-error hover:bg-error/10" onclick={() => (confirmDeleteOpen = true)}>
          <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0" />
          </svg>
          Delete Completed ({counts.completed + counts.failed + counts.cancelled})
        </button>
      {/if}
    </div>
  </div>

  <!-- Job list -->
  {#if initialLoad}
    <div class="flex justify-center py-12">
      <span class="loading loading-spinner loading-lg"></span>
    </div>
  {:else if loadError}
    <div class="card-glass rounded-box p-12 text-center">
      <svg xmlns="http://www.w3.org/2000/svg" class="w-12 h-12 mx-auto mb-4 text-error/20" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1">
        <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v2m0 4h.01M21 12c0 4.97-4.03 9-9 9s-9-4.03-9-9 4.03-9 9-9 9 4.03 9 9Z" />
      </svg>
      <p class="text-base text-error/80">Failed to load jobs</p>
      <p class="text-sm text-base-content/40 mt-1">Check backend logs or try again later.</p>
    </div>
  {:else if jobs.length === 0}
    <div class="card-glass rounded-box p-12 text-center">
      <svg xmlns="http://www.w3.org/2000/svg" class="w-12 h-12 mx-auto mb-4 text-base-content/10" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1">
        <path stroke-linecap="round" stroke-linejoin="round" d="M3.75 12h16.5m-16.5 3.75h16.5M3.75 19.5h16.5M5.625 4.5h12.75a1.875 1.875 0 0 1 0 3.75H5.625a1.875 1.875 0 0 1 0-3.75Z" />
      </svg>
      <p class="text-base text-base-content/40">No jobs found</p>
      <p class="text-sm text-base-content/25 mt-1">Jobs will appear here when Sonarr/Radarr sends webhooks</p>
    </div>
  {:else}
    <div class="space-y-2">
      {#each jobs as job (job.id)}
        <JobCard {job} onRemoved={fetchJobs} detailed={true} />
      {/each}
    </div>
    {#if hasMore}
      <div class="flex justify-center pt-2">
        <button class="btn btn-sm btn-outline" onclick={loadMore} disabled={loadingMore}>
          {#if loadingMore}
            <span class="loading loading-spinner loading-xs"></span>
          {/if}
          Load more
        </button>
      </div>
    {/if}
  {/if}
</div>

<!-- Delete Completed confirmation dialog -->
{#if confirmDeleteOpen}
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onclick={() => (confirmDeleteOpen = false)}>
    <div class="card bg-base-200 shadow-xl w-96 max-w-[90vw]" onclick={(e) => e.stopPropagation()}>
      <div class="card-body gap-4">
        <h3 class="card-title text-error text-base">
          <svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" />
          </svg>
          Delete Completed Jobs
        </h3>
        <p class="text-sm text-base-content/70">
          This will permanently delete <strong>{counts.completed + counts.failed + counts.cancelled}</strong> finished job{counts.completed + counts.failed + counts.cancelled !== 1 ? 's' : ''} from the database. This cannot be undone.
        </p>
        <div class="card-actions justify-end gap-2">
          <button class="btn btn-sm btn-ghost" onclick={() => (confirmDeleteOpen = false)}>Cancel</button>
          <button class="btn btn-sm btn-error" onclick={handleDeleteCompleted}>Delete</button>
        </div>
      </div>
    </div>
  </div>
{/if}
