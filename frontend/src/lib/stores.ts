/**
 * Module-level cache for browse data.
 * Survives SvelteKit client-side navigation so pages don't re-fetch on every visit.
 * Uses stale-while-revalidate: returns stale data instantly, refreshes in background.
 */
import type { BrowseMovie, BrowseSeries } from './types';

interface Cache<T> {
  data: T[];
  timestamp: number;
}

const CACHE_TTL = 5 * 60 * 1000; // 5 minutes — matches server-side TTL

let moviesCache: Cache<BrowseMovie> | null = null;
let seriesCache: Cache<BrowseSeries> | null = null;

// Deduplication: prevent concurrent identical fetches
let moviesFetchPromise: Promise<void> | null = null;
let seriesFetchPromise: Promise<void> | null = null;

export interface CacheResult<T> {
  data: T[];
  fresh: boolean;
}

export function getCachedMovies(): CacheResult<BrowseMovie> | null {
  if (!moviesCache) return null;
  return {
    data: moviesCache.data,
    fresh: Date.now() - moviesCache.timestamp < CACHE_TTL,
  };
}

export function setCachedMovies(data: BrowseMovie[]): void {
  moviesCache = { data, timestamp: Date.now() };
}

export function getCachedSeries(): CacheResult<BrowseSeries> | null {
  if (!seriesCache) return null;
  return {
    data: seriesCache.data,
    fresh: Date.now() - seriesCache.timestamp < CACHE_TTL,
  };
}

export function setCachedSeries(data: BrowseSeries[]): void {
  seriesCache = { data, timestamp: Date.now() };
}

/** Mark movies cache as stale (keeps data for instant display, triggers refresh). */
export function invalidateMovies(): void {
  if (moviesCache) moviesCache.timestamp = 0;
}

/** Mark series cache as stale (keeps data for instant display, triggers refresh). */
export function invalidateSeries(): void {
  if (seriesCache) seriesCache.timestamp = 0;
}

/**
 * Deduplicated fetch: if a fetch is already in-flight, return the same promise.
 * Prevents concurrent API storms when multiple triggers fire.
 */
export function deduplicatedMoviesFetch(fetchFn: () => Promise<void>): Promise<void> {
  if (moviesFetchPromise) return moviesFetchPromise;
  moviesFetchPromise = fetchFn().finally(() => {
    moviesFetchPromise = null;
  });
  return moviesFetchPromise;
}

export function deduplicatedSeriesFetch(fetchFn: () => Promise<void>): Promise<void> {
  if (seriesFetchPromise) return seriesFetchPromise;
  seriesFetchPromise = fetchFn().finally(() => {
    seriesFetchPromise = null;
  });
  return seriesFetchPromise;
}
