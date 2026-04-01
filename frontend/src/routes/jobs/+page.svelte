<script lang="ts">
import { cancelAllJobs, cancelAllPending, deleteJob, getJobs } from '$lib/api';
import JobCard from '$lib/components/JobCard.svelte';
import type { Job, JobStatus } from '$lib/types';

let jobs: Job[] = $state([]);
let loading = $state(true);
let filter: JobStatus | 'all' = $state('all');
let search: string = $state('');

const filtered = $derived.by(() => {
  let result = jobs;
  if (filter !== 'all') {
    result = result.filter((j) => j.status === filter);
  }
  if (search) {
    const q = search.toLowerCase();
    result = result.filter((j) => j.file_path.toLowerCase().includes(q));
  }
  return result.toSorted((a, b) => b.created_at - a.created_at);
});

const counts = $derived({
  all: jobs.length,
  pending: jobs.filter((j) => j.status === 'pending').length,
  running: jobs.filter((j) => j.status === 'running').length,
  completed: jobs.filter((j) => j.status === 'completed').length,
  failed: jobs.filter((j) => j.status === 'failed').length,
  cancelled: jobs.filter((j) => j.status === 'cancelled').length,
});

let loadError = $state(false);
async function fetchJobs() {
  try {
    const res = await getJobs();
    jobs = res.jobs;
    loadError = false;
  } catch {
    loadError = true;
  } finally {
    loading = false;
  }
}

$effect(() => {
  fetchJobs();
  const id = setInterval(fetchJobs, 3000);
  return () => clearInterval(id);
});

async function clearCompleted() {
  const completed = jobs.filter(
    (j) => j.status === 'completed' || j.status === 'failed' || j.status === 'cancelled',
  );
  for (const job of completed) {
    try {
      await deleteJob(job.id);
    } catch {
      // ignore individual failures
    }
  }
  await fetchJobs();
}

async function handleCancelPending() {
  try {
    await cancelAllPending();
  } catch {
    // ignore
  }
  await fetchJobs();
}

async function handleStopAll() {
  try {
    await cancelAllJobs();
  } catch {
    // ignore
  }
  await fetchJobs();
}

const filters: { value: JobStatus | 'all'; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'running', label: 'Running' },
  { value: 'pending', label: 'Pending' },
  { value: 'completed', label: 'Completed' },
  { value: 'failed', label: 'Failed' },
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
  </div>

  <div class="flex items-center justify-between">
    <div class="text-sm text-base-content/50">
      {filtered.length} job{filtered.length !== 1 ? 's' : ''}
    </div>
    <div class="flex items-center gap-2">
      {#if counts.running > 0 || counts.pending > 0}
        <button class="btn btn-sm btn-ghost text-error hover:bg-error/10" onclick={handleStopAll}>
          <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="M5.25 7.5A2.25 2.25 0 0 1 7.5 5.25h9a2.25 2.25 0 0 1 2.25 2.25v9a2.25 2.25 0 0 1-2.25 2.25h-9a2.25 2.25 0 0 1-2.25-2.25v-9Z" />
          </svg>
          Stop All ({counts.running + counts.pending})
        </button>
      {/if}
      <button class="btn btn-sm btn-ghost text-error hover:bg-error/10" onclick={clearCompleted}>
        <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
          <path stroke-linecap="round" stroke-linejoin="round" d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0" />
        </svg>
        Clear Finished
      </button>
    </div>
  </div>

  <!-- Job list -->
  {#if loading}
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
  {:else if filtered.length === 0}
    <div class="card-glass rounded-box p-12 text-center">
      <svg xmlns="http://www.w3.org/2000/svg" class="w-12 h-12 mx-auto mb-4 text-base-content/10" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1">
        <path stroke-linecap="round" stroke-linejoin="round" d="M3.75 12h16.5m-16.5 3.75h16.5M3.75 19.5h16.5M5.625 4.5h12.75a1.875 1.875 0 0 1 0 3.75H5.625a1.875 1.875 0 0 1 0-3.75Z" />
      </svg>
      <p class="text-base text-base-content/40">No jobs found</p>
      <p class="text-sm text-base-content/25 mt-1">Jobs will appear here when Sonarr/Radarr sends webhooks</p>
    </div>
  {:else}
    <div class="space-y-2">
      {#each filtered as job (job.id)}
        <JobCard {job} onRemoved={fetchJobs} detailed={true} />
      {/each}
    </div>
  {/if}
</div>
