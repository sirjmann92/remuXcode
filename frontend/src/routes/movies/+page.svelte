<script lang="ts">
import { convertFile, getConfig, getMovies } from '$lib/api';
import type { BrowseMovie, ConfigSummary } from '$lib/types';

let movies: BrowseMovie[] = $state([]);
let config: ConfigSummary | null = $state(null);
let loading = $state(true);
let loadError = $state(false);
let search = $state('');
let filter: string = $state('any');
let queueing: Record<number, boolean> = $state({});
let detailMovie: BrowseMovie | null = $state(null);

const filtered = $derived.by(() => {
  if (!search) return movies;
  const q = search.toLowerCase();
  return movies.filter(
    (m) => m.title.toLowerCase().includes(q) || m.genres.some((g) => g.toLowerCase().includes(q)),
  );
});

const summary = $derived({
  total: filtered.length,
  needsWork: filtered.filter(
    (m) => m.needs_audio_conversion || m.needs_video_conversion || m.needs_cleanup,
  ).length,
});

async function fetchMovies() {
  try {
    const res = await getMovies(undefined, filter);
    movies = res.movies;
    loadError = false;
  } catch {
    loadError = true;
  } finally {
    loading = false;
  }
}

async function queueMovie(movie: BrowseMovie) {
  queueing[movie.id] = true;
  try {
    await convertFile(movie.path, 'full');
  } catch {
    // ignore
  } finally {
    queueing[movie.id] = false;
  }
}

function formatSize(bytes: number | null): string {
  if (!bytes) return '—';
  if (bytes > 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  if (bytes > 1e6) return `${(bytes / 1e6).toFixed(0)} MB`;
  return `${bytes} B`;
}

function needsWork(m: BrowseMovie): boolean {
  return !!m.needs_audio_conversion || !!m.needs_video_conversion || m.needs_cleanup;
}

const langNames: Record<string, string> = {
  eng: 'English',
  fre: 'French',
  spa: 'Spanish',
  ger: 'German',
  ita: 'Italian',
  por: 'Portuguese',
  rus: 'Russian',
  chi: 'Chinese',
  jpn: 'Japanese',
  kor: 'Korean',
  ara: 'Arabic',
  dut: 'Dutch',
  dan: 'Danish',
  fin: 'Finnish',
  nor: 'Norwegian',
  swe: 'Swedish',
  pol: 'Polish',
  cze: 'Czech',
  hun: 'Hungarian',
  tur: 'Turkish',
  gre: 'Greek',
  heb: 'Hebrew',
  hin: 'Hindi',
  tha: 'Thai',
  ind: 'Indonesian',
  rum: 'Romanian',
  bul: 'Bulgarian',
  hrv: 'Croatian',
  ukr: 'Ukrainian',
  ice: 'Icelandic',
};

function langName(code: string): string {
  return langNames[code.toLowerCase()] ?? code.toUpperCase();
}

function removableTracks(tracks: string[]): string[] {
  if (!config) return [];
  const keep = new Set(config.cleanup.keep_languages.map((l) => l.toLowerCase()));
  return tracks.filter((t) => !keep.has(t.toLowerCase()));
}

function keptTracks(tracks: string[]): string[] {
  if (!config) return [];
  const keep = new Set(config.cleanup.keep_languages.map((l) => l.toLowerCase()));
  return tracks.filter((t) => keep.has(t.toLowerCase()));
}

function trackSummary(tracks: string[]): string {
  const counts: Record<string, number> = {};
  for (const t of tracks) {
    const name = langName(t);
    counts[name] = (counts[name] ?? 0) + 1;
  }
  return Object.entries(counts)
    .map(([name, n]) => (n > 1 ? `${name} (${n})` : name))
    .join(', ');
}

$effect(() => {
  filter;
  loading = true;
  fetchMovies();
});

getConfig()
  .then((c) => {
    config = c;
  })
  .catch(() => {});

const filters: { value: string; label: string }[] = [
  { value: 'any', label: 'All' },
  { value: 'needs_conversion', label: 'Needs Work' },
  { value: 'audio', label: 'Audio' },
  { value: 'cleanup', label: 'Cleanup' },
  { value: 'anime', label: 'Anime' },
];
</script>

<svelte:head>
  <title>Movies · remuXcode</title>
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
        </button>
      {/each}
    </div>
    <div class="relative flex-1">
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

  <div class="text-sm text-base-content/50">
    {summary.total} movie{summary.total !== 1 ? 's' : ''}
    {#if summary.needsWork > 0}
      <span class="text-warning">· {summary.needsWork} need{summary.needsWork !== 1 ? '' : 's'} work</span>
    {/if}
  </div>

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
      <p class="text-base text-base-content/40">No movies found</p>
    </div>
  {:else}
    <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6 gap-3">
      {#each filtered as movie (movie.id)}
        <!-- svelte-ignore a11y_click_events_have_key_events -->
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <div
          class="card-glass rounded-box overflow-hidden cursor-pointer hover:ring-1 hover:ring-primary/30 transition-all"
          onclick={() => (detailMovie = movie)}
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
            <!-- Warning badges -->
            <div class="absolute top-1.5 right-1.5 flex flex-col gap-1">
              {#if movie.needs_audio_conversion}
                <span class="badge badge-warning badge-xs">Audio</span>
              {/if}
              {#if movie.needs_cleanup}
                <span class="badge badge-info badge-xs">Cleanup</span>
              {/if}
            </div>
            {#if movie.is_anime}
              <div class="absolute top-1.5 left-1.5">
                <span class="badge badge-secondary badge-xs">Anime</span>
              </div>
            {/if}
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
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="modal modal-open">
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
              <p class="text-sm">{detailMovie.audio_codec} will be converted</p>
            </div>
          {/if}

          {#if detailMovie.needs_cleanup && config}
            {@const removeSubs = removableTracks(detailMovie.subtitles)}
            {@const keepSubs = keptTracks(detailMovie.subtitles)}
            {@const removeAudio = removableTracks(detailMovie.audio_languages)}
            {@const keepAudio = keptTracks(detailMovie.audio_languages)}

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
      {/if}
    </div>
    <div class="modal-backdrop" onclick={() => (detailMovie = null)}></div>
  </div>
{/if}
