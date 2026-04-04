<script lang="ts">
import { getConfig, getJobs } from '$lib/api';
import JobCard from '$lib/components/JobCard.svelte';
import type { ConfigSummary, Job } from '$lib/types';

let activeJobs: Job[] = $state([]);
let pendingJobs: Job[] = $state([]);
let recentJobs: Job[] = $state([]);
let totalCompleted = $state(0);
let totalFailed = $state(0);
let config: ConfigSummary | null = $state(null);
let loading = $state(true);

async function fetchData() {
  try {
    const [overview, running, pending, completed, failed, configData] = await Promise.all([
      getJobs({ limit: 1 }),
      getJobs({ status: 'running', limit: 25 }),
      getJobs({ status: 'pending', limit: 25 }),
      getJobs({ status: 'completed', limit: 5 }),
      getJobs({ status: 'failed', limit: 5 }),
      getConfig(),
    ]);

    activeJobs = running.jobs;
    pendingJobs = pending.jobs;
    recentJobs = [...completed.jobs, ...failed.jobs]
      .toSorted((a, b) => (b.completed_at ?? 0) - (a.completed_at ?? 0))
      .slice(0, 5);
    totalCompleted = overview.counts?.completed ?? completed.total ?? completed.jobs.length;
    totalFailed = overview.counts?.failed ?? failed.total ?? failed.jobs.length;
    config = configData;
  } catch {
    // keep stale data
  } finally {
    loading = false;
  }
}

$effect(() => {
  fetchData();
  const id = setInterval(fetchData, 3000);
  return () => clearInterval(id);
});
</script>

<svelte:head>
  <title>Dashboard · remuXcode</title>
</svelte:head>

<div class="space-y-6">
  <!-- Stats -->
  <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
    <a href="/jobs?filter=running" class="card-glass rounded-box p-4 stat-card hover:ring-1 hover:ring-info/40 transition-all">
      <div class="text-xs text-base-content/40 uppercase tracking-wider mb-1">Running</div>
      <div class="text-3xl font-bold text-info">{activeJobs.length}</div>
    </a>
    <a href="/jobs?filter=pending" class="card-glass rounded-box p-4 stat-card hover:ring-1 hover:ring-warning/40 transition-all">
      <div class="text-xs text-base-content/40 uppercase tracking-wider mb-1">Pending</div>
      <div class="text-3xl font-bold text-warning">{pendingJobs.length}</div>
    </a>
    <a href="/jobs?filter=completed" class="card-glass rounded-box p-4 stat-card hover:ring-1 hover:ring-success/40 transition-all">
      <div class="text-xs text-base-content/40 uppercase tracking-wider mb-1">Completed</div>
      <div class="text-3xl font-bold text-success">{totalCompleted}</div>
    </a>
    <a href="/jobs?filter=failed" class="card-glass rounded-box p-4 stat-card hover:ring-1 hover:ring-error/40 transition-all">
      <div class="text-xs text-base-content/40 uppercase tracking-wider mb-1">Failed</div>
      <div class="text-3xl font-bold text-error">{totalFailed}</div>
    </a>
  </div>

  <!-- Config status -->
  {#if config}
    <div class="grid grid-cols-2 md:grid-cols-5 gap-3 text-sm">
      <div class="card-glass rounded-box p-3 flex items-center gap-2">
        <span class="relative flex h-2 w-2">
          {#if config.audio.enabled}
            <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-success opacity-50"></span>
          {/if}
          <span class="relative inline-flex rounded-full h-2 w-2 {config.audio.enabled ? 'bg-success' : 'bg-base-content/20'}"></span>
        </span>
        <span class="text-base-content/70">Audio</span>
      </div>
      <div class="card-glass rounded-box p-3 flex items-center gap-2">
        <span class="relative flex h-2 w-2">
          {#if config.video.enabled}
            <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-success opacity-50"></span>
          {/if}
          <span class="relative inline-flex rounded-full h-2 w-2 {config.video.enabled ? 'bg-success' : 'bg-base-content/20'}"></span>
        </span>
        <span class="text-base-content/70">Video</span>
      </div>
      <div class="card-glass rounded-box p-3 flex items-center gap-2">
        <span class="relative flex h-2 w-2">
          {#if config.cleanup.enabled}
            <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-success opacity-50"></span>
          {/if}
          <span class="relative inline-flex rounded-full h-2 w-2 {config.cleanup.enabled ? 'bg-success' : 'bg-base-content/20'}"></span>
        </span>
        <span class="text-base-content/70">Cleanup</span>
      </div>
      <div class="card-glass rounded-box p-3 flex items-center gap-2">
        <span class="relative flex h-2 w-2">
          <span class="relative inline-flex rounded-full h-2 w-2 {config.sonarr.configured ? 'bg-success' : 'bg-base-content/20'}"></span>
        </span>
        <span class="text-base-content/70">Sonarr</span>
      </div>
      <div class="card-glass rounded-box p-3 flex items-center gap-2">
        <span class="relative flex h-2 w-2">
          <span class="relative inline-flex rounded-full h-2 w-2 {config.radarr.configured ? 'bg-success' : 'bg-base-content/20'}"></span>
        </span>
        <span class="text-base-content/70">Radarr</span>
      </div>
    </div>
  {/if}

  <!-- Active jobs -->
  {#if activeJobs.length > 0}
    <section>
      <h2 class="text-sm font-semibold uppercase tracking-wider text-base-content/40 mb-3 flex items-center gap-2">
        <span class="w-1.5 h-1.5 rounded-full bg-info animate-pulse"></span>
        In Progress
      </h2>
      <div class="space-y-2">
        {#each activeJobs as job (job.id)}
          <JobCard {job} onRemoved={fetchData} />
        {/each}
      </div>
    </section>
  {/if}

  <!-- Pending jobs -->
  {#if pendingJobs.length > 0}
    <section>
      <h2 class="text-sm font-semibold uppercase tracking-wider text-base-content/40 mb-3 flex items-center gap-2">
        <span class="w-1.5 h-1.5 rounded-full bg-warning"></span>
        Queued <span class="badge badge-xs badge-warning ml-1">{pendingJobs.length}</span>
      </h2>
      <div class="space-y-2">
        {#each pendingJobs as job (job.id)}
          <JobCard {job} onRemoved={fetchData} />
        {/each}
      </div>
    </section>
  {/if}

  <!-- Recent activity -->
  <section>
    <h2 class="text-sm font-semibold uppercase tracking-wider text-base-content/40 mb-3">Recent Activity</h2>
    {#if loading}
      <div class="flex justify-center py-8">
        <span class="loading loading-spinner loading-lg"></span>
      </div>
    {:else if recentJobs.length === 0}
      <div class="card-glass rounded-box p-8 text-center">
        <svg xmlns="http://www.w3.org/2000/svg" class="w-10 h-10 mx-auto mb-3 text-base-content/15" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1">
          <path stroke-linecap="round" stroke-linejoin="round" d="M3.75 12h16.5m-16.5 3.75h16.5M3.75 19.5h16.5M5.625 4.5h12.75a1.875 1.875 0 0 1 0 3.75H5.625a1.875 1.875 0 0 1 0-3.75Z" />
        </svg>
        <p class="text-sm text-base-content/40">No recent activity</p>
        <p class="text-xs text-base-content/25 mt-1">Jobs will appear when Sonarr/Radarr sends webhooks</p>
      </div>
    {:else}
      <div class="space-y-2">
        {#each recentJobs as job (job.id)}
          <JobCard {job} onRemoved={fetchData} />
        {/each}
      </div>
    {/if}
  </section>
</div>
