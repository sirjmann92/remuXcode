<script lang="ts">
import { cancelJob, deleteJob } from '$lib/api';
import { channelLabel } from '$lib/format';
import type { Job } from '$lib/types';
import StatusBadge from './StatusBadge.svelte';

interface Props {
  job: Job;
  onRemoved?: () => void;
  detailed?: boolean;
}

const { job, onRemoved, detailed = false }: Props = $props();
let deleting = $state(false);
let cancelling = $state(false);

const fileName = $derived(job.file_path.split('/').pop() ?? job.file_path);
const libraryLink = $derived.by(() => {
  const isShow = /\/Season \d+\//i.test(job.file_path);
  const page = isShow ? '/shows' : '/movies';
  return `${page}?file=${encodeURIComponent(job.file_path)}`;
});
const elapsed = $derived.by(() => {
  if (!job.started_at) return null;
  const end = job.completed_at ?? Date.now() / 1000;
  const secs = Math.floor(end - job.started_at);
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
});

function codecLabel(codec: string): string {
  const map: Record<string, string> = {
    ac3: 'AC3',
    eac3: 'E-AC3',
    aac: 'AAC',
    dts: 'DTS',
    truehd: 'TrueHD',
  };
  return map[codec.toLowerCase()] ?? codec.toUpperCase();
}

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

async function handleCancel() {
  cancelling = true;
  try {
    await cancelJob(job.id);
    onRemoved?.();
  } catch {
    // ignore
  } finally {
    cancelling = false;
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
        {#if elapsed && job.status !== 'running'}
          <span class="text-xs text-base-content/30">{elapsed}</span>
        {/if}
      </div>
      <div class="flex items-center gap-0.5">
        <a
          href={libraryLink}
          class="btn btn-ghost btn-xs text-primary/60 hover:text-primary transition-opacity"
          title="View in library"
        >
          <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M13.5 6H5.25A2.25 2.25 0 0 0 3 8.25v10.5A2.25 2.25 0 0 0 5.25 21h10.5A2.25 2.25 0 0 0 18 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25" />
          </svg>
        </a>
        {#if job.status === 'running' || job.status === 'pending'}
          <button
            class="btn btn-ghost btn-xs text-warning opacity-60 hover:opacity-100 transition-opacity"
            onclick={handleCancel}
            disabled={cancelling}
            title="Cancel"
          >
            {#if cancelling}
              <span class="loading loading-spinner loading-xs"></span>
            {:else}
              <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                <path stroke-linecap="round" stroke-linejoin="round" d="M5.25 7.5A2.25 2.25 0 0 1 7.5 5.25h9a2.25 2.25 0 0 1 2.25 2.25v9a2.25 2.25 0 0 1-2.25 2.25h-9a2.25 2.25 0 0 1-2.25-2.25v-9Z" />
              </svg>
            {/if}
          </button>
        {:else if job.status === 'completed' || job.status === 'failed' || job.status === 'cancelled'}
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
      {#if detailed}
        <!-- Detailed view for Jobs page -->
        <div class="space-y-1.5 pt-1">
          {#if job.result.audio?.success}
            <div class="flex items-start gap-2">
              <span class="badge badge-warning badge-xs gap-0.5 shrink-0 mt-0.5">
                <svg xmlns="http://www.w3.org/2000/svg" class="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="m4.5 12.75 6 6 9-13.5" /></svg>
                Audio
              </span>
              <div class="text-xs text-base-content/60">
                {#if job.result.audio.converted_streams?.length}
                  {#each job.result.audio.converted_streams as stream, i}
                    <span>{codecLabel(stream.from_codec)} {channelLabel(stream.channels)} → {codecLabel(stream.to_codec)} {channelLabel(stream.channels)}</span>
                    {#if i < (job.result.audio.converted_streams?.length ?? 0) - 1}<span class="text-base-content/30"> · </span>{/if}
                  {/each}
                {:else}
                  {job.result.audio.streams_converted} stream{(job.result.audio.streams_converted ?? 0) !== 1 ? 's' : ''} converted
                {/if}
              </div>
            </div>
          {/if}
          {#if job.result.video?.success}
            <div class="flex items-start gap-2">
              <span class="badge badge-secondary badge-xs gap-0.5 shrink-0 mt-0.5">
                <svg xmlns="http://www.w3.org/2000/svg" class="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="m4.5 12.75 6 6 9-13.5" /></svg>
                Video
              </span>
              <span class="text-xs text-base-content/60">
                {job.result.video.codec_from} → {job.result.video.codec_to}
                {#if job.result.video.content_type}
                  <span class="text-base-content/30">({job.result.video.content_type})</span>
                {/if}
                {#if job.result.video.size_change_percent != null}
                  <span class="text-base-content/30">({job.result.video.size_change_percent > 0 ? '+' : ''}{job.result.video.size_change_percent.toFixed(0)}%)</span>
                {/if}
              </span>
            </div>
          {/if}
          {#if job.result.cleanup?.success}
            <div class="flex items-start gap-2">
              <span class="badge badge-info badge-xs gap-0.5 shrink-0 mt-0.5">
                <svg xmlns="http://www.w3.org/2000/svg" class="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="m4.5 12.75 6 6 9-13.5" /></svg>
                Cleanup
              </span>
              <span class="text-xs text-base-content/60">
                {#if job.result.cleanup.subtitle_removed > 0 || job.result.cleanup.audio_removed > 0}
                  {#if job.result.cleanup.subtitle_removed > 0}
                    Removed {job.result.cleanup.subtitle_removed} sub{job.result.cleanup.subtitle_removed !== 1 ? 's' : ''}
                    {#if job.result.cleanup.subtitle_kept}<span class="text-base-content/30">(kept {job.result.cleanup.subtitle_kept})</span>{/if}
                  {/if}
                  {#if job.result.cleanup.subtitle_removed > 0 && job.result.cleanup.audio_removed > 0}
                    <span class="text-base-content/30"> · </span>
                  {/if}
                  {#if job.result.cleanup.audio_removed > 0}
                    Removed {job.result.cleanup.audio_removed} audio
                    {#if job.result.cleanup.audio_kept}<span class="text-base-content/30">(kept {job.result.cleanup.audio_kept})</span>{/if}
                  {/if}
                {:else}
                  No streams removed
                {/if}
              </span>
            </div>
          {/if}
        </div>
      {:else}
        <!-- Compact badges for Dashboard -->
        <div class="flex gap-1.5 flex-wrap">
          {#if job.result.audio?.success}
            <span class="badge badge-warning badge-xs gap-0.5">
              <svg xmlns="http://www.w3.org/2000/svg" class="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="m4.5 12.75 6 6 9-13.5" /></svg>
              Audio
            </span>
          {/if}
          {#if job.result.video?.success}
            <span class="badge badge-secondary badge-xs gap-0.5">
              <svg xmlns="http://www.w3.org/2000/svg" class="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="m4.5 12.75 6 6 9-13.5" /></svg>
              Video
            </span>
          {/if}
          {#if job.result.cleanup?.success}
            <span class="badge badge-info badge-xs gap-0.5">
              <svg xmlns="http://www.w3.org/2000/svg" class="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="m4.5 12.75 6 6 9-13.5" /></svg>
              Cleanup
            </span>
          {/if}
        </div>
      {/if}
    {/if}
  </div>
</div>
