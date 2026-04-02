<script lang="ts">
import { analyzeFile } from '$lib/api';
import { channelLabel } from '$lib/format';
import { langLabel } from '$lib/languages';
import type { AnalyzeResult } from '$lib/types';

interface Props {
  path: string;
  onclose: () => void;
}

const { path, onclose }: Props = $props();

let result: AnalyzeResult | null = $state(null);
let loading = $state(true);
let error = $state('');
let activeTab: 'video' | 'audio' | 'subtitles' | 'general' = $state('general');

$effect(() => {
  loading = true;
  error = '';
  analyzeFile(path)
    .then((r) => {
      result = r;
    })
    .catch((e) => {
      error = e.message || 'Analysis failed';
    })
    .finally(() => {
      loading = false;
    });
});

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

        {#if result.needs_audio_conversion || result.needs_video_conversion}
          <div class="divider text-xs text-base-content/30">Conversion Needed</div>
          <div class="flex flex-wrap gap-2">
            {#if result.needs_audio_conversion}
              <span class="badge badge-warning badge-sm">Audio conversion needed</span>
            {/if}
            {#if result.needs_video_conversion}
              <span class="badge badge-warning badge-sm">Video conversion needed</span>
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
              <div class="card-glass rounded-box p-3">
                <div class="flex items-center justify-between mb-2">
                  <span class="font-medium text-sm">Stream #{v.index}</span>
                  <div class="flex gap-1">
                    {#if v.is_hevc}<span class="badge badge-success badge-xs">HEVC</span>{/if}
                    {#if v.is_h264}<span class="badge badge-ghost badge-xs">H.264</span>{/if}
                  </div>
                </div>
                <div class="grid grid-cols-2 gap-x-6 gap-y-1 text-xs">
                  <div class="text-base-content/50">Codec</div>
                  <div>{v.codec_long || v.codec}</div>
                  <div class="text-base-content/50">Resolution</div>
                  <div>{v.resolution}</div>
                  <div class="text-base-content/50">Profile</div>
                  <div>{v.profile ?? '—'}</div>
                  <div class="text-base-content/50">Pixel Format</div>
                  <div>{v.pix_fmt} ({v.bit_depth}-bit)</div>
                  <div class="text-base-content/50">Frame Rate</div>
                  <div>{formatFrameRate(v.frame_rate)}</div>
                  <div class="text-base-content/50">Bitrate</div>
                  <div>{formatBitrate(v.bitrate)}</div>
                </div>
              </div>
            {/each}
          </div>
        {/if}

      <!-- Audio Streams -->
      {:else if activeTab === 'audio'}
        {#if result.audio_streams.length === 0}
          <p class="text-sm text-base-content/40 py-4 text-center">No audio streams</p>
        {:else}
          <div class="space-y-3">
            {#each result.audio_streams as a}
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
                {#each result.subtitle_streams as s}
                  <tr>
                    <td>{s.index}</td>
                    <td>{langLabel(s.language)}</td>
                    <td class="font-mono text-xs">{s.codec}</td>
                    <td class="max-w-48 truncate" title={s.title ?? ''}>{s.title ?? '—'}</td>
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
  </div>
  <div class="modal-backdrop" role="button" tabindex="-1" aria-label="Close" onclick={onclose} onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') onclose(); }}></div>
</div>
