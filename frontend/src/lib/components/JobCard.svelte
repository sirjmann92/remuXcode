<script lang="ts">
import { cancelJob, deleteJob } from '$lib/api';
import { channelLabel, formatSize, formatTimestamp } from '$lib/format';
import type { Job, JobPhase } from '$lib/types';
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

function sizeDetail(original?: number, newSize?: number): string | null {
  if (!original || !newSize || original === 0) return null;
  const pct = ((newSize - original) / original) * 100;
  return `${formatSize(original)} → ${formatSize(newSize)} (${pct > 0 ? '+' : ''}${pct.toFixed(0)}%)`;
}

const phaseColors: Record<JobPhase, string> = {
  audio: 'badge-warning',
  video: 'badge-secondary',
  cleanup: 'badge-info',
};

const phaseLabels: Record<JobPhase, string> = {
  audio: 'Audio',
  video: 'Video',
  cleanup: 'Cleanup',
};

/** True when the job completed but no phases actually did any work. */
const noWorkNeeded = $derived(
  job.status === 'completed' &&
    job.result != null &&
    job.result.audio == null &&
    job.result.video == null &&
    job.result.cleanup == null,
);

/** Compute overall size reduction across all completed phases. */
const overallSize = $derived.by(() => {
  const r = job.result;
  if (!r) return null;
  const phases = [r.audio, r.video, r.cleanup].filter(
    (p): p is NonNullable<typeof p> => p != null && p.success === true,
  );
  if (phases.length < 2) return null;
  const first = phases.find((p) => p.original_size != null && p.original_size > 0);
  const last = [...phases].reverse().find((p) => p.new_size != null && p.new_size > 0);
  if (!first?.original_size || !last?.new_size) return null;
  if (first.original_size === last.new_size) return null;
  const pct = ((last.new_size - first.original_size) / first.original_size) * 100;
  return `${formatSize(first.original_size)} → ${formatSize(last.new_size)} (${pct > 0 ? '+' : ''}${pct.toFixed(0)}%)`;
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
  <div class="flex">
    {#if job.poster_url}
      <div class="shrink-0 {job.status === 'running' ? 'w-16' : 'w-12'} overflow-hidden rounded-l-box">
        <img
          src={job.poster_url}
          alt=""
          class="h-full w-full object-cover"
          loading="lazy"
        />
      </div>
    {/if}
  <div class="flex-1 min-w-0 p-4 space-y-2">
    <div class="flex items-center justify-between gap-2">
      <div class="flex items-center gap-2 min-w-0">
        <StatusBadge status={job.status} />
        <span class="badge badge-outline badge-xs text-base-content/40">{job.job_type}</span>
        <span class="badge badge-outline badge-xs text-base-content/40">{job.source}</span>
        {#if noWorkNeeded}
          <span class="badge badge-ghost badge-xs text-base-content/40 gap-0.5">
            <svg xmlns="http://www.w3.org/2000/svg" class="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="m4.5 12.75 6 6 9-13.5" /></svg>
            No work needed
          </span>
        {/if}
        {#if elapsed && job.status !== 'running'}
          <span class="text-xs text-base-content/30">{elapsed}</span>
        {/if}
        {#if formatTimestamp(job.started_at) && job.status !== 'pending'}
          <span class="text-xs text-base-content/25" title="Started">{formatTimestamp(job.started_at)}</span>
        {/if}
        {#if formatTimestamp(job.completed_at)}
          <span class="text-xs text-base-content/25">→ {formatTimestamp(job.completed_at)}</span>
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

    {#if job.planned_phases?.length && (job.status === 'running' || job.status === 'completed' || job.status === 'failed')}
      {#if detailed}
        <div class="space-y-1.5">
          {#each job.planned_phases as phase}
            {@const isDone = job.completed_phases?.includes(phase) || job.status !== 'running'}
            {@const isCurrent = job.status === 'running' && job.current_phase === phase && !job.completed_phases?.includes(phase)}
            {@const phaseResult = phase === 'audio' ? job.result?.audio : phase === 'video' ? job.result?.video : job.result?.cleanup}
            {@const phaseSucceeded = isDone && phaseResult?.success === true}
            {@const phaseFailed = isDone && phaseResult != null && phaseResult.success === false}
            <div class="flex items-center gap-2">
              <span class="badge {phaseFailed ? 'badge-error' : phaseColors[phase]} badge-xs gap-0.5 shrink-0 {isCurrent ? '' : isDone ? 'opacity-80' : 'opacity-30'}">
                {#if phaseSucceeded}
                  <svg xmlns="http://www.w3.org/2000/svg" class="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="m4.5 12.75 6 6 9-13.5" /></svg>
                {:else if phaseFailed}
                  <svg xmlns="http://www.w3.org/2000/svg" class="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18 18 6M6 6l12 12" /></svg>
                {:else if isCurrent}
                  <span class="loading loading-spinner" style="width: 0.625rem; height: 0.625rem;"></span>
                {/if}
                {phaseLabels[phase]}
              </span>
              {#if phaseSucceeded && phase === 'audio' && job.result?.audio}
                <div class="text-xs text-base-content/60">
                  {#if job.result.audio.converted_streams?.length}
                    {#each job.result.audio.converted_streams as stream, i}
                      <span>{codecLabel(stream.from_codec)} {channelLabel(stream.channels)} → {codecLabel(stream.to_codec)} {channelLabel(stream.channels)}</span>
                      {#if i < (job.result.audio.converted_streams?.length ?? 0) - 1}<span class="text-base-content/30"> · </span>{/if}
                    {/each}
                  {:else}
                    {job.result.audio.streams_converted} stream{(job.result.audio.streams_converted ?? 0) !== 1 ? 's' : ''} converted
                  {/if}
                  {#if (job.result.audio.streams_dropped ?? 0) > 0}
                    <span class="text-base-content/30"> · </span>Dropped {job.result.audio.streams_dropped} redundant
                  {/if}
                  {#if sizeDetail(job.result.audio.original_size, job.result.audio.new_size)}
                    <span class="text-base-content/30"> · {sizeDetail(job.result.audio.original_size, job.result.audio.new_size)}</span>
                  {/if}
                </div>
              {:else if phaseSucceeded && phase === 'video' && job.result?.video}
                <span class="text-xs text-base-content/60">
                  {job.result.video.codec_from} → {job.result.video.codec_to}
                  {#if job.result.video.content_type}
                    <span class="text-base-content/30">({job.result.video.content_type})</span>
                  {/if}
                  {#if sizeDetail(job.result.video.original_size, job.result.video.new_size)}
                    <span class="text-base-content/30"> · {sizeDetail(job.result.video.original_size, job.result.video.new_size)}</span>
                  {/if}
                </span>
              {:else if phaseSucceeded && phase === 'cleanup' && job.result?.cleanup}
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
                  {#if sizeDetail(job.result.cleanup.original_size, job.result.cleanup.new_size)}
                    <span class="text-base-content/30"> · {sizeDetail(job.result.cleanup.original_size, job.result.cleanup.new_size)}</span>
                  {/if}
                </span>
              {/if}
              {#if phaseFailed && phaseResult?.error}
                <span class="text-xs text-error/70 truncate" title={phaseResult.error}>{phaseResult.error}</span>
              {/if}
            </div>
          {/each}
        </div>
      {:else}
        <div class="flex gap-1.5 flex-wrap items-center">
          {#each job.planned_phases as phase}
            {@const isDone = job.completed_phases?.includes(phase) || job.status !== 'running'}
            {@const isCurrent = job.status === 'running' && job.current_phase === phase && !job.completed_phases?.includes(phase)}
            {@const phaseResult = phase === 'audio' ? job.result?.audio : phase === 'video' ? job.result?.video : job.result?.cleanup}
            {@const phaseSucceeded = isDone && phaseResult?.success === true}
            {@const phaseFailed = isDone && phaseResult != null && phaseResult.success === false}
            <span class="badge {phaseFailed ? 'badge-error' : phaseColors[phase]} badge-xs gap-0.5 {isCurrent ? '' : isDone ? 'opacity-80' : 'opacity-30'}">
              {#if phaseSucceeded}
                <svg xmlns="http://www.w3.org/2000/svg" class="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="m4.5 12.75 6 6 9-13.5" /></svg>
              {:else if phaseFailed}
                <svg xmlns="http://www.w3.org/2000/svg" class="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18 18 6M6 6l12 12" /></svg>
              {:else if isCurrent}
                <span class="loading loading-spinner" style="width: 0.625rem; height: 0.625rem;"></span>
              {/if}
              {phaseLabels[phase]}
            </span>
          {/each}
        </div>
      {/if}
    {:else if job.result}
      <!-- Legacy jobs without planned_phases -->
      {#if detailed}
        <div class="space-y-1.5">
          {#if job.result.audio?.success}
            <div class="flex items-center gap-2">
              <span class="badge badge-warning badge-xs gap-0.5 shrink-0 opacity-80">
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
                {#if sizeDetail(job.result.audio.original_size, job.result.audio.new_size)}
                  <span class="text-base-content/30"> · {sizeDetail(job.result.audio.original_size, job.result.audio.new_size)}</span>
                {/if}
              </div>
            </div>
          {/if}
          {#if job.result.video?.success}
            <div class="flex items-center gap-2">
              <span class="badge badge-secondary badge-xs gap-0.5 shrink-0 opacity-80">
                <svg xmlns="http://www.w3.org/2000/svg" class="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="m4.5 12.75 6 6 9-13.5" /></svg>
                Video
              </span>
              <span class="text-xs text-base-content/60">
                {job.result.video.codec_from} → {job.result.video.codec_to}
                {#if job.result.video.content_type}
                  <span class="text-base-content/30">({job.result.video.content_type})</span>
                {/if}
                {#if sizeDetail(job.result.video.original_size, job.result.video.new_size)}
                  <span class="text-base-content/30"> · {sizeDetail(job.result.video.original_size, job.result.video.new_size)}</span>
                {/if}
              </span>
            </div>
          {/if}
          {#if job.result.cleanup?.success}
            <div class="flex items-center gap-2">
              <span class="badge badge-info badge-xs gap-0.5 shrink-0 opacity-80">
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
                {#if sizeDetail(job.result.cleanup.original_size, job.result.cleanup.new_size)}
                  <span class="text-base-content/30"> · {sizeDetail(job.result.cleanup.original_size, job.result.cleanup.new_size)}</span>
                {/if}
              </span>
            </div>
          {/if}
          {#if job.result.audio && !job.result.audio.success}
            <div class="flex items-center gap-2">
              <span class="badge badge-error badge-xs gap-0.5 shrink-0 opacity-80">
                <svg xmlns="http://www.w3.org/2000/svg" class="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18 18 6M6 6l12 12" /></svg>
                Audio
              </span>
              {#if job.result.audio.error}
                <span class="text-xs text-error/70 truncate" title={job.result.audio.error}>{job.result.audio.error}</span>
              {/if}
            </div>
          {/if}
          {#if job.result.video && !job.result.video.success}
            <div class="flex items-center gap-2">
              <span class="badge badge-error badge-xs gap-0.5 shrink-0 opacity-80">
                <svg xmlns="http://www.w3.org/2000/svg" class="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18 18 6M6 6l12 12" /></svg>
                Video
              </span>
              {#if job.result.video.error}
                <span class="text-xs text-error/70 truncate" title={job.result.video.error}>{job.result.video.error}</span>
              {/if}
            </div>
          {/if}
          {#if job.result.cleanup && !job.result.cleanup.success}
            <div class="flex items-center gap-2">
              <span class="badge badge-error badge-xs gap-0.5 shrink-0 opacity-80">
                <svg xmlns="http://www.w3.org/2000/svg" class="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18 18 6M6 6l12 12" /></svg>
                Cleanup
              </span>
              {#if job.result.cleanup.error}
                <span class="text-xs text-error/70 truncate" title={job.result.cleanup.error}>{job.result.cleanup.error}</span>
              {/if}
            </div>
          {/if}
        </div>
      {:else}
        <div class="flex gap-1.5 flex-wrap">
          {#if job.result.audio?.success}
            <span class="badge badge-warning badge-xs gap-0.5">
              <svg xmlns="http://www.w3.org/2000/svg" class="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="m4.5 12.75 6 6 9-13.5" /></svg>
              Audio
            </span>
          {:else if job.result.audio && !job.result.audio.success}
            <span class="badge badge-error badge-xs gap-0.5">
              <svg xmlns="http://www.w3.org/2000/svg" class="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18 18 6M6 6l12 12" /></svg>
              Audio
            </span>
          {/if}
          {#if job.result.video?.success}
            <span class="badge badge-secondary badge-xs gap-0.5">
              <svg xmlns="http://www.w3.org/2000/svg" class="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="m4.5 12.75 6 6 9-13.5" /></svg>
              Video
            </span>
          {:else if job.result.video && !job.result.video.success}
            <span class="badge badge-error badge-xs gap-0.5">
              <svg xmlns="http://www.w3.org/2000/svg" class="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18 18 6M6 6l12 12" /></svg>
              Video
            </span>
          {/if}
          {#if job.result.cleanup?.success}
            <span class="badge badge-info badge-xs gap-0.5">
              <svg xmlns="http://www.w3.org/2000/svg" class="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="m4.5 12.75 6 6 9-13.5" /></svg>
              Cleanup
            </span>
          {:else if job.result.cleanup && !job.result.cleanup.success}
            <span class="badge badge-error badge-xs gap-0.5">
              <svg xmlns="http://www.w3.org/2000/svg" class="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18 18 6M6 6l12 12" /></svg>
              Cleanup
            </span>
          {/if}
        </div>
      {/if}
    {/if}

    {#if job.status === 'running'}
      <div class="space-y-1.5">
        {#if job.status_detail && detailed}
          <p class="text-xs text-base-content/50 italic truncate" title={job.status_detail}>{job.status_detail}</p>
        {/if}
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
      {@const isProtected = job.error.includes('original file preserved')}
      {@const isStale = job.error.includes('Stale job')}
      {#if isProtected}
        <div class="flex items-start gap-1.5 rounded-md bg-success/10 border border-success/20 px-2 py-1.5">
          <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5 shrink-0 mt-0.5 text-success" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M9 12.75 11.25 15 15 9.75m-3-7.036A11.959 11.959 0 0 1 3.598 6 11.99 11.99 0 0 0 3 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285Z" />
          </svg>
          <p class="text-xs text-success/90">{job.error}</p>
        </div>
      {:else if isStale}
        <div class="flex items-start gap-1.5 rounded-md bg-warning/10 border border-warning/20 px-2 py-1.5">
          <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5 shrink-0 mt-0.5 text-warning" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
          </svg>
          <p class="text-xs text-warning/90">{job.error}</p>
        </div>
      {:else}
        <p class="text-xs text-error/80">{job.error}</p>
      {/if}
    {/if}

    {#if overallSize}
      <div class="flex items-center gap-1.5 pt-1 border-t border-base-content/5">
        <span class="text-xs font-medium text-base-content/50">Total</span>
        <span class="text-xs text-base-content/40">{overallSize}</span>
      </div>
    {/if}
  </div>
  </div>
</div>
