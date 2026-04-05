import { langName } from '$lib/languages';
import type { ConfigSummary } from '$lib/types';

/** Human-readable file size. */
export function formatSize(bytes: number | null | undefined): string {
  if (!bytes) return '—';
  if (bytes > 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  if (bytes > 1e6) return `${(bytes / 1e6).toFixed(0)} MB`;
  return `${bytes} B`;
}

/** Channel label: 2→Stereo, 6→5.1, 8→7.1 etc. */
export function channelLabel(ch: number, layout?: string | null): string {
  if (layout) return `${ch}ch (${layout})`;
  const map: Record<number, string> = { 1: 'Mono', 2: 'Stereo', 6: '5.1', 8: '7.1' };
  return map[ch] ?? `${ch}ch`;
}

/** Tracks that are NOT in the user's keep-languages list. */
export function removableTracks(
  tracks: string[],
  config: ConfigSummary | null,
  isAnimeAudio?: boolean,
): string[] {
  if (!config) return [];
  if (isAnimeAudio && config.cleanup.anime_keep_original_audio) return [];
  const keep = new Set(config.cleanup.keep_languages.map((l) => l.toLowerCase()));
  return tracks.filter((t) => !keep.has(t.toLowerCase()));
}

/** Tracks that ARE in the user's keep-languages list. */
export function keptTracks(tracks: string[], config: ConfigSummary | null): string[] {
  if (!config) return [];
  const keep = new Set(config.cleanup.keep_languages.map((l) => l.toLowerCase()));
  return tracks.filter((t) => keep.has(t.toLowerCase()));
}

/** Summarize track list as "English (2), Japanese". */
export function trackSummary(tracks: string[]): string {
  const counts: Record<string, number> = {};
  for (const t of tracks) {
    const name = langName(t);
    counts[name] = (counts[name] ?? 0) + 1;
  }
  return Object.entries(counts)
    .map(([name, n]) => (n > 1 ? `${name} (${n})` : name))
    .join(', ');
}

/** Format epoch timestamp as locale short datetime. */
export function formatTimestamp(epoch: number | null | undefined): string | null {
  if (!epoch) return null;
  return new Date(epoch * 1000).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}
