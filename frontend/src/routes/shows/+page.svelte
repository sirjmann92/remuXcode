<script lang="ts">
import { convertFile, getConfig, getSeries, getSeriesDetail } from '$lib/api';
import type { BrowseSeries, ConfigSummary, EpisodeFile, Season, SeriesDetail } from '$lib/types';

let seriesList: BrowseSeries[] = $state([]);
let config: ConfigSummary | null = $state(null);
let loading = $state(true);
let loadError = $state(false);
let search = $state('');
let filter: string = $state('any');

// Detail view state
let selectedSeries: SeriesDetail | null = $state(null);
let detailLoading = $state(false);
let expandedSeasons: Record<number, boolean> = $state({});
let queueing: Record<string, boolean> = $state({});

const filtered = $derived.by(() => {
  if (!search) return seriesList;
  const q = search.toLowerCase();
  return seriesList.filter(
    (s) => s.title.toLowerCase().includes(q) || s.genres.some((g) => g.toLowerCase().includes(q)),
  );
});

const listSummary = $derived.by(() => {
  const total = filtered.length;
  const audioEps = filtered.reduce((s, r) => s + (r.audio_convert_count ?? 0), 0);
  const cleanupEps = filtered.reduce((s, r) => s + (r.cleanup_count ?? 0), 0);
  const needsWorkEps = audioEps + cleanupEps;
  return { total, audioEps, cleanupEps, needsWorkEps };
});

async function fetchSeries() {
  try {
    const res = await getSeries(undefined, filter);
    seriesList = res.series;
    loadError = false;
  } catch {
    loadError = true;
  } finally {
    loading = false;
  }
}

async function openDetail(series: BrowseSeries) {
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
  selectedSeries = null;
}

function toggleSeason(num: number) {
  expandedSeasons[num] = !expandedSeasons[num];
}

async function queueEpisode(ep: EpisodeFile) {
  const key = ep.path;
  queueing[key] = true;
  try {
    await convertFile(ep.path, 'full');
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
      await convertFile(ep.path, 'full');
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
        await convertFile(ep.path, 'full');
      }
    }
  } catch {
    // ignore
  } finally {
    queueing.all = false;
  }
}

function formatSize(bytes: number | null | undefined): string {
  if (!bytes) return '—';
  if (bytes > 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  if (bytes > 1e6) return `${(bytes / 1e6).toFixed(0)} MB`;
  return `${bytes} B`;
}

function episodeNeedsWork(ep: EpisodeFile): boolean {
  return !!ep.needs_audio_conversion || ep.needs_cleanup;
}

function seasonNeedsWork(season: Season): number {
  return season.needs_work;
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
};

function langName(code: string): string {
  return langNames[code.toLowerCase()] ?? code.toUpperCase();
}

function removableTracks(tracks: string[]): string[] {
  if (!config) return [];
  const keep = new Set(config.cleanup.keep_languages.map((l) => l.toLowerCase()));
  return tracks.filter((t) => !keep.has(t.toLowerCase()));
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
  fetchSeries();
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
              <span class="badge badge-secondary badge-sm">Anime</span>
            {/if}
            <span class="badge badge-ghost badge-sm">{selectedSeries.status}</span>
            {#each selectedSeries.genres.slice(0, 4) as genre}
              <span class="badge badge-outline badge-sm">{genre}</span>
            {/each}
          </div>
          <div class="flex gap-2 mt-2">
            <button
              class="btn btn-primary btn-sm"
              onclick={queueAllSeries}
              disabled={queueing['all']}
            >
              {#if queueing['all']}
                <span class="loading loading-spinner loading-xs"></span>
              {:else}
                Queue All Episodes
              {/if}
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Seasons -->
    {#each selectedSeries.seasons as season (season.season_number)}
      <div class="card-glass rounded-box overflow-hidden">
        <!-- Season header (clickable) -->
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <!-- svelte-ignore a11y_click_events_have_key_events -->
        <div
          class="p-4 flex items-center justify-between hover:bg-base-content/5 transition-colors cursor-pointer"
          onclick={() => toggleSeason(season.season_number)}
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
            {#if seasonNeedsWork(season) > 0}
              <span class="badge badge-warning badge-sm">{seasonNeedsWork(season)} need work</span>
            {/if}
            <span class="text-sm text-base-content/40">{formatSize(season.size)}</span>
            <button
              class="btn btn-primary btn-xs"
              onclick={(e) => { e.stopPropagation(); queueSeason(season); }}
              disabled={queueing[`season-${season.season_number}`]}
            >
              {#if queueing[`season-${season.season_number}`]}
                <span class="loading loading-spinner loading-xs"></span>
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
              <div class="flex items-center gap-3 px-4 py-2.5 hover:bg-base-content/5 transition-colors border-b border-base-content/5 last:border-b-0">
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
                    {#if ep.needs_audio_conversion}
                      <span class="badge badge-warning badge-xs">Audio</span>
                    {/if}
                    {#if ep.needs_cleanup}
                      <span class="badge badge-info badge-xs">Cleanup</span>
                    {/if}
                  </div>
                  {#if ep.needs_cleanup && config}
                    {@const removeSubs = removableTracks(ep.subtitles)}
                    {@const removeAudio = removableTracks(ep.audio_languages)}
                    {#if removeSubs.length > 0 || removeAudio.length > 0}
                      <p class="text-xs text-base-content/40 mt-0.5">
                        {#if removeSubs.length > 0}Subs to remove: {trackSummary(removeSubs)}{/if}
                        {#if removeSubs.length > 0 && removeAudio.length > 0} · {/if}
                        {#if removeAudio.length > 0}Audio to remove: {trackSummary(removeAudio)}{/if}
                      </p>
                    {/if}
                  {/if}
                </div>
                <!-- Size -->
                <span class="text-xs text-base-content/30 shrink-0">{formatSize(ep.size)}</span>
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
          placeholder="Search shows…"
          class="input input-sm input-bordered w-full pl-9"
          bind:value={search}
        />
      </div>
    </div>

    <div class="text-sm text-base-content/50">
      {listSummary.total} show{listSummary.total !== 1 ? 's' : ''}
      {#if listSummary.needsWorkEps > 0}
        <span class="text-warning">· {listSummary.needsWorkEps} episode{listSummary.needsWorkEps !== 1 ? 's' : ''} need work</span>
      {/if}
    </div>

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
        <p class="text-base text-base-content/40">No shows found</p>
      </div>
    {:else}
      <div class="space-y-2">
        {#each filtered as series (series.id)}
          <button
            class="card-glass rounded-box w-full text-left hover:bg-base-content/5 transition-colors"
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
                    <span class="badge badge-secondary badge-xs">Anime</span>
                  {/if}
                </div>
              </div>
              <!-- Needs work badges -->
              <div class="flex items-center gap-2 shrink-0">
                {#if (series.audio_convert_count ?? 0) > 0}
                  <span class="badge badge-warning badge-sm">{series.audio_convert_count} audio</span>
                {/if}
                {#if (series.cleanup_count ?? 0) > 0}
                  <span class="badge badge-info badge-sm">{series.cleanup_count} cleanup</span>
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
