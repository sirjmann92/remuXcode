<script lang="ts">
import { analyzeFile, getActiveJobs, removeCoverArt, retagFile } from '$lib/api';
import { channelLabel, videoCodecLabel } from '$lib/format';
import { langLabel, langNames } from '$lib/languages';
import type { AnalyzeResult, RetagOverride } from '$lib/types';
import ConvertOptionsModal from './ConvertOptionsModal.svelte';

interface Props {
  path: string;
  poster_url?: string;
  media_type?: string;
  radarr_movie_id?: number;
  sonarr_episode_file_id?: number;
  onclose: () => void;
}

const { path, poster_url, media_type, radarr_movie_id, sonarr_episode_file_id, onclose }: Props =
  $props();

let result: AnalyzeResult | null = $state(null);
let loading = $state(true);
let error = $state('');
let activeTab: 'video' | 'audio' | 'subtitles' | 'general' = $state('general');
let showCustomEncode = $state(false);

// Retag state
let audioEdits = $state<Array<{ language: string; title: string }>>([]);
let subEdits = $state<Array<{ language: string; title: string }>>([]);
let retagLoading = $state(false);
let retagError = $state('');
let retagSuccess = $state(false);

// Cover art removal state
let removingIndices = $state(new Set<number>());
let removeCoverArtError = $state('');
let activeJobPaths = $state<Set<string>>(new Set());

// Cover art lightbox
let lightboxSrc = $state<string | null>(null);

const langOptions = Object.entries(langNames).sort(([, a], [, b]) => a.localeCompare(b));

$effect(() => {
  loading = true;
  error = '';
  Promise.all([
    analyzeFile(path, radarr_movie_id, sonarr_episode_file_id),
    getActiveJobs().catch(() => ({}) as Record<string, unknown>),
  ])
    .then(([r, jobs]) => {
      result = r;
      activeJobPaths = new Set(Object.keys(jobs));
      audioEdits = r.audio_streams.map((a) => ({
        language: a.language ?? '',
        title: a.title ?? '',
      }));
      subEdits = r.subtitle_streams.map((s) => ({
        language: s.language ?? '',
        title: s.title ?? '',
      }));
    })
    .catch((e) => {
      error = e.message || 'Analysis failed';
    })
    .finally(() => {
      loading = false;
    });
});

const pendingOverrides = $derived.by((): RetagOverride[] => {
  if (!result) return [];
  const overrides: RetagOverride[] = [];
  result.audio_streams.forEach((a, i) => {
    const edit = audioEdits[i];
    if (!edit) return;
    const ov: RetagOverride = { track_type: 'audio', track_index: i };
    let changed = false;
    if (edit.language !== (a.language ?? '')) {
      ov.language = edit.language || undefined;
      changed = true;
    }
    if (edit.title !== (a.title ?? '')) {
      ov.title = edit.title;
      changed = true;
    }
    if (changed) overrides.push(ov);
  });
  result.subtitle_streams.forEach((s, i) => {
    const edit = subEdits[i];
    if (!edit) return;
    const ov: RetagOverride = { track_type: 'subtitle', track_index: i };
    let changed = false;
    if (edit.language !== (s.language ?? '')) {
      ov.language = edit.language || undefined;
      changed = true;
    }
    if (edit.title !== (s.title ?? '')) {
      ov.title = edit.title;
      changed = true;
    }
    if (changed) overrides.push(ov);
  });
  return overrides;
});

async function handleRetag() {
  if (!pendingOverrides.length) return;
  retagLoading = true;
  retagError = '';
  retagSuccess = false;
  try {
    await retagFile(path, pendingOverrides);
    retagSuccess = true;
    setTimeout(() => onclose(), 1500);
  } catch (e: unknown) {
    retagError = e instanceof Error ? e.message : 'Retag failed';
  } finally {
    retagLoading = false;
  }
}

function formatDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function formatSize(bytes: number): string {
  if (bytes > 1e9) return `${(bytes / 1e9).toFixed(2)} GB`;
  if (bytes > 1e6) return `${(bytes / 1e6).toFixed(1)} MB`;
  if (bytes > 1e3) return `${(bytes / 1e3).toFixed(0)} KB`;
  return `${bytes} B`;
}

function formatBitrate(bps: number | null): string {
  if (!bps) return '—';
  if (bps > 1e6) return `${(bps / 1e6).toFixed(1)} Mbps`;
  if (bps > 1e3) return `${(bps / 1e3).toFixed(0)} kbps`;
  return `${bps} bps`;
}

function formatFrameRate(rate: string): string {
  if (!rate || rate === '0/1') return '—';
  const parts = rate.split('/');
  if (parts.length === 2) {
    const fps = parseInt(parts[0], 10) / parseInt(parts[1], 10);
    return `${fps.toFixed(3)} fps`;
  }
  return rate;
}

function fileName(path: string): string {
  return path.split('/').pop() ?? path;
}

function hdrLabel(v: import('$lib/types').AnalyzeVideoStream): string {
  const parts: string[] = [];
  if (v.is_dolby_vision) parts.push('Dolby Vision');
  if (v.is_hdr10_plus) parts.push('HDR10+');
  else if (v.is_hdr10) parts.push('HDR10');
  if (v.is_hlg) parts.push('HLG');
  return parts.join(' + ');
}

async function handleRemoveCoverArt(streamIndex: number) {
  if (!confirm('Remove this cover art image? This cannot be undone.')) return;
  removingIndices = new Set([...removingIndices, streamIndex]);
  removeCoverArtError = '';
  try {
    await removeCoverArt(path, streamIndex);
    // Re-fetch analysis to reflect the change
    const [r, jobs] = await Promise.all([
      analyzeFile(path, radarr_movie_id, sonarr_episode_file_id),
      getActiveJobs().catch(() => ({}) as Record<string, unknown>),
    ]);
    result = r;
    activeJobPaths = new Set(Object.keys(jobs));
    audioEdits = r.audio_streams.map((a) => ({ language: a.language ?? '', title: a.title ?? '' }));
    subEdits = r.subtitle_streams.map((s) => ({
      language: s.language ?? '',
      title: s.title ?? '',
    }));
  } catch (e) {
    removeCoverArtError = e instanceof Error ? e.message : 'Failed to remove cover art';
  } finally {
    removingIndices = new Set([...removingIndices].filter((i) => i !== streamIndex));
  }
}
</script>

<div class="modal modal-open" role="dialog" aria-modal="true">
  <div class="modal-box max-w-2xl">
    <button class="btn btn-sm btn-circle btn-ghost absolute right-2 top-2" onclick={onclose}>✕</button>

    <h3 class="text-lg font-semibold mb-1">File Analysis</h3>
    <p class="text-xs text-base-content/40 truncate mb-4" title={path}>{fileName(path)}</p>

    {#if loading}
      <div class="flex justify-center py-12">
        <span class="loading loading-spinner loading-lg"></span>
      </div>
    {:else if error}
      <div class="alert alert-error">
        <span>{error}</span>
        {#if error.toLowerCase().includes('not found')}
          <p class="text-xs mt-1 opacity-70">The file may have been moved, deleted, or is still downloading. Try refreshing the library in Radarr/Sonarr.</p>
        {/if}
      </div>
    {:else if result}
      <!-- Tabs -->
      <div class="tabs tabs-bordered mb-4">
        <button class="tab {activeTab === 'general' ? 'tab-active' : ''}" onclick={() => activeTab = 'general'}>General</button>
        <button class="tab {activeTab === 'video' ? 'tab-active' : ''}" onclick={() => activeTab = 'video'}>
          Video <span class="text-xs text-base-content/40 ml-1">({result.video_streams.length})</span>
        </button>
        <button class="tab {activeTab === 'audio' ? 'tab-active' : ''}" onclick={() => activeTab = 'audio'}>
          Audio <span class="text-xs text-base-content/40 ml-1">({result.audio_streams.length})</span>
        </button>
        <button class="tab {activeTab === 'subtitles' ? 'tab-active' : ''}" onclick={() => activeTab = 'subtitles'}>
          Subs <span class="text-xs text-base-content/40 ml-1">({result.subtitle_streams.length})</span>
        </button>
      </div>

      <!-- Tab content (fixed height with scroll prevents resize when switching tabs) -->
      <div class="h-[24rem] overflow-y-auto">

      <!-- General -->
      {#if activeTab === 'general'}
        <div class="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
          <div class="text-base-content/50">Format</div>
          <div>{result.format}</div>
          <div class="text-base-content/50">Duration</div>
          <div>{formatDuration(result.duration)}</div>
          <div class="text-base-content/50">Size</div>
          <div>{formatSize(result.size)}</div>
          <div class="text-base-content/50">Bitrate</div>
          <div>{formatBitrate(result.bitrate)}</div>
          <div class="text-base-content/50">Chapters</div>
          <div>{result.chapters}</div>
          <div class="text-base-content/50">Content Type</div>
          <div class="flex items-center gap-2">
            {result.content_type}
            {#if result.is_anime}
              <span class="badge badge-secondary badge-xs">Anime</span>
            {/if}
          </div>
          <div class="text-base-content/50">Streams</div>
          <div>{result.video_streams.length}V / {result.audio_streams.length}A / {result.subtitle_streams.length}S</div>
        </div>

        {#if result.needs_audio_conversion || result.needs_video_conversion || result.needs_cleanup}
          <div class="divider text-xs text-base-content/30">Work Needed</div>
          <div class="flex flex-wrap gap-2">
            {#if result.audio_codecs_to_convert?.length}
              <span class="badge badge-warning badge-sm">{result.audio_codecs_to_convert.join(', ')} will be converted</span>
            {/if}
            {#if result.audio_codecs_to_drop?.length}
              <span class="badge badge-warning badge-sm">{result.audio_codecs_to_drop.join(', ')} will be removed</span>
            {/if}
            {#if result.needs_audio_conversion && !result.audio_codecs_to_convert?.length && !result.audio_codecs_to_drop?.length}
              <span class="badge badge-warning badge-sm">Audio conversion needed</span>
            {/if}
            {#if result.needs_video_conversion}
              <span class="badge badge-warning badge-sm">{videoCodecLabel(result.video_streams[0]?.codec ?? '')} will be re-encoded</span>
            {/if}
            {#if result.needs_cleanup}
              <span class="badge badge-info badge-sm" title={result.subtitle_langs_to_remove?.join(', ')}>Subtitles will be cleaned</span>
            {/if}
          </div>
        {/if}

        {#if Object.keys(result.format_tags).length > 0}
          <div class="divider text-xs text-base-content/30">Container Tags</div>
          <div class="overflow-x-auto">
            <table class="table table-xs">
              <tbody>
                {#each Object.entries(result.format_tags) as [key, value]}
                  <tr>
                    <td class="text-base-content/50 font-mono text-xs w-40">{key}</td>
                    <td class="text-xs break-all">{value}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        {/if}

      <!-- Video Streams -->
      {:else if activeTab === 'video'}
        {#if result.video_streams.length === 0}
          <p class="text-sm text-base-content/40 py-4 text-center">No video streams</p>
        {:else}
          <div class="space-y-3">
            {#each result.video_streams as v, i}
              {#if v.is_attached_pic}
                <div class="card-glass rounded-box p-3">
                  <div class="flex items-center justify-between mb-2">
                    <span class="font-medium text-sm">Stream #{v.index}</span>
                    <span class="badge badge-secondary badge-sm">Cover Art</span>
                  </div>
                  <div class="flex items-center gap-3">
                    <button
                      class="cursor-zoom-in shrink-0"
                      title="Click to enlarge"
                      onclick={() => lightboxSrc = `/api/cover-art?path=${encodeURIComponent(path)}&index=${v.index}`}
                    >
                      <img
                        src="/api/cover-art?path={encodeURIComponent(path)}&index={v.index}"
                        class="w-16 h-16 object-contain rounded border border-base-content/10 hover:opacity-80 transition-opacity"
                        alt="Cover art"
                      />
                    </button>
                    <div class="flex-1 text-xs text-base-content/50 space-y-1">
                      <div>{v.codec_long || v.codec}</div>
                      <button
                        class="btn btn-xs btn-error btn-outline mt-1"
                        disabled={removingIndices.has(v.index) || activeJobPaths.has(path)}
                        onclick={() => handleRemoveCoverArt(v.index)}
                      >
                        {#if removingIndices.has(v.index)}Removing…{:else}Remove{/if}
                      </button>
                    </div>
                  </div>
                </div>
              {:else}
              <div class="card-glass rounded-box p-3">
                <div class="flex items-center justify-between mb-2">
                  <span class="font-medium text-sm">Stream #{v.index}</span>
                  <div class="flex gap-1 flex-wrap justify-end">
                    {#if v.is_hevc}<span class="badge badge-success badge-xs">HEVC</span>{/if}
                    {#if v.is_h264}<span class="badge badge-ghost badge-xs">H.264</span>{/if}
                    {#if v.is_dolby_vision}<span class="badge badge-secondary badge-xs">DV</span>{/if}
                    {#if v.is_hdr10_plus}<span class="badge badge-warning badge-xs">HDR10+</span>
                    {:else if v.is_hdr10}<span class="badge badge-warning badge-xs">HDR10</span>{/if}
                    {#if v.is_hlg}<span class="badge badge-info badge-xs">HLG</span>{/if}
                  </div>
                </div>
                <div class="grid grid-cols-2 gap-x-6 gap-y-1 text-xs">
                  <div class="text-base-content/50">Codec</div>
                  <div>{v.codec_long || v.codec}</div>
                  <div class="text-base-content/50">Resolution</div>
                  <div>{v.resolution}</div>
                  <div class="text-base-content/50">Profile</div>
                  <div>{v.profile ?? '—'}</div>
                  {#if hdrLabel(v)}
                    <div class="text-base-content/50">HDR</div>
                    <div>{hdrLabel(v)}</div>
                  {/if}
                  <div class="text-base-content/50">Pixel Format</div>
                  <div>{v.pix_fmt} ({v.bit_depth}-bit)</div>
                  <div class="text-base-content/50">Frame Rate</div>
                  <div>{formatFrameRate(v.frame_rate)}</div>
                  <div class="text-base-content/50">Bitrate</div>
                  <div>{formatBitrate(v.bitrate)}</div>
                </div>
              </div>
              {/if}
            {/each}
            {#if removeCoverArtError}
              <div class="alert alert-error text-xs py-2 px-3">{removeCoverArtError}</div>
            {/if}
          </div>
        {/if}

      <!-- Audio Streams -->
      {:else if activeTab === 'audio'}
        {#if result.audio_streams.length === 0}
          <p class="text-sm text-base-content/40 py-4 text-center">No audio streams</p>
        {:else}
          <div class="space-y-3">
            {#each result.audio_streams as a, i}
              <div class="card-glass rounded-box p-3">
                <div class="flex items-center justify-between mb-2">
                  <div class="flex items-center gap-2">
                    <span class="font-medium text-sm">Stream #{a.index}</span>
                    <span class="text-xs text-base-content/40">{langLabel(a.language)}</span>
                  </div>
                  <div class="flex gap-1">
                    {#if a.is_default}<span class="badge badge-ghost badge-xs">Default</span>{/if}
                    {#if a.is_dts}<span class="badge badge-warning badge-xs">DTS</span>{/if}
                    {#if a.is_truehd}<span class="badge badge-warning badge-xs">TrueHD</span>{/if}
                    {#if a.is_lossless}<span class="badge badge-info badge-xs">Lossless</span>{/if}
                  </div>
                </div>
                <div class="grid grid-cols-2 gap-x-6 gap-y-1 text-xs">
                  <div class="text-base-content/50">Codec</div>
                  <div>{a.codec_long || a.codec}</div>
                  <div class="text-base-content/50">Channels</div>
                  <div>{channelLabel(a.channels, a.channel_layout)}</div>
                  <div class="text-base-content/50">Sample Rate</div>
                  <div>{(a.sample_rate / 1000).toFixed(1)} kHz</div>
                  <div class="text-base-content/50">Bitrate</div>
                  <div>{formatBitrate(a.bitrate)}</div>
                  {#if a.title}
                    <div class="text-base-content/50">Title</div>
                    <div>{a.title}</div>
                  {/if}
                </div>
                {#if audioEdits[i]}
                  <div class="flex items-center gap-2 mt-2 pt-2 border-t border-base-content/10">
                    <span class="text-xs text-base-content/40 shrink-0">Fix:</span>
                    <select
                      class="select select-xs flex-1 min-w-0"
                      bind:value={audioEdits[i].language}
                    >
                      <option value="">Unknown</option>
                      {#each langOptions as [code, name]}
                        <option value={code}>{name}</option>
                      {/each}
                    </select>
                    <input
                      type="text"
                      class="input input-xs flex-1 min-w-0"
                      placeholder="Title"
                      bind:value={audioEdits[i].title}
                    />
                  </div>
                {/if}
              </div>
            {/each}
          </div>
        {/if}

      <!-- Subtitle Streams -->
      {:else if activeTab === 'subtitles'}
        {#if result.subtitle_streams.length === 0}
          <p class="text-sm text-base-content/40 py-4 text-center">No subtitle streams</p>
        {:else}
          <div class="overflow-x-auto">
            <table class="table table-xs">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Language</th>
                  <th>Codec</th>
                  <th>Title</th>
                  <th>Flags</th>
                </tr>
              </thead>
              <tbody>
                {#each result.subtitle_streams as s, i}
                  <tr>
                    <td>{s.index}</td>
                    <td>
                      {#if subEdits[i]}
                        <select class="select select-xs w-28" bind:value={subEdits[i].language}>
                          <option value="">Unknown</option>
                          {#each langOptions as [code, name]}
                            <option value={code}>{name}</option>
                          {/each}
                        </select>
                      {:else}
                        {langLabel(s.language)}
                      {/if}
                    </td>
                    <td class="font-mono text-xs">{s.codec}</td>
                    <td>
                      {#if subEdits[i]}
                        <input
                          type="text"
                          class="input input-xs w-36"
                          placeholder="Title"
                          bind:value={subEdits[i].title}
                        />
                      {:else}
                        <span class="max-w-48 truncate" title={s.title ?? ''}>{s.title ?? '—'}</span>
                      {/if}
                    </td>
                    <td>
                      <div class="flex gap-1">
                        {#if s.is_default}<span class="badge badge-ghost badge-xs">Default</span>{/if}
                        {#if s.is_forced}<span class="badge badge-accent badge-xs">Forced</span>{/if}
                        {#if s.is_sdh}<span class="badge badge-info badge-xs">SDH</span>{/if}
                      </div>
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        {/if}
      {/if}
      </div><!-- end min-h tab content -->
    {/if}

    <div class="modal-action">
      {#if retagSuccess}
        <span class="text-success text-sm self-center">✓ Metadata updated!</span>
      {:else if retagError}
        <span class="text-error text-xs self-center max-w-48 truncate" title={retagError}>{retagError}</span>
      {/if}
      {#if pendingOverrides.length > 0}
        <button
          class="btn btn-warning btn-sm"
          onclick={handleRetag}
          disabled={retagLoading || retagSuccess}
        >
          {#if retagLoading}
            <span class="loading loading-spinner loading-xs"></span>
          {:else}
            Fix Metadata ({pendingOverrides.length})
          {/if}
        </button>
      {/if}
      <button class="btn btn-ghost btn-sm" onclick={() => (showCustomEncode = true)}>
        Custom Encode
      </button>
      <button class="btn btn-sm" onclick={onclose}>Close</button>
    </div>
  </div>
  <div class="modal-backdrop" role="button" tabindex="-1" aria-label="Close" onclick={onclose} onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') onclose(); }}></div>
</div>

{#if showCustomEncode}
  <ConvertOptionsModal
    paths={path}
    {poster_url}
    {media_type}
    onclose={() => (showCustomEncode = false)}
    onqueued={() => { showCustomEncode = false; onclose(); }}
  />
{/if}

{#if lightboxSrc}
  <div
    class="fixed inset-0 z-[9999] flex items-center justify-center bg-black/80 p-4"
    role="button"
    tabindex="-1"
    aria-label="Close image preview"
    onclick={() => lightboxSrc = null}
    onkeydown={(e) => { if (e.key === 'Escape' || e.key === 'Enter') lightboxSrc = null; }}
  >
    <img
      src={lightboxSrc}
      class="max-w-full max-h-full object-contain rounded shadow-2xl"
      alt="Cover art full size"
    />
  </div>
{/if}
