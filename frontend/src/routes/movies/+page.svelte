<script lang="ts">
import { goto } from '$app/navigation';
import { page } from '$app/stores';
import {
  convertFile,
  getActiveJobs,
  getConfig,
  getMovies,
  getScanProgress,
  refreshRadarr,
  startMovieScan,
  stopScan,
} from '$lib/api';
import {
  audioCodecMatches,
  buildAudioOptions,
  buildVideoOptions,
  videoCodecMatches,
} from '$lib/codecs';
import AnalyzeModal from '$lib/components/AnalyzeModal.svelte';
import { formatSize, keptTracks, removableTracks, trackSummary } from '$lib/format';
import { langName } from '$lib/languages';
import {
  deduplicatedMoviesFetch,
  getCachedMovies,
  invalidateMovies,
  setCachedMovies,
} from '$lib/stores';
import type { ActiveJobsMap, BrowseMovie, ConfigSummary, ScanProgress } from '$lib/types';

let movies: BrowseMovie[] = $state([]);
let config: ConfigSummary | null = $state(null);
let loading = $state(true);
let loadError = $state(false);
let search = $state('');
let filter: string = $state('any');
let audioFormat: string = $state('any');
let videoFormat: string = $state('any');
let sortBy: string = $state('needsWork');
let queueing: Record<number, boolean> = $state({});
let queueingAll = $state(false);
let selected: Set<number> = $state(new Set());
let queueingSelected = $state(false);
let detailMovie: BrowseMovie | null = $state(null);
let analyzePath: string | null = $state(null);
let activeJobs: ActiveJobsMap = $state({});
let showRefreshConfirm = $state(false);
let refreshingLibrary = $state(false);
let refreshMsg = $state('');
let reloading = $state(false);
let scanProgress: ScanProgress | null = $state(null);
let scanPollTimer: ReturnType<typeof setInterval> | null = null;
let prevActiveKeys: Set<string> = new Set();
let jobPollTimer: ReturnType<typeof setInterval> | null = null;

async function handleStartScan() {
  try {
    await startMovieScan();
    pollScanProgress();
  } catch (e) {
    // may already be running
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
        // Refresh movie data after scan completes
        invalidateMovies();
        fetchMovies();
      }
    } catch {
      /* ignore */
    }
  };
  tick();
  scanPollTimer = setInterval(tick, 2000);
}

// Check if a scan is already running on mount
getScanProgress()
  .then((p) => {
    scanProgress = p;
    if (p.running && p.type === 'movies') pollScanProgress();
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
    // Detect jobs that just finished (were active, now gone)
    const finished = [...prevActiveKeys].some((k) => !nextKeys.has(k));
    prevActiveKeys = nextKeys;
    activeJobs = next;
    if (finished) {
      invalidateMovies();
      fetchMovies(); // Background refresh — stale data stays visible
    }
  } catch {
    /* ignore */
  }
}

function getJobStatus(path: string) {
  return activeJobs[path] ?? null;
}

$effect(() => {
  startJobPolling();
  return () => {
    if (jobPollTimer) clearInterval(jobPollTimer);
    if (scanPollTimer) clearInterval(scanPollTimer);
  };
});

const analysisFiltersActive = $derived(audioFormat !== 'any' || videoFormat !== 'any');

const filtered = $derived.by(() => {
  let result = movies;

  // Primary filter
  if (filter && filter !== 'any') {
    result = result.filter((m) => {
      switch (filter) {
        case 'needs_conversion':
          return m.needs_audio_conversion || m.needs_video_conversion || m.needs_cleanup;
        case 'video':
          return m.needs_video_conversion;
        case 'audio':
          return m.needs_audio_conversion;
        case 'anime':
          return m.is_anime;
        case 'cleanup':
          return m.needs_cleanup;
        default:
          return true;
      }
    });
  }

  // Audio format filter
  if (audioFormat !== 'any') {
    result = result.filter((m) => audioCodecMatches(m.audio_codec ?? '', audioFormat, m.has_dts_x));
  }

  // Video format filter
  if (videoFormat !== 'any') {
    result = result.filter((m) => videoCodecMatches(m.video_codec ?? '', videoFormat));
  }

  if (search) {
    const q = search.toLowerCase();
    result = result.filter(
      (m) => m.title.toLowerCase().includes(q) || m.genres.some((g) => g.toLowerCase().includes(q)),
    );
  }
  switch (sortBy) {
    case 'needsWork':
      result = result.toSorted((a, b) => {
        const aw = needsWork(a) ? 0 : 1;
        const bw = needsWork(b) ? 0 : 1;
        return aw - bw || a.title.localeCompare(b.title);
      });
      break;
    case 'title':
      result = result.toSorted((a, b) => a.title.localeCompare(b.title));
      break;
    case 'year':
      result = result.toSorted((a, b) => b.year - a.year || a.title.localeCompare(b.title));
      break;
    case 'size':
      result = result.toSorted((a, b) => (b.size ?? 0) - (a.size ?? 0));
      break;
  }
  return result;
});

const summary = $derived({
  total: filtered.length,
  needsWork: filtered.filter(
    (m) => m.needs_audio_conversion || m.needs_video_conversion || m.needs_cleanup,
  ).length,
});

async function doFetchMovies() {
  try {
    const res = await getMovies();
    movies = res.movies;
    setCachedMovies(res.movies);
    loadError = false;
  } catch {
    // Only show error if we have no data at all
    if (movies.length === 0) loadError = true;
  } finally {
    loading = false;
  }
}

function fetchMovies() {
  deduplicatedMoviesFetch(doFetchMovies);
}

async function reloadMovies() {
  reloading = true;
  loading = true;
  movies = [];
  invalidateMovies();
  try {
    const res = await getMovies(undefined, undefined, true);
    movies = res.movies;
    setCachedMovies(res.movies);
    loadError = false;
  } catch {
    if (movies.length === 0) loadError = true;
  } finally {
    reloading = false;
    loading = false;
  }
}

async function queueMovie(movie: BrowseMovie) {
  queueing[movie.id] = true;
  try {
    await convertFile(movie.path, 'full', movie.poster);
  } catch {
    // ignore
  } finally {
    queueing[movie.id] = false;
  }
}

function needsWork(m: BrowseMovie): boolean {
  return !!m.needs_audio_conversion || !!m.needs_video_conversion || m.needs_cleanup;
}

async function queueAllFiltered() {
  const items = filtered.filter((m) => needsWork(m));
  if (items.length === 0) return;
  queueingAll = true;
  try {
    for (const m of items) {
      await convertFile(m.path, 'full', m.poster);
    }
  } catch {
    // ignore
  } finally {
    queueingAll = false;
  }
}

function toggleSelect(id: number) {
  const next = new Set(selected);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  selected = next;
}

function selectAllFiltered() {
  selected = new Set(filtered.filter((m) => needsWork(m)).map((m) => m.id));
}

function clearSelection() {
  selected = new Set();
}

async function queueSelected() {
  const items = filtered.filter((m) => selected.has(m.id));
  if (items.length === 0) return;
  queueingSelected = true;
  try {
    for (const m of items) {
      await convertFile(m.path, 'full', m.poster);
    }
  } catch {
    // ignore
  } finally {
    queueingSelected = false;
    selected = new Set();
  }
}

async function handleRefreshLibrary() {
  showRefreshConfirm = false;
  refreshingLibrary = true;
  refreshMsg = '';
  try {
    const res = await refreshRadarr();
    refreshMsg = res.message;
    setTimeout(() => {
      refreshMsg = '';
    }, 4000);
  } catch (e) {
    refreshMsg = e instanceof Error ? e.message : 'Radarr refresh failed';
  } finally {
    refreshingLibrary = false;
  }
}

// Stale-while-revalidate: show cached data instantly, refresh in background if stale
const cached = getCachedMovies();
if (cached) {
  movies = cached.data;
  loading = false;
  if (!cached.fresh) fetchMovies(); // Background refresh
} else {
  fetchMovies();
}

getConfig()
  .then((c) => {
    config = c;
  })
  .catch(() => {});

// Deep-link: open movie detail from ?file= param (e.g. from job card)
$effect(() => {
  const fileParam = $page.url.searchParams.get('file');
  if (!fileParam || movies.length === 0) return;
  const match = movies.find((m) => m.path === fileParam);
  if (match) {
    detailMovie = match;
    goto('/movies', { replaceState: true });
  }
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
    movies.map((m) => m.audio_codec),
    movies.some((m) => m.has_dts_x),
  ),
);
const videoOptions = $derived(buildVideoOptions(movies.map((m) => m.video_codec)));

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

const sortOptions: { value: string; label: string }[] = [
  { value: 'needsWork', label: 'Needs Work' },
  { value: 'title', label: 'Title' },
  { value: 'year', label: 'Year' },
  { value: 'size', label: 'Size' },
];
</script>

<svelte:head>
  <title>Movies · remuXcode</title>
</svelte:head>

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
      <select class="select select-sm select-bordered w-36" bind:value={videoFormat}>
        {#each videoOptions as vf}
          <option value={vf.value}>{vf.label}</option>
        {/each}
      </select>
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
        placeholder="Search movies…"
        class="input input-sm input-bordered w-full pl-9"
        bind:value={search}
      />
    </div>
  </div>

  <div class="flex items-center justify-between">
    <div class="flex items-center gap-2">
      <div class="text-sm text-base-content/50">
        {summary.total} movie{summary.total !== 1 ? 's' : ''}
        {#if summary.needsWork > 0}
          <span class="text-warning">· {summary.needsWork} need{summary.needsWork !== 1 ? '' : 's'} work</span>
        {/if}
      </div>
      {#if refreshMsg}
        <span class="text-xs text-success">{refreshMsg}</span>
      {/if}
      <button
        class="btn btn-ghost btn-xs text-base-content/40"
        onclick={reloadMovies}
        disabled={reloading}
        title="Clear cache and reload movie data from Radarr"
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
        disabled={refreshingLibrary || !config?.radarr?.configured}
        title="Force Radarr to re-read all movie files from disk"
      >
        {#if refreshingLibrary}
          <span class="loading loading-spinner loading-xs"></span>
        {:else}
          <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.992 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182" /></svg>
        {/if}
        Refresh Library
      </button>
      {#if scanProgress?.running && scanProgress.type === 'movies'}
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
          disabled={!config?.radarr?.configured}
          title="Analyze all movie files with ffprobe for detailed codec info"
        >
          <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m5.231 13.481L15 17.25m-4.5-15H5.625c-.621 0-1.125.504-1.125 1.125v16.5c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Zm3.75 11.625a2.625 2.625 0 1 1-5.25 0 2.625 2.625 0 0 1 5.25 0Z" /></svg>
          Analyze Library
        </button>
      {/if}
    </div>
    <div class="flex items-center gap-2">
      {#if selected.size > 0}
        <button class="btn btn-ghost btn-sm" onclick={clearSelection}>
          Clear ({selected.size})
        </button>
        <button
          class="btn btn-primary btn-sm"
          onclick={queueSelected}
          disabled={queueingSelected}
        >
          {#if queueingSelected}
            <span class="loading loading-spinner loading-xs"></span>
          {:else}
            Queue {selected.size} Selected
          {/if}
        </button>
      {:else if summary.needsWork > 0}
        <button class="btn btn-ghost btn-sm" onclick={selectAllFiltered}>
          Select All
        </button>
        <button
          class="btn btn-primary btn-sm"
          onclick={queueAllFiltered}
          disabled={queueingAll}
        >
          {#if queueingAll}
            <span class="loading loading-spinner loading-xs"></span>
          {:else}
            Queue {summary.needsWork} Movie{summary.needsWork !== 1 ? 's' : ''}
          {/if}
        </button>
      {/if}
    </div>
  </div>

  <!-- Scan progress bar -->
  {#if scanProgress?.running && scanProgress.type === 'movies' && scanProgress.total > 0}
    <div class="card-glass rounded-box p-3">
      <div class="flex items-center justify-between text-xs text-base-content/50 mb-1">
        <span>Analyzing library… {scanProgress.analyzed + scanProgress.skipped}/{scanProgress.total}</span>
        <span class="truncate ml-2 max-w-[200px]">{scanProgress.current_file ?? ''}</span>
      </div>
      <progress class="progress progress-primary w-full" value={scanProgress.analyzed + scanProgress.skipped} max={scanProgress.total}></progress>
    </div>
  {/if}

  <!-- Movie grid -->
  {#if loading}
    <div class="flex justify-center py-12">
      <span class="loading loading-spinner loading-lg"></span>
    </div>
  {:else if loadError}
    <div class="card-glass rounded-box p-12 text-center">
      <p class="text-base text-error/80">Failed to load movies</p>
      <p class="text-sm text-base-content/40 mt-1">Check that Radarr is configured and reachable.</p>
    </div>
  {:else if filtered.length === 0}
    <div class="card-glass rounded-box p-12 text-center">
      {#if analysisFiltersActive}
        <p class="text-base text-base-content/40">No movies match this filter</p>
        <p class="text-sm text-base-content/30 mt-1">Some filters (DTS:X, Atmos, etc.) require library analysis. Click <strong>Analyze Library</strong> above to scan files with ffprobe.</p>
      {:else if search}
        <p class="text-base text-base-content/40">No movies match your search</p>
        <p class="text-sm text-base-content/30 mt-1">Try a different spelling or clear the search.</p>
      {:else if filter === 'needs_conversion'}
        <p class="text-2xl mb-1">&#127881;</p>
        <p class="text-base text-base-content/60">Your movie library is flawless.</p>
        <p class="text-sm text-base-content/30 mt-1">Nothing to convert — go watch something instead.</p>
      {:else if filter === 'video'}
        <p class="text-2xl mb-1">&#127916;</p>
        <p class="text-base text-base-content/60">All video tracks look great.</p>
        <p class="text-sm text-base-content/30 mt-1">Not a single re-encode needed. You love to see it.</p>
      {:else if filter === 'audio'}
        <p class="text-2xl mb-1">&#127911;</p>
        <p class="text-base text-base-content/60">Audio is already perfect across the board.</p>
        <p class="text-sm text-base-content/30 mt-1">Every track is exactly where it should be.</p>
      {:else if filter === 'cleanup'}
        <p class="text-2xl mb-1">&#10024;</p>
        <p class="text-base text-base-content/60">Squeaky clean — nothing to tidy up.</p>
        <p class="text-sm text-base-content/30 mt-1">Your library is more organized than your sock drawer.</p>
      {:else if filter === 'anime'}
        <p class="text-2xl mb-1">&#128517;</p>
        <p class="text-base text-base-content/60">No anime movies found.</p>
        <p class="text-sm text-base-content/30 mt-1">Your Radarr doesn't seem to have any anime tagged. Or you just haven't discovered the genre yet.</p>
      {:else}
        <p class="text-base text-base-content/40">No movies found</p>
        <p class="text-sm text-base-content/30 mt-1">Make sure Radarr has movies in your library.</p>
      {/if}
    </div>
  {:else}
    <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6 gap-3">
      {#each filtered as movie (movie.id)}
        <div
          class="card-glass rounded-box overflow-hidden cursor-pointer hover:ring-1 hover:ring-primary/30 transition-all {selected.has(movie.id) ? 'ring-2 ring-primary' : ''}"
          role="button"
          tabindex="0"
          onclick={() => (detailMovie = movie)}
          onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); detailMovie = movie; } }}
        >
          <!-- Poster -->
          <div class="aspect-[2/3] bg-base-300 relative overflow-hidden">
            <img
              src={movie.poster}
              alt={movie.title}
              class="w-full h-full object-cover"
              loading="lazy"
              onerror={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }}
            />
            <!-- Badges -->
            <div class="absolute top-2 left-2 right-2 flex justify-between items-start pointer-events-none">
              <div class="flex flex-col gap-1 items-start">
                {#if movie.is_anime}
                  <span class="badge badge-secondary badge-xs">Anime</span>
                {/if}
                {#if movie.has_dts_x}
                  <span class="badge badge-error badge-xs">DTS:X</span>
                {/if}
              </div>
              <div class="flex flex-col gap-1 items-end">
                {#if movie.needs_video_conversion}
                  <span class="badge badge-error badge-xs">Video</span>
                {/if}
                {#if movie.needs_audio_conversion}
                  <span class="badge badge-warning badge-xs">Audio</span>
                {/if}
                {#if movie.needs_cleanup}
                  <span class="badge badge-info badge-xs">Cleanup</span>
                {/if}
              </div>
            </div>
            <!-- Job status overlay -->
            {#if getJobStatus(movie.path)}
              {@const job = getJobStatus(movie.path)!}
              <div class="absolute inset-0 bg-black/60 flex flex-col items-center justify-center pointer-events-none">
                <span class="loading loading-spinner loading-md text-primary"></span>
                <span class="text-xs font-medium mt-2 capitalize">{job.status}</span>
                {#if job.status === 'running' && job.progress > 0}
                  <span class="text-xs text-base-content/60 mt-0.5">{Math.round(job.progress)}%</span>
                {/if}
              </div>
            {/if}
            <!-- Select checkbox -->
            {#if needsWork(movie)}
              <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
              <label
                class="absolute bottom-2 left-2 z-10 cursor-pointer"
                onclick={(e) => e.stopPropagation()}
                onkeydown={(e) => e.stopPropagation()}
              >
                <input
                  type="checkbox"
                  class="checkbox checkbox-primary checkbox-sm bg-black/40 border-white/40"
                  checked={selected.has(movie.id)}
                  onchange={() => toggleSelect(movie.id)}
                />
              </label>
            {/if}
            <!-- Analyze button -->
            <button
              class="absolute bottom-2 right-2 btn btn-circle btn-xs btn-ghost bg-black/40 hover:bg-black/70 text-white pointer-events-auto"
              title="Analyze file"
              onclick={(e) => { e.stopPropagation(); analyzePath = movie.path; }}
            >
              <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                <path stroke-linecap="round" stroke-linejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m5.231 13.481L15 17.25m-4.5-15H5.625c-.621 0-1.125.504-1.125 1.125v16.5c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Zm3.75 11.625a2.625 2.625 0 1 1-5.25 0 2.625 2.625 0 0 1 5.25 0Z" />
              </svg>
            </button>
          </div>
          <!-- Info -->
          <div class="p-2.5 space-y-0.5">
            <h3 class="text-sm font-medium truncate" title={movie.title}>{movie.title}</h3>
            <div class="flex items-center justify-between text-xs text-base-content/40">
              <span>{movie.year}</span>
              <span>{formatSize(movie.size)}</span>
            </div>
            <div class="flex flex-wrap gap-1">
              {#if movie.resolution}
                <span class="badge badge-ghost text-[10px] px-1.5 py-0 h-4 min-h-0">{movie.resolution}</span>
              {/if}
              {#if movie.audio_codec}
                <span class="badge badge-ghost text-[10px] px-1.5 py-0 h-4 min-h-0">{movie.audio_codec}</span>
              {/if}
              {#if movie.video_codec}
                <span class="badge badge-ghost text-[10px] px-1.5 py-0 h-4 min-h-0">{movie.video_codec}</span>
              {/if}
            </div>
          </div>
        </div>
      {/each}
    </div>
  {/if}
</div>

<!-- Detail Modal -->
{#if detailMovie}
  <div class="modal modal-open" role="dialog" aria-modal="true">
    <div class="modal-box max-w-lg">
      <button class="btn btn-sm btn-circle btn-ghost absolute right-2 top-2" onclick={() => (detailMovie = null)}>✕</button>

      <div class="flex gap-4">
        <div class="w-28 shrink-0">
          <img src={detailMovie.poster} alt={detailMovie.title} class="w-full rounded-lg" loading="lazy" />
        </div>
        <div class="flex-1 min-w-0 space-y-2">
          <h3 class="text-lg font-semibold">{detailMovie.title}</h3>
          <div class="text-sm text-base-content/50">{detailMovie.year}{detailMovie.resolution ? ` · ${detailMovie.resolution}` : ''}</div>
          <div class="flex flex-wrap gap-1">
            {#if detailMovie.video_codec}<span class="badge badge-ghost badge-sm">{detailMovie.video_codec}</span>{/if}
            {#if detailMovie.audio_codec}<span class="badge badge-ghost badge-sm">{detailMovie.audio_codec}</span>{/if}
            <span class="badge badge-ghost badge-sm">{formatSize(detailMovie.size)}</span>
          </div>
          {#if detailMovie.genres.length > 0}
            <div class="text-xs text-base-content/40">{detailMovie.genres.slice(0, 4).join(', ')}</div>
          {/if}
        </div>
      </div>

      {#if needsWork(detailMovie)}
        <div class="divider text-xs text-base-content/30">Processing Details</div>

        <div class="space-y-3">
          {#if detailMovie.needs_audio_conversion}
            <div class="flex items-start gap-2">
              <span class="badge badge-warning badge-sm shrink-0">Audio</span>
              <p class="text-sm">{detailMovie.audio_codecs_to_convert?.length ? detailMovie.audio_codecs_to_convert.join(', ') : detailMovie.audio_codec} will be converted</p>
            </div>
          {/if}

          {#if detailMovie.needs_cleanup && config}
            {@const removeSubs = removableTracks(detailMovie.subtitles, config)}
            {@const keepSubs = keptTracks(detailMovie.subtitles, config)}
            {@const removeAudio = removableTracks(detailMovie.audio_languages, config, detailMovie.is_anime)}
            {@const keepAudio = (detailMovie.is_anime && config.cleanup.anime_keep_original_audio) ? detailMovie.audio_languages : keptTracks(detailMovie.audio_languages, config)}

            {#if removeSubs.length > 0}
              <div class="flex items-start gap-2">
                <span class="badge badge-info badge-sm shrink-0">Subs</span>
                <div class="text-sm">
                  <span class="text-error/80">{trackSummary(removeSubs)}</span>
                  <span class="text-base-content/40"> to remove</span>
                  {#if keepSubs.length > 0}
                    <span class="text-base-content/40"> · keeping </span>
                    <span class="text-success/80">{trackSummary(keepSubs)}</span>
                  {/if}
                </div>
              </div>
            {/if}

            {#if removeAudio.length > 0}
              <div class="flex items-start gap-2">
                <span class="badge badge-info badge-sm shrink-0">Audio</span>
                <div class="text-sm">
                  <span class="text-error/80">{trackSummary(removeAudio)}</span>
                  <span class="text-base-content/40"> to remove</span>
                  {#if keepAudio.length > 0}
                    <span class="text-base-content/40"> · keeping </span>
                    <span class="text-success/80">{trackSummary(keepAudio)}</span>
                  {/if}
                </div>
              </div>
            {/if}
          {:else if detailMovie.needs_cleanup}
            <div class="flex items-start gap-2">
              <span class="badge badge-info badge-sm shrink-0">Cleanup</span>
              <p class="text-sm text-base-content/50">Subtitle/audio cleanup needed</p>
            </div>
          {/if}

          {#if detailMovie.needs_video_conversion}
            <div class="flex items-start gap-2">
              <span class="badge badge-warning badge-sm shrink-0">Video</span>
              <p class="text-sm">{detailMovie.video_codec} will be re-encoded</p>
            </div>
          {/if}
        </div>

        <div class="modal-action">
          <button
            class="btn btn-ghost btn-sm"
            onclick={() => { analyzePath = detailMovie!.path; }}
          >
            Analyze
          </button>
          <button
            class="btn btn-primary btn-sm"
            onclick={() => { queueMovie(detailMovie!); detailMovie = null; }}
            disabled={queueing[detailMovie.id]}
          >
            {#if queueing[detailMovie.id]}
              <span class="loading loading-spinner loading-xs"></span>
            {:else}
              Queue for Processing
            {/if}
          </button>
        </div>
      {:else}
        <div class="divider text-xs text-base-content/30">Details</div>
        <div class="space-y-2 text-sm">
          <div class="flex items-center gap-2">
            <span class="text-success">✓</span>
            <span class="text-base-content/50">No processing needed</span>
          </div>
          {#if detailMovie.audio_languages.length > 0}
            <div class="text-base-content/40">Audio: {trackSummary(detailMovie.audio_languages)}</div>
          {/if}
          {#if detailMovie.subtitles.length > 0}
            <div class="text-base-content/40">Subtitles: {trackSummary(detailMovie.subtitles)}</div>
          {/if}
        </div>
        <div class="modal-action">
          <button
            class="btn btn-ghost btn-sm"
            onclick={() => { analyzePath = detailMovie!.path; }}
          >
            Analyze
          </button>
        </div>
      {/if}
    </div>
    <div class="modal-backdrop" role="button" tabindex="-1" aria-label="Close" onclick={() => (detailMovie = null)} onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') detailMovie = null; }}></div>
  </div>
{/if}

{#if analyzePath}
  <AnalyzeModal path={analyzePath} onclose={() => (analyzePath = null)} />
{/if}

{#if showRefreshConfirm}
  <div class="modal modal-open">
    <div class="modal-box max-w-sm">
      <h3 class="font-bold text-lg">Refresh Radarr Library</h3>
      <p class="py-4 text-sm text-base-content/70">This will force Radarr to re-read metadata for <strong>every movie</strong> in your library from disk. Depending on library size, this could take a long time.</p>
      <div class="modal-action">
        <button class="btn btn-ghost btn-sm" onclick={() => (showRefreshConfirm = false)}>Cancel</button>
        <button class="btn btn-warning btn-sm" onclick={handleRefreshLibrary}>Refresh Library</button>
      </div>
    </div>
    <div class="modal-backdrop" role="button" tabindex="-1" aria-label="Close" onclick={() => (showRefreshConfirm = false)} onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') showRefreshConfirm = false; }}></div>
  </div>
{/if}
