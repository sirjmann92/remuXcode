<script lang="ts">
  import type { Job } from '$lib/types';
  import StatusBadge from './StatusBadge.svelte';
  import { deleteJob } from '$lib/api';

  interface Props {
    job: Job;
    onRemoved?: () => void;
  }

  let { job, onRemoved }: Props = $props();
  let deleting = $state(false);

  const fileName = $derived(job.file_path.split('/').pop() ?? job.file_path);
  const elapsed = $derived.by(() => {
    if (!job.started_at) return null;
    const end = job.completed_at ?? Date.now() / 1000;
    const secs = Math.floor(end - job.started_at);
    const m = Math.floor(secs / 60);
    const s = secs % 60;
    return m > 0 ? `${m}m ${s}s` : `${s}s`;
  });

  async function handleDelete() {
    deleting = true;
    try {
      await deleteJob(job.id);
      onRemoved?.();
    } catch {
      // ignore
    } finally {
      deleting = false;
    }
  }
</script>

<div class="card-glass rounded-box">
  <div class="p-4 space-y-2">
    <div class="flex items-center justify-between gap-2">
      <div class="flex items-center gap-2 min-w-0">
        <StatusBadge status={job.status} />
        <span class="badge badge-outline badge-xs text-base-content/40">{job.job_type}</span>
        <span class="badge badge-outline badge-xs text-base-content/40">{job.source}</span>
      </div>
      {#if job.status === 'completed' || job.status === 'failed' || job.status === 'cancelled'}
        <button
          class="btn btn-ghost btn-xs opacity-30 hover:opacity-100 transition-opacity"
          onclick={handleDelete}
          disabled={deleting}
          title="Remove"
        >
          <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M6 18 18 6M6 6l12 12" />
          </svg>
        </button>
      {/if}
    </div>

    <p class="text-sm font-mono truncate text-base-content/80" title={job.file_path}>{fileName}</p>

    {#if job.status === 'running'}
      <div class="space-y-1">
        <div class="flex justify-between items-center">
          <span class="text-xs text-base-content/40">{job.progress.toFixed(1)}%</span>
          {#if elapsed}
            <span class="text-xs text-base-content/30">{elapsed}</span>
          {/if}
        </div>
        <progress class="progress progress-info w-full h-1.5" value={job.progress} max="100"></progress>
      </div>
    {/if}

    {#if job.error}
      <p class="text-xs text-error/80 truncate" title={job.error}>{job.error}</p>
    {/if}

    {#if job.result}
      <div class="flex gap-1.5 flex-wrap">
        {#if job.result && job.result.audio?.success}
          <span class="badge badge-success/20 badge-xs text-success gap-1">
            <svg xmlns="http://www.w3.org/2000/svg" class="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="m4.5 12.75 6 6 9-13.5" /></svg>
            audio
          </span>
        {/if}
        {#if job.result && job.result.video?.success}
          <span class="badge badge-success/20 badge-xs text-success gap-1">
            <svg xmlns="http://www.w3.org/2000/svg" class="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="m4.5 12.75 6 6 9-13.5" /></svg>
            video
          </span>
        {/if}
        {#if job.result && job.result.cleanup?.success}
          <span class="badge badge-success/20 badge-xs text-success gap-1">
            <svg xmlns="http://www.w3.org/2000/svg" class="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="m4.5 12.75 6 6 9-13.5" /></svg>
            cleanup
          </span>
        {/if}
      </div>
    {/if}
  </div>
</div>
