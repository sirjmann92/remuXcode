<script lang="ts">
import { tick } from 'svelte';
import { goto } from '$app/navigation';
import { page } from '$app/stores';
import {
  convertFile,
  getActiveJobs,
  getConfig,
  getScanProgress,
  getSeries,
  getSeriesDetail,
  refreshSonarr,
  startSeriesScan,
  stopScan,
} from '$lib/api';
import {
  audioCodecMatches,
  buildAudioOptions,
  buildVideoOptions,
  videoCodecMatches,
} from '$lib/codecs';
import AnalyzeModal from '$lib/components/AnalyzeModal.svelte';
import ConvertOptionsModal from '$lib/components/ConvertOptionsModal.svelte';
import { formatSize, keptTracks, removableTracks, trackSummary } from '$lib/format';
import { langName } from '$lib/languages';
import { buildResolutionOptions, resolutionMatches } from '$lib/resolution';
import {
  deduplicatedSeriesFetch,
  getCachedSeries,
  invalidateSeries,
  setCachedSeries,
} from '$lib/stores';
import type {
  ActiveJobsMap,
  BrowseSeries,
  ConfigSummary,
  EpisodeFile,
  ScanProgress,
  Season,
  SeriesDetail,
} from '$lib/types';

let seriesList: BrowseSeries[] = $state([]);
let config: ConfigSummary | null = $state(null);
let loading = $state(true);
let loadError = $state(false);
let search = $state('');
let filter: string = $state('any');
let audioFormat: string = $state('any');
let videoFormat: string = $state('any');
let resolutionFilter: string = $state('any');
let sortBy: string = $state('needsWork');

// Detail view state
let selectedSeries: SeriesDetail | null = $state(null);
let detailLoading = $state(false);
let expandedSeasons: Record<number, boolean> = $state({});
let queueing: Record<string, boolean> = $state({});
let queueingAll = $state(false);
let selectedEps: Set<string> = $state(new Set());
let queueingSelected = $state(false);
let analyzePath: string | null = $state(null);
let customEncodePaths: string[] | null = $state(null);
let customEncodeLabel: string | undefined = $state(undefined);
let activeJobs: ActiveJobsMap = $state({});
let showRefreshConfirm = $state(false);
let refreshingLibrary = $state(false);
let refreshMsg = $state('');
let reloading = $state(false);
let rescanning = $state(false);
let scanProgress: ScanProgress | null = $state(null);
let scanPollTimer: ReturnType<typeof setInterval> | null = null;
let prevActiveKeys: Set<string> = new Set();
let jobPollTimer: ReturnType<typeof setInterval> | null = null;
let deepLinkFile: string | null = $page.url.searchParams.get('file');
let scrollY = $state(0);
let savedScrollY = 0;

$effect(() => {
  function onScroll() {
    scrollY = window.scrollY;
  }
  window.addEventListener('scroll', onScroll, { passive: true });
  return () => window.removeEventListener('scroll', onScroll);
});

/** Strip leading articles for Sonarr/Radarr-style title sorting. */
function sortTitle(title: string): string {
  return title.replace(/^(The|A|An)\s+/i, '').trim();
}

async function handleStartScan() {
  try {
    await startSeriesScan();
    pollScanProgress();
  } catch {
    /* ignore */
  }
}

async function handleStopScan() {
  try {
    await stopScan();
  } catch {
    /* ignore */
  }
}

function pollScanProgress() {
  if (scanPollTimer) return;
  const tick = async () => {
    try {
      scanProgress = await getScanProgress();
      if (!scanProgress.running) {
        if (scanPollTimer) clearInterval(scanPollTimer);
        scanPollTimer = null;
        invalidateSeries();
        fetchSeries();
      }
    } catch {
      /* ignore */
    }
  };
  tick();
  scanPollTimer = setInterval(tick, 2000);
}

getScanProgress()
  .then((p) => {
    scanProgress = p;
    if (p.running && p.type === 'series') pollScanProgress();
  })
  .catch(() => {});

function startJobPolling() {
  if (jobPollTimer) return;
  refreshActiveJobs();
  jobPollTimer = setInterval(refreshActiveJobs, 3000);
}

async function refreshActiveJobs() {
  try {
    const next = await getActiveJobs();
    const nextKeys = new Set(Object.keys(next));
    const finished = [...prevActiveKeys].some((k) => !nextKeys.has(k));
    prevActiveKeys = nextKeys;
    activeJobs = next;
    if (finished) {
      // Refresh list data; if viewing a series detail, refresh that too
      invalidateSeries();
      fetchSeries(); // Background refresh — stale data stays visible
      if (selectedSeries) {
        try {
          selectedSeries = await getSeriesDetail(selectedSeries.id);
        } catch {
          /* ignore */
        }
      }
    }
  } catch {
    /* ignore */
  }
}

function getJobStatus(path: string) {
  return activeJobs[path] ?? null;
}

function seriesActiveCount(seriesPath: string): number {
  return Object.keys(activeJobs).filter((p) => p.startsWith(seriesPath)).length;
}

function seasonActiveJobs(season: Season): number {
  return season.episodes.filter((ep) => activeJobs[ep.path]).length;
}

$effect(() => {
  startJobPolling();
  return () => {
    if (jobPollTimer) clearInterval(jobPollTimer);
    if (scanPollTimer) clearInterval(scanPollTimer);
  };
});

async function handleRefreshLibrary() {
  showRefreshConfirm = false;
  refreshingLibrary = true;
  refreshMsg = '';
  try {
    const res = await refreshSonarr();
    refreshMsg = res.message;
    setTimeout(() => {
      refreshMsg = '';
    }, 4000);
  } catch (e) {
    refreshMsg = e instanceof Error ? e.message : 'Sonarr refresh failed';
  } finally {
    refreshingLibrary = false;
  }
}

const analysisFiltersActive = $derived(audioFormat !== 'any' || videoFormat !== 'any');

const filtered = $derived.by(() => {
  let result = seriesList;

  // Primary filter
  if (filter && filter !== 'any') {
    result = result.filter((s) => {
      switch (filter) {
        case 'needs_conversion':
          return (
            (s.audio_convert_count ?? 0) > 0 ||
            (s.video_convert_count ?? 0) > 0 ||
            (s.cleanup_count ?? 0) > 0
          );
        case 'video':
          return (s.video_convert_count ?? 0) > 0;
        case 'audio':
          return (s.audio_convert_count ?? 0) > 0;
        case 'anime':
          return s.is_anime;
        case 'cleanup':
          return (s.cleanup_count ?? 0) > 0;
        default:
          return true;
      }
    });
  }

  // Audio format filter
  if (audioFormat !== 'any') {
    result = result.filter((s) =>
      (s.audio_codecs ?? []).some((c) => audioCodecMatches(c, audioFormat)),
    );
  }

  // Video format filter
  if (videoFormat !== 'any') {
    result = result.filter((s) =>
      (s.video_codecs ?? []).some((c) => videoCodecMatches(c, videoFormat)),
    );
  }

  // Resolution filter
  if (resolutionFilter !== 'any') {
    result = result.filter((s) =>
      (s.resolutions ?? []).some((r) => resolutionMatches(r, resolutionFilter)),
    );
  }

  if (search) {
    const q = search.toLowerCase();
    result = result.filter(
      (s) => s.title.toLowerCase().includes(q) || s.genres.some((g) => g.toLowerCase().includes(q)),
    );
  }
  switch (sortBy) {
    case 'needsWork':
      result = result.toSorted((a, b) => {
        const aw = a.needs_work_count ?? 0;
        const bw = b.needs_work_count ?? 0;
        return bw - aw || sortTitle(a.title).localeCompare(sortTitle(b.title));
      });
      break;
    case 'title':
      result = result.toSorted((a, b) => sortTitle(a.title).localeCompare(sortTitle(b.title)));
      break;
    case 'episodes':
      result = result.toSorted((a, b) => b.episode_file_count - a.episode_file_count);
      break;
    case 'size':
      result = result.toSorted((a, b) => b.size_on_disk - a.size_on_disk);
      break;
    case 'dateAdded':
      result = result.toSorted((a, b) => Date.parse(b.added ?? '') - Date.parse(a.added ?? ''));
      break;
  }
  return result;
});

const listSummary = $derived.by(() => {
  const total = filtered.length;
  const audioEps = filtered.reduce((s, r) => s + (r.audio_convert_count ?? 0), 0);
  const videoEps = filtered.reduce((s, r) => s + (r.video_convert_count ?? 0), 0);
  const cleanupEps = filtered.reduce((s, r) => s + (r.cleanup_count ?? 0), 0);
  const needsWorkEps = filtered.reduce((s, r) => s + (r.needs_work_count ?? 0), 0);
  return { total, audioEps, videoEps, cleanupEps, needsWorkEps };
});

async function doFetchSeries() {
  try {
    const res = await getSeries();
    seriesList = res.series;
    setCachedSeries(res.series);
    loadError = false;
  } catch {
    if (seriesList.length === 0) loadError = true;
  } finally {
    loading = false;
  }
}

function fetchSeries() {
  deduplicatedSeriesFetch(doFetchSeries);
}

async function reloadSeries() {
  reloading = true;
  loading = true;
  seriesList = [];
  invalidateSeries();
  try {
    const res = await getSeries(undefined, undefined, true);
    seriesList = res.series;
    setCachedSeries(res.series);
    loadError = false;
  } catch {
    if (seriesList.length === 0) loadError = true;
  } finally {
    reloading = false;
    loading = false;
  }
}

async function rescanShow() {
  if (!selectedSeries) return;
  rescanning = true;
  try {
    selectedSeries = await getSeriesDetail(selectedSeries.id);
  } catch {
    // keep existing data on failure
  } finally {
    rescanning = false;
  }
}

async function openDetail(series: BrowseSeries) {
  savedScrollY = window.scrollY;
  detailLoading = true;
  expandedSeasons = {};
  try {
    selectedSeries = await getSeriesDetail(series.id);
  } catch {
    selectedSeries = null;
  } finally {
    detailLoading = false;
  }
}

function closeDetail() {
  const y = savedScrollY;
  selectedSeries = null;
  selectedEps = new Set();
  tick().then(() => window.scrollTo({ top: y, behavior: 'instant' }));
}

function toggleSeason(num: number) {
  expandedSeasons[num] = !expandedSeasons[num];
}

async function queueEpisode(ep: EpisodeFile) {
  const key = ep.path;
  queueing[key] = true;
  try {
    await convertFile(ep.path, 'full', selectedSeries?.poster, 'episode');
  } catch {
    // ignore
  } finally {
    queueing[key] = false;
  }
}

async function queueSeason(season: Season) {
  const key = `season-${season.season_number}`;
  queueing[key] = true;
  try {
    for (const ep of season.episodes) {
      if (episodeNeedsWork(ep)) {
        await convertFile(ep.path, 'full', selectedSeries?.poster, 'episode');
      }
    }
  } catch {
    // ignore
  } finally {
    queueing[key] = false;
  }
}

async function queueSeasonOrSelected(season: Season) {
  const seasonPaths = new Set(season.episodes.map((e) => e.path));
  const selectedInSeason = [...selectedEps].filter((p) => seasonPaths.has(p));
  const key = `season-${season.season_number}`;
  queueing[key] = true;
  try {
    if (selectedInSeason.length > 0) {
      for (const path of selectedInSeason) {
        await convertFile(path, 'full', selectedSeries?.poster, 'episode');
      }
      const next = new Set(selectedEps);
      for (const p of selectedInSeason) next.delete(p);
      selectedEps = next;
    } else {
      for (const ep of season.episodes) {
        if (episodeNeedsWork(ep)) {
          await convertFile(ep.path, 'full', selectedSeries?.poster, 'episode');
        }
      }
    }
  } catch {
    // ignore
  } finally {
    queueing[key] = false;
  }
}

async function queueAllSeries() {
  if (!selectedSeries) return;
  queueing.all = true;
  try {
    for (const season of selectedSeries.seasons) {
      for (const ep of season.episodes) {
        if (episodeNeedsWork(ep)) {
          await convertFile(ep.path, 'full', selectedSeries.poster, 'episode');
        }
      }
    }
  } catch {
    // ignore
  } finally {
    queueing.all = false;
  }
}

async function queueAllFiltered() {
  const items = filtered.filter(
    (s) =>
      (s.audio_convert_count ?? 0) > 0 ||
      (s.video_convert_count ?? 0) > 0 ||
      (s.cleanup_count ?? 0) > 0,
  );
  if (items.length === 0) return;
  queueingAll = true;
  try {
    for (const series of items) {
      const detail = await getSeriesDetail(series.id);
      for (const season of detail.seasons) {
        for (const ep of season.episodes) {
          if (episodeNeedsWork(ep)) {
            await convertFile(ep.path, 'full', series.poster, 'episode');
          }
        }
      }
    }
  } catch {
    // ignore
  } finally {
    queueingAll = false;
  }
}

function episodeNeedsWork(ep: EpisodeFile): boolean {
  return !!ep.needs_audio_conversion || !!ep.needs_video_conversion || ep.needs_cleanup;
}

function toggleEpSelect(path: string) {
  const next = new Set(selectedEps);
  if (next.has(path)) next.delete(path);
  else next.add(path);
  selectedEps = next;
}

function selectAllEps() {
  if (!selectedSeries) return;
  const paths = selectedSeries.seasons
    .flatMap((s) => s.episodes)
    .filter((ep) => episodeNeedsWork(ep))
    .map((ep) => ep.path);
  selectedEps = new Set(paths);
}

function clearEpSelection() {
  selectedEps = new Set();
}

async function queueSelectedEps() {
  if (selectedEps.size === 0) return;
  queueingSelected = true;
  try {
    for (const path of selectedEps) {
      await convertFile(path, 'full', selectedSeries?.poster, 'episode');
    }
  } catch {
    // ignore
  } finally {
    queueingSelected = false;
    selectedEps = new Set();
  }
}

function seasonNeedsWork(season: Season): number {
  return season.needs_work;
}

// Episode-level filtering: when format filters are active, filter episodes per season
const filteredSeasons = $derived.by(() => {
  if (!selectedSeries) return [];
  const seasons = selectedSeries.seasons;
  if (audioFormat === 'any' && videoFormat === 'any' && resolutionFilter === 'any') return seasons;

  return seasons
    .map((season) => {
      const eps = season.episodes.filter((ep) => {
        if (
          audioFormat !== 'any' &&
          !audioCodecMatches(ep.audio_codec ?? '', audioFormat, ep.has_dts_x)
        )
          return false;
        if (videoFormat !== 'any' && !videoCodecMatches(ep.video_codec ?? '', videoFormat))
          return false;
        if (resolutionFilter !== 'any' && !resolutionMatches(ep.resolution ?? '', resolutionFilter))
          return false;
        return true;
      });
      return {
        ...season,
        episode_count: eps.length,
        needs_audio: eps.filter((ep) => !!ep.needs_audio_conversion).length,
        needs_work: eps.filter(
          (ep) => !!ep.needs_audio_conversion || !!ep.needs_video_conversion || ep.needs_cleanup,
        ).length,
        size: eps.reduce((sum, ep) => sum + (ep.size ?? 0), 0),
        episodes: eps,
      };
    })
    .filter((season) => season.episodes.length > 0);
});

// Stale-while-revalidate: show cached data instantly, refresh in background if stale
const cached = getCachedSeries();
if (cached) {
  seriesList = cached.data;
  loading = false;
  if (!cached.fresh) fetchSeries();
} else {
  fetchSeries();
}

getConfig()
  .then((c) => {
    config = c;
  })
  .catch(() => {});

// Deep-link: open series detail from ?file= param (e.g. from job card)
$effect(() => {
  if (!deepLinkFile || seriesList.length === 0 || selectedSeries) return;
  const match = seriesList.find((s) => deepLinkFile?.startsWith(s.path));
  if (!match) return;
  const filePath = deepLinkFile;
  deepLinkFile = null;
  goto('/shows', { replaceState: true });
  // Open detail, then expand the matching season
  detailLoading = true;
  expandedSeasons = {};
  getSeriesDetail(match.id)
    .then((detail) => {
      selectedSeries = detail;
      detailLoading = false;
      // Find and expand the season containing the target episode
      for (const season of detail.seasons) {
        if (season.episodes.some((ep) => ep.path === filePath)) {
          expandedSeasons[season.season_number] = true;
          break;
        }
      }
    })
    .catch(() => {
      detailLoading = false;
    });
});

const filters: { value: string; label: string }[] = [
  { value: 'any', label: 'All' },
  { value: 'needs_conversion', label: 'Needs Work' },
  { value: 'video', label: 'Video' },
  { value: 'audio', label: 'Audio' },
  { value: 'cleanup', label: 'Cleanup' },
  { value: 'anime', label: 'Anime' },
];

const audioOptions = $derived(
  buildAudioOptions(
    seriesList.flatMap((s) => s.audio_codecs ?? []),
    seriesList.some((s) => (s.audio_codecs ?? []).includes('DTS:X')),
  ),
);
const videoOptions = $derived(buildVideoOptions(seriesList.flatMap((s) => s.video_codecs ?? [])));
const resolutionOptions = $derived(
  buildResolutionOptions(seriesList.flatMap((s) => s.resolutions ?? [])),
);

// Reset filter if selected value is no longer in contextual options
$effect(() => {
  if (audioFormat !== 'any' && !audioOptions.some((o) => o.value === audioFormat)) {
    audioFormat = 'any';
  }
});
$effect(() => {
  if (videoFormat !== 'any' && !videoOptions.some((o) => o.value === videoFormat)) {
    videoFormat = 'any';
  }
});
$effect(() => {
  if (resolutionFilter !== 'any' && !resolutionOptions.some((o) => o.value === resolutionFilter)) {
    resolutionFilter = 'any';
  }
});

const sortOptions: { value: string; label: string }[] = [
  { value: 'needsWork', label: 'Needs Work' },
  { value: 'title', label: 'Title' },
  { value: 'episodes', label: 'Episodes' },
  { value: 'size', label: 'Size' },
  { value: 'dateAdded', label: 'Date Added' },
];
</script>

<svelte:head>
  <title>{selectedSeries ? `${selectedSeries.title} · Shows` : 'Shows'} · remuXcode</title>
</svelte:head>

{#if selectedSeries}
  <!-- Detail View -->
  <div class="space-y-4">
    <!-- Back nav -->
    <button class="btn btn-ghost btn-sm gap-1" onclick={closeDetail}>
      <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
        <path stroke-linecap="round" stroke-linejoin="round" d="M15.75 19.5 8.25 12l7.5-7.5" />
      </svg>
      Back to Shows
    </button>

    <!-- Series header -->
    <div class="card-glass rounded-box p-4">
      <div class="flex gap-4">
        <div class="w-24 shrink-0">
          <img
            src={selectedSeries.poster}
            alt={selectedSeries.title}
            class="w-full rounded-lg"
            loading="lazy"
            onerror={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }}
          />
        </div>
        <div class="flex-1 min-w-0 space-y-2">
          <h2 class="text-lg font-semibold">{selectedSeries.title}</h2>
          <div class="flex flex-wrap gap-1.5 text-xs">
            <span class="badge badge-ghost badge-sm">{selectedSeries.year}</span>
            {#if selectedSeries.is_anime}
              <span class="badge badge-accent badge-sm">Anime</span>
            {/if}
            <span class="badge badge-ghost badge-sm">{selectedSeries.status}</span>
            {#each selectedSeries.genres.slice(0, 4) as genre}
              <span class="badge badge-outline badge-sm">{genre}</span>
            {/each}
          </div>
          <div class="flex gap-2 mt-2">
            {#if config?.sonarr?.configured}
              <a
                href="{config.sonarr.url}/series/{selectedSeries.title_slug}"
                target="_blank"
                rel="noopener noreferrer"
                class="btn btn-ghost btn-sm gap-1"
                title="Open in Sonarr"
              >
                <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M13.5 6H5.25A2.25 2.25 0 0 0 3 8.25v10.5A2.25 2.25 0 0 0 5.25 21h10.5A2.25 2.25 0 0 0 18 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25" />
                </svg>
                Open in Sonarr
              </a>
            {/if}
            <button class="btn btn-ghost btn-sm" onclick={selectAllEps}>
              Select All
            </button>
            <button
              class="btn btn-ghost btn-sm gap-1"
              onclick={rescanShow}
              disabled={rescanning}
              title="Re-read this show from disk"
            >
              {#if rescanning}
                <span class="loading loading-spinner loading-xs"></span>
              {:else}
                <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182m0-4.991v4.99" />
                </svg>
              {/if}
              Rescan
            </button>
            <button
              class="btn btn-ghost btn-sm"
              title="Custom encode all episodes (downscale / HDR)"
              onclick={() => {
                if (!selectedSeries) return;
                customEncodePaths = selectedSeries.seasons.flatMap(s => s.episodes.map(e => e.path));
                customEncodeLabel = selectedSeries.title;
              }}
            >
              Custom Encode All
            </button>
            {#if selectedEps.size > 0}
              <button class="btn btn-ghost btn-sm ml-auto" onclick={clearEpSelection}>
                Clear ({selectedEps.size})
              </button>
              <button
                class="btn btn-primary btn-sm"
                onclick={queueSelectedEps}
                disabled={queueingSelected}
              >
                {#if queueingSelected}
                  <span class="loading loading-spinner loading-xs"></span>
                {:else}
                  Queue {selectedEps.size} Selected
                {/if}
              </button>
            {:else}
              <button
                class="btn btn-primary btn-sm ml-auto"
                onclick={queueAllSeries}
                disabled={queueing['all']}
              >
                {#if queueing['all']}
                  <span class="loading loading-spinner loading-xs"></span>
                {:else}
                  Queue All Episodes
                {/if}
              </button>
            {/if}
          </div>
        </div>
      </div>
    </div>

    <!-- Seasons -->
    {#each filteredSeasons as season (season.season_number)}
      {@const seasonSelected = season.episodes.filter((e) => selectedEps.has(e.path)).length}
      <div class="card-glass rounded-box overflow-hidden">
        <!-- Season header (clickable) -->
        <div
          class="p-4 flex items-center justify-between hover:bg-base-content/5 transition-colors cursor-pointer"
          role="button"
          tabindex="0"
          onclick={() => toggleSeason(season.season_number)}
          onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleSeason(season.season_number); } }}
        >
          <div class="flex items-center gap-3">
            <svg
              xmlns="http://www.w3.org/2000/svg"
              class="w-4 h-4 transition-transform {expandedSeasons[season.season_number] ? 'rotate-90' : ''}"
              fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"
            >
              <path stroke-linecap="round" stroke-linejoin="round" d="m8.25 4.5 7.5 7.5-7.5 7.5" />
            </svg>
            <span class="font-medium">
              {season.season_number === 0 ? 'Specials' : `Season ${season.season_number}`}
            </span>
            <span class="text-sm text-base-content/40">
              {season.episode_count} episode{season.episode_count !== 1 ? 's' : ''}
            </span>
          </div>
          <div class="flex items-center gap-2">
            {#if seasonActiveJobs(season) > 0}
              <span class="flex items-center gap-1">
                <span class="loading loading-spinner loading-xs text-primary"></span>
                <span class="text-xs text-primary">{seasonActiveJobs(season)} active</span>
              </span>
            {/if}
            {#if seasonNeedsWork(season) > 0}
              <span class="badge badge-warning badge-sm">{seasonNeedsWork(season)} need work</span>
            {/if}
            <span class="text-sm text-base-content/40">{formatSize(season.size)}</span>
            <button
              class="btn btn-ghost btn-xs"
              title="Custom encode season (downscale / HDR)"
              onclick={(e) => {
                e.stopPropagation();
                customEncodePaths = season.episodes.map(e => e.path);
                customEncodeLabel = season.season_number === 0 ? 'Specials' : `Season ${season.season_number}`;
              }}
            >
              <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                <path stroke-linecap="round" stroke-linejoin="round" d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.325.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 0 1 1.37.49l1.296 2.247a1.125 1.125 0 0 1-.26 1.431l-1.003.827c-.293.241-.438.613-.43.992a7.723 7.723 0 0 1 0 .255c-.008.378.137.75.43.991l1.004.827c.424.35.534.955.26 1.43l-1.298 2.247a1.125 1.125 0 0 1-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.47 6.47 0 0 1-.22.128c-.331.183-.581.495-.644.869l-.213 1.281c-.09.543-.56.94-1.11.94h-2.594c-.55 0-1.019-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 0 1-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 0 1-1.369-.49l-1.297-2.247a1.125 1.125 0 0 1 .26-1.431l1.004-.827c.292-.24.437-.613.43-.991a6.932 6.932 0 0 1 0-.255c.007-.38-.138-.751-.43-.992l-1.004-.827a1.125 1.125 0 0 1-.26-1.43l1.297-2.247a1.125 1.125 0 0 1 1.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.086.22-.128.332-.183.582-.495.644-.869l.214-1.28Z" />
                <path stroke-linecap="round" stroke-linejoin="round" d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
              </svg>
            </button>
            <button
              class="btn btn-primary btn-xs"
              onclick={(e) => { e.stopPropagation(); queueSeasonOrSelected(season); }}
              disabled={queueing[`season-${season.season_number}`]}
            >
              {#if queueing[`season-${season.season_number}`]}
                <span class="loading loading-spinner loading-xs"></span>
              {:else if seasonSelected > 0}
                Queue {seasonSelected} Selected
              {:else}
                Queue Season
              {/if}
            </button>
          </div>
        </div>

        <!-- Episode list -->
        {#if expandedSeasons[season.season_number]}
          <div class="border-t border-base-content/5">
            {#each season.episodes as ep (ep.episode_number)}
              <div class="flex items-center gap-3 px-4 py-2.5 hover:bg-base-content/5 transition-colors border-b border-base-content/5 last:border-b-0 {selectedEps.has(ep.path) ? 'bg-primary/5' : ''}">
                <!-- Checkbox -->
                {#if episodeNeedsWork(ep)}
                  <input
                    type="checkbox"
                    class="checkbox checkbox-primary checkbox-xs shrink-0"
                    checked={selectedEps.has(ep.path)}
                    onchange={() => toggleEpSelect(ep.path)}
                  />
                {:else}
                  <div class="w-4 shrink-0"></div>
                {/if}
                <!-- Episode number -->
                <span class="text-sm text-base-content/30 w-8 text-right shrink-0">
                  {ep.episode_number}
                </span>
                <!-- Title -->
                <div class="flex-1 min-w-0">
                  <p class="text-sm truncate" title={ep.title}>{ep.title}</p>
                  <div class="flex flex-wrap gap-1 mt-0.5">
                    {#if ep.video_codec}
                      <span class="badge badge-ghost badge-xs">{ep.video_codec}</span>
                    {/if}
                    {#if ep.audio_codec}
                      <span class="badge badge-ghost badge-xs">{ep.audio_codec}</span>
                    {/if}
                    {#if ep.resolution}
                      <span class="badge badge-ghost badge-xs">{ep.resolution}</span>
                    {/if}
                    {#if ep.needs_video_conversion}
                      <span class="badge badge-secondary badge-xs">Video</span>
                    {/if}
                    {#if ep.needs_audio_conversion}
                      <span class="badge badge-warning badge-xs">Audio</span>
                    {/if}
                    {#if ep.needs_cleanup}
                      <span class="badge badge-info badge-xs">Cleanup</span>
                    {/if}
                    {#if ep.has_dts_x}
                      <span class="badge badge-error badge-xs">DTS:X</span>
                    {/if}
                    {#if ep.cover_art_count}
                      <span class="badge badge-neutral badge-xs">Art</span>
                    {/if}
                  </div>
                  <!-- Language details -->
                  {#if (ep.audio_languages.length > 0 || ep.subtitles.length > 0) && config}
                    {@const isAnime = selectedSeries?.is_anime}
                    {@const removeSubs = removableTracks(ep.subtitles, config)}
                    {@const keepSubs = keptTracks(ep.subtitles, config)}
                    {@const removeAudio = removableTracks(ep.audio_languages, config, isAnime)}
                    {@const keepAudio = (isAnime && config.cleanup.anime_keep_original_audio) ? ep.audio_languages : (!isAnime && config.cleanup.keep_original_audio) ? ep.audio_languages : keptTracks(ep.audio_languages, config)}
                    <div class="flex flex-wrap gap-x-3 gap-y-0.5 mt-0.5">
                      {#if ep.audio_languages.length > 0}
                        <span class="text-xs text-base-content/30">
                          Audio:
                          {#if keepAudio.length > 0}<span class="text-success/70">{trackSummary(keepAudio)}</span>{/if}
                          {#if keepAudio.length > 0 && removeAudio.length > 0}<span class="text-base-content/20"> · </span>{/if}
                          {#if removeAudio.length > 0}<span class="text-error/60 line-through">{trackSummary(removeAudio)}</span>{/if}
                        </span>
                      {/if}
                      {#if ep.subtitles.length > 0}
                        <span class="text-xs text-base-content/30">
                          Subs:
                          {#if keepSubs.length > 0}<span class="text-success/70">{trackSummary(keepSubs)}</span>{/if}
                          {#if keepSubs.length > 0 && removeSubs.length > 0}<span class="text-base-content/20"> · </span>{/if}
                          {#if removeSubs.length > 0}<span class="text-error/60 line-through">{trackSummary(removeSubs)}</span>{/if}
                        </span>
                      {/if}
                    </div>
                  {/if}
                </div>
                <!-- Size -->
                <span class="text-xs text-base-content/30 shrink-0">{formatSize(ep.size)}</span>
                <!-- Job status -->
                {#if getJobStatus(ep.path)}
                  {@const job = getJobStatus(ep.path)!}
                  <span class="flex items-center gap-1 shrink-0">
                    <span class="loading loading-spinner loading-xs text-primary"></span>
                    <span class="text-xs capitalize text-primary">{job.status}</span>
                    {#if job.status === 'running' && job.progress > 0}
                      <span class="text-xs text-base-content/40">{Math.round(job.progress)}%</span>
                    {/if}
                  </span>
                {/if}
                <!-- Analyze button -->
                <button
                  class="btn btn-ghost btn-xs shrink-0"
                  title="Analyze file"
                  onclick={(e) => { e.stopPropagation(); analyzePath = ep.path; }}
                >
                  <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m5.231 13.481L15 17.25m-4.5-15H5.625c-.621 0-1.125.504-1.125 1.125v16.5c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Zm3.75 11.625a2.625 2.625 0 1 1-5.25 0 2.625 2.625 0 0 1 5.25 0Z" />
                  </svg>
                </button>
                <!-- Custom Encode button -->
                <button
                  class="btn btn-ghost btn-xs shrink-0"
                  title="Custom encode (downscale / HDR)"
                  onclick={(e) => { e.stopPropagation(); customEncodePaths = [ep.path]; customEncodeLabel = undefined; }}
                >
                  <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.325.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 0 1 1.37.49l1.296 2.247a1.125 1.125 0 0 1-.26 1.431l-1.003.827c-.293.241-.438.613-.43.992a7.723 7.723 0 0 1 0 .255c-.008.378.137.75.43.991l1.004.827c.424.35.534.955.26 1.43l-1.298 2.247a1.125 1.125 0 0 1-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.47 6.47 0 0 1-.22.128c-.331.183-.581.495-.644.869l-.213 1.281c-.09.543-.56.94-1.11.94h-2.594c-.55 0-1.019-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 0 1-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 0 1-1.369-.49l-1.297-2.247a1.125 1.125 0 0 1 .26-1.431l1.004-.827c.292-.24.437-.613.43-.991a6.932 6.932 0 0 1 0-.255c.007-.38-.138-.751-.43-.992l-1.004-.827a1.125 1.125 0 0 1-.26-1.43l1.297-2.247a1.125 1.125 0 0 1 1.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.086.22-.128.332-.183.582-.495.644-.869l.214-1.28Z" />
                    <path stroke-linecap="round" stroke-linejoin="round" d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
                  </svg>
                </button>
                <!-- Queue button -->
                {#if episodeNeedsWork(ep)}
                  <button
                    class="btn btn-primary btn-xs shrink-0"
                    onclick={() => queueEpisode(ep)}
                    disabled={queueing[ep.path]}
                  >
                    {#if queueing[ep.path]}
                      <span class="loading loading-spinner loading-xs"></span>
                    {:else}
                      Queue
                    {/if}
                  </button>
                {/if}
              </div>
            {/each}
          </div>
        {/if}
      </div>
    {/each}
  </div>
{:else if detailLoading}
  <div class="flex justify-center py-12">
    <span class="loading loading-spinner loading-lg"></span>
  </div>
{:else}
  <!-- Series List View -->
  <div class="space-y-4">
    <!-- Filters + Search -->
    <div class="flex flex-col gap-3">
      <div class="flex flex-wrap items-center gap-2">
        <div class="join">
          {#each filters as f}
            <button
              class="join-item btn btn-sm {filter === f.value ? 'btn-primary' : 'btn-ghost border-base-content/10'}"
              onclick={() => (filter = f.value)}
            >
              {f.label}
            </button>
          {/each}
        </div>
        <select class="select select-sm select-bordered w-36" bind:value={audioFormat}>
          {#each audioOptions as af}
            <option value={af.value}>{af.label}</option>
          {/each}
        </select>
        {#if videoOptions.length > 1}
          <select class="select select-sm select-bordered w-36" bind:value={videoFormat}>
            {#each videoOptions as vf}
              <option value={vf.value}>{vf.label}</option>
            {/each}
          </select>
        {/if}
        {#if resolutionOptions.length > 1}
          <select class="select select-sm select-bordered w-36" bind:value={resolutionFilter}>
            {#each resolutionOptions as rf}
              <option value={rf.value}>{rf.label}</option>
            {/each}
          </select>
        {/if}
        <select class="select select-sm select-bordered w-auto ml-auto" bind:value={sortBy}>
          {#each sortOptions as s}
            <option value={s.value}>{s.label}</option>
          {/each}
        </select>
      </div>
      <div class="relative">
        <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-base-content/30" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
          <path stroke-linecap="round" stroke-linejoin="round" d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z" />
        </svg>
        <input
          type="text"
          placeholder="Search shows…"
          class="input input-sm input-bordered w-full pl-9 {search ? 'pr-8' : ''}"
          bind:value={search}
        />
        {#if search}
          <button
            class="absolute right-2 top-1/2 -translate-y-1/2 btn btn-ghost btn-xs btn-circle text-base-content/40"
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

    <div class="flex items-center justify-between">
    <div class="flex items-center gap-2">
      <div class="text-sm text-base-content/50">
        {listSummary.total} show{listSummary.total !== 1 ? 's' : ''}
        {#if listSummary.needsWorkEps > 0}
          <span class="text-warning">· {listSummary.needsWorkEps} episode{listSummary.needsWorkEps !== 1 ? 's' : ''} need work</span>
        {/if}
      </div>
      {#if refreshMsg}
        <span class="text-xs text-success">{refreshMsg}</span>
      {/if}
      <button
        class="btn btn-ghost btn-xs text-base-content/40"
        onclick={reloadSeries}
        disabled={reloading}
        title="Clear cache and reload series data from Sonarr"
      >
        {#if reloading}
          <span class="loading loading-spinner loading-xs"></span>
        {:else}
          <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.992 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182" /></svg>
        {/if}
        Reload
      </button>
      <button
        class="btn btn-ghost btn-xs text-base-content/40"
        onclick={() => (showRefreshConfirm = true)}
        disabled={refreshingLibrary || !config?.sonarr?.configured}
        title="Force Sonarr to re-read all series files from disk"
      >
        {#if refreshingLibrary}
          <span class="loading loading-spinner loading-xs"></span>
        {:else}
          <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.992 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182" /></svg>
        {/if}
        Refresh Library
      </button>
      {#if scanProgress?.running && scanProgress.type === 'series'}
        <button
          class="btn btn-ghost btn-xs text-error/70"
          onclick={handleStopScan}
          title="Stop library analysis"
        >
          <span class="loading loading-spinner loading-xs"></span>
          Analyzing {scanProgress.analyzed + scanProgress.skipped}/{scanProgress.total}
        </button>
      {:else}
        <button
          class="btn btn-ghost btn-xs text-base-content/40"
          onclick={handleStartScan}
          disabled={!config?.sonarr?.configured}
          title="Analyze all episode files with ffprobe for detailed codec info"
        >
          <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m5.231 13.481L15 17.25m-4.5-15H5.625c-.621 0-1.125.504-1.125 1.125v16.5c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Zm3.75 11.625a2.625 2.625 0 1 1-5.25 0 2.625 2.625 0 0 1 5.25 0Z" /></svg>
          Analyze Library
        </button>
      {/if}
    </div>
      {#if listSummary.needsWorkEps > 0}
        <button
          class="btn btn-primary btn-sm"
          onclick={queueAllFiltered}
          disabled={queueingAll}
        >
          {#if queueingAll}
            <span class="loading loading-spinner loading-xs"></span>
          {:else}
            Queue {listSummary.needsWorkEps} Episode{listSummary.needsWorkEps !== 1 ? 's' : ''}
          {/if}
        </button>
      {/if}
    </div>

    <!-- Scan progress bar -->
    {#if scanProgress?.running && scanProgress.type === 'series' && scanProgress.total > 0}
      <div class="card-glass rounded-box p-3">
        <div class="flex items-center justify-between text-xs text-base-content/50 mb-1">
          <span>Analyzing library… {scanProgress.analyzed + scanProgress.skipped}/{scanProgress.total}</span>
          <span class="truncate ml-2 max-w-[200px]">{scanProgress.current_file ?? ''}</span>
        </div>
        <progress class="progress progress-primary w-full" value={scanProgress.analyzed + scanProgress.skipped} max={scanProgress.total}></progress>
      </div>
    {/if}

    <!-- Series list -->
    {#if loading}
      <div class="flex justify-center py-12">
        <span class="loading loading-spinner loading-lg"></span>
      </div>
    {:else if loadError}
      <div class="card-glass rounded-box p-12 text-center">
        <p class="text-base text-error/80">Failed to load shows</p>
        <p class="text-sm text-base-content/40 mt-1">Check that Sonarr is configured and reachable.</p>
      </div>
    {:else if filtered.length === 0}
      <div class="card-glass rounded-box p-12 text-center">
        {#if analysisFiltersActive}
          <p class="text-base text-base-content/40">No shows match this filter</p>
          <p class="text-sm text-base-content/30 mt-1">Some filters (DTS:X, etc.) require library analysis. Click <strong>Analyze Library</strong> above to scan files with ffprobe.</p>
        {:else if search}
          <p class="text-base text-base-content/40">No shows match your search</p>
          <p class="text-sm text-base-content/30 mt-1">Try a different spelling or clear the search.</p>
        {:else if filter === 'needs_conversion'}
          <p class="text-2xl mb-1">&#127881;</p>
          <p class="text-base text-base-content/60">Every episode is in peak condition.</p>
          <p class="text-sm text-base-content/30 mt-1">Your library called — it says thanks.</p>
        {:else if filter === 'video'}
          <p class="text-2xl mb-1">&#127916;</p>
          <p class="text-base text-base-content/60">All video tracks are already on point.</p>
          <p class="text-sm text-base-content/30 mt-1">Not a re-encode in sight. Well done.</p>
        {:else if filter === 'audio'}
          <p class="text-2xl mb-1">&#127911;</p>
          <p class="text-base text-base-content/60">Audio is impeccable across all episodes.</p>
          <p class="text-sm text-base-content/30 mt-1">Nothing to transcode here — just vibes.</p>
        {:else if filter === 'cleanup'}
          <p class="text-2xl mb-1">&#10024;</p>
          <p class="text-base text-base-content/60">No stray tracks or subtitles to remove.</p>
          <p class="text-sm text-base-content/30 mt-1">Marie Kondo would be proud.</p>
        {:else if filter === 'anime'}
          <p class="text-2xl mb-1">&#128517;</p>
          <p class="text-base text-base-content/60">No anime series found.</p>
          <p class="text-sm text-base-content/30 mt-1">Sonarr doesn't seem to have any anime tagged. Give it time.</p>
        {:else}
          <p class="text-base text-base-content/40">No shows found</p>
          <p class="text-sm text-base-content/30 mt-1">Make sure Sonarr has series in your library.</p>
        {/if}
      </div>
    {:else}
      <div class="space-y-2">
        {#each filtered as series (series.id)}
          <button
            class="card-glass rounded-box w-full text-left cursor-pointer hover:bg-base-content/5 transition-colors"
            onclick={() => openDetail(series)}
          >
            <div class="flex items-center gap-4 p-3">
              <!-- Poster thumbnail -->
              <div class="w-12 h-18 shrink-0 rounded overflow-hidden bg-base-300">
                <img
                  src={series.poster}
                  alt={series.title}
                  class="w-full h-full object-cover"
                  loading="lazy"
                  onerror={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }}
                />
              </div>
              <!-- Info -->
              <div class="flex-1 min-w-0">
                <h3 class="text-sm font-medium truncate">{series.title}</h3>
                <div class="flex flex-wrap items-center gap-1.5 mt-1 text-xs text-base-content/40">
                  <span>{series.year}</span>
                  <span>·</span>
                  <span>{series.season_count} season{series.season_count !== 1 ? 's' : ''}</span>
                  <span>·</span>
                  <span>{series.episode_file_count} episode{series.episode_file_count !== 1 ? 's' : ''}</span>
                  {#if series.is_anime}
                    <span class="badge badge-accent badge-xs">Anime</span>
                  {/if}
                </div>
              </div>
              <!-- Needs work badges -->
              <div class="flex items-center gap-2 shrink-0">
                {#if seriesActiveCount(series.path) > 0}
                  <span class="flex items-center gap-1">
                    <span class="loading loading-spinner loading-xs text-primary"></span>
                    <span class="text-xs text-primary">{seriesActiveCount(series.path)}</span>
                  </span>
                {/if}
                {#if (series.video_convert_count ?? 0) > 0}
                  <span class="badge badge-secondary badge-sm">{series.video_convert_count} video</span>
                {/if}
                {#if (series.audio_convert_count ?? 0) > 0}
                  <span class="badge badge-warning badge-sm">{series.audio_convert_count} audio</span>
                {/if}
                {#if (series.cleanup_count ?? 0) > 0}
                  <span class="badge badge-info badge-sm">{series.cleanup_count} cleanup</span>
                {/if}
                {#if (series.dts_x_count ?? 0) > 0}
                  <span class="badge badge-error badge-sm">{series.dts_x_count} DTS:X</span>
                {/if}
                {#if (series.cover_art_episodes ?? 0) > 0}
                  <span class="badge badge-neutral badge-sm">{series.cover_art_episodes} Art</span>
                {/if}
                <span class="text-xs text-base-content/30">{formatSize(series.size_on_disk)}</span>
                <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4 text-base-content/20" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                  <path stroke-linecap="round" stroke-linejoin="round" d="m8.25 4.5 7.5 7.5-7.5 7.5" />
                </svg>
              </div>
            </div>
          </button>
        {/each}
      </div>
    {/if}
  </div>
{/if}

{#if analyzePath}
  <AnalyzeModal
    path={analyzePath}
    media_type="episode"
    onclose={() => (analyzePath = null)}
    oncoverartchanged={(p, count) => {
      if (!selectedSeries) return;
      for (const season of selectedSeries.seasons) {
        const ep = season.episodes.find((e) => e.path === p);
        if (ep) { ep.cover_art_count = count; break; }
      }
    }}
  />
{/if}

{#if customEncodePaths}
  <ConvertOptionsModal
    paths={customEncodePaths}
    label={customEncodeLabel}
    media_type="episode"
    onclose={() => (customEncodePaths = null)}
  />
{/if}

{#if showRefreshConfirm}
  <div class="modal modal-open">
    <div class="modal-box max-w-sm">
      <h3 class="font-bold text-lg">Refresh Sonarr Library</h3>
      <p class="py-4 text-sm text-base-content/70">This will force Sonarr to re-read metadata for <strong>every series</strong> in your library from disk. Depending on library size, this could take a long time.</p>
      <div class="modal-action">
        <button class="btn btn-ghost btn-sm" onclick={() => (showRefreshConfirm = false)}>Cancel</button>
        <button class="btn btn-warning btn-sm" onclick={handleRefreshLibrary}>Refresh Library</button>
      </div>
    </div>
    <div class="modal-backdrop" role="button" tabindex="-1" aria-label="Close" onclick={() => (showRefreshConfirm = false)} onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') showRefreshConfirm = false; }}></div>
  </div>
{/if}

{#if scrollY > 300}
  <button
    class="btn btn-circle btn-sm fixed bottom-6 right-6 z-50 shadow-lg"
    onclick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}
    aria-label="Back to top"
  >
    <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
      <path stroke-linecap="round" stroke-linejoin="round" d="M4.5 15.75l7.5-7.5 7.5 7.5" />
    </svg>
  </button>
{/if}
