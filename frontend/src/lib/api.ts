import type {
  ActiveJobsMap,
  AnalyzeResult,
  ConfigSummary,
  EncodeOptions,
  HealthStatus,
  JobLogsResponse,
  JobsResponse,
  MoviesResponse,
  RetagOverride,
  ScanProgress,
  SeriesDetail,
  SeriesResponse,
} from './types';

async function request<T>(path: string, init?: RequestInit, timeoutMs = 60_000): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(path, {
      ...init,
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json', ...init?.headers },
    });
    if (!res.ok) {
      const body = await res.text();
      // Extract human-readable detail from JSON error responses
      let message = body;
      try {
        const parsed = JSON.parse(body);
        if (parsed.detail) message = parsed.detail;
      } catch {
        // not JSON, use raw body
      }
      throw new Error(message);
    }
    return res.json();
  } finally {
    clearTimeout(timeout);
  }
}

// Health
export async function getHealth(): Promise<HealthStatus> {
  return request('/health');
}

// Jobs
export async function getJobs(options?: {
  limit?: number;
  offset?: number;
  status?: 'all' | 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
  search?: string;
  phase?: string;
  media_type?: string;
  source?: string;
  date_from?: string;
  date_to?: string;
}): Promise<JobsResponse> {
  if (!options) return request('/api/jobs');

  const params = new URLSearchParams();
  if (options.limit != null) params.set('limit', String(options.limit));
  if (options.offset != null) params.set('offset', String(options.offset));
  if (options.status && options.status !== 'all') params.set('status', options.status);
  if (options.search) params.set('search', options.search);
  if (options.phase && options.phase !== 'all') params.set('phase', options.phase);
  if (options.media_type && options.media_type !== 'all')
    params.set('media_type', options.media_type);
  if (options.source && options.source !== 'all') params.set('source', options.source);
  if (options.date_from) params.set('date_from', options.date_from);
  if (options.date_to) params.set('date_to', options.date_to);

  const qs = params.toString();
  return request(`/api/jobs${qs ? `?${qs}` : ''}`);
}

export async function deleteJob(id: string): Promise<{ message: string }> {
  return request(`/api/jobs/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export async function cancelJob(id: string): Promise<{ message: string }> {
  return request(`/api/jobs/${encodeURIComponent(id)}/cancel`, { method: 'POST' });
}

export async function retryJob(id: string): Promise<{ job_id: string; message: string }> {
  return request(`/api/jobs/${encodeURIComponent(id)}/retry`, { method: 'POST' });
}

export async function cancelAllPending(): Promise<{ message: string; cancelled: number }> {
  return request('/api/jobs/cancel-pending', { method: 'POST' });
}

export async function cancelRunning(): Promise<{ message: string; cancelled: number }> {
  return request('/api/jobs/cancel-running', { method: 'POST' });
}

export async function cancelAllJobs(): Promise<{ message: string; cancelled: number }> {
  return request('/api/jobs/cancel-all', { method: 'POST' });
}

export async function deleteFinished(): Promise<{ message: string; deleted: number }> {
  return request('/api/jobs/finished', { method: 'DELETE' });
}

export async function getJobLogs(id: string): Promise<JobLogsResponse> {
  return request(`/api/jobs/${encodeURIComponent(id)}/logs`);
}

export async function reorderJobs(order: string[]): Promise<{ order: string[] }> {
  return request('/api/jobs/reorder', {
    method: 'POST',
    body: JSON.stringify({ order }),
  });
}

// Convert
export async function convertFile(
  path: string,
  type: string = 'full',
  poster_url?: string,
  media_type?: string,
  encode_options?: EncodeOptions,
): Promise<{ message: string; job_id: string }> {
  return request('/api/convert', {
    method: 'POST',
    body: JSON.stringify({ path, type, poster_url, media_type, encode_options }),
  });
}

// Retag
export async function retagFile(
  path: string,
  overrides: RetagOverride[],
): Promise<{ message: string; job_id: string }> {
  return request('/api/retag', {
    method: 'POST',
    body: JSON.stringify({ path, overrides }),
  });
}

// Config
export async function getConfig(): Promise<ConfigSummary> {
  return request('/api/config');
}

// Logs
export async function getAppLogs(lines = 1000): Promise<import('$lib/types').AppLogsResponse> {
  return request(`/api/logs?lines=${lines}`);
}

export async function getSystemInfo(): Promise<import('$lib/types').SystemInfo> {
  return request('/api/system/info');
}

export async function updateConfig(patch: Record<string, unknown>): Promise<{ message: string }> {
  return request('/api/config', {
    method: 'PATCH',
    body: JSON.stringify(patch),
  });
}

export async function regenerateApiKey(): Promise<{ api_key: string }> {
  return request('/api/config/api-key/regenerate', { method: 'POST' });
}

// Browse
export async function getMovies(
  search?: string,
  filter?: string,
  cacheBust?: boolean,
): Promise<MoviesResponse> {
  const params = new URLSearchParams();
  if (search) params.set('search', search);
  if (filter && filter !== 'any') params.set('filter', filter);
  params.set('analyze', 'false');
  if (cacheBust) params.set('cache_bust', 'true');
  const qs = params.toString();
  return request(`/api/movies${qs ? `?${qs}` : ''}`);
}

export async function getSeries(
  search?: string,
  filter?: string,
  cacheBust?: boolean,
): Promise<SeriesResponse> {
  const params = new URLSearchParams();
  if (search) params.set('search', search);
  if (filter && filter !== 'any') params.set('filter', filter);
  params.set('analyze', 'false');
  if (cacheBust) params.set('cache_bust', 'true');
  const qs = params.toString();
  return request(`/api/series${qs ? `?${qs}` : ''}`);
}

export async function getSeriesDetail(id: number): Promise<SeriesDetail> {
  return request(`/api/series/${id}`);
}

// Analyze
export async function analyzeFile(
  path: string,
  radarrMovieId?: number,
  sonarrEpisodeFileId?: number,
): Promise<AnalyzeResult> {
  const params = new URLSearchParams({ path });
  if (radarrMovieId) params.set('radarr_movie_id', String(radarrMovieId));
  if (sonarrEpisodeFileId) params.set('sonarr_episode_file_id', String(sonarrEpisodeFileId));
  return request(`/api/analyze?${params}`);
}

export async function removeCoverArt(
  path: string,
  index: number,
): Promise<{ message: string; removed: number }> {
  // Large MKV files on NAS can take several minutes to remux — use a long timeout.
  return request(
    '/api/cover-art/remove',
    {
      method: 'POST',
      body: JSON.stringify({ path, index }),
      headers: { 'Content-Type': 'application/json' },
    },
    600_000,
  );
}

// Active jobs (pending/running) keyed by file path
export async function getActiveJobs(): Promise<ActiveJobsMap> {
  const res = await request<{ active: ActiveJobsMap }>('/api/jobs/active');
  return res.active;
}

// Library refresh
export async function refreshSonarr(): Promise<{ message: string }> {
  return request('/api/config/refresh/sonarr', { method: 'POST' });
}

export async function refreshRadarr(): Promise<{ message: string }> {
  return request('/api/config/refresh/radarr', { method: 'POST' });
}

export async function testSonarrWebhook(): Promise<{ message: string }> {
  return request('/api/config/test-webhook/sonarr', { method: 'POST' });
}

export async function testRadarrWebhook(): Promise<{ message: string }> {
  return request('/api/config/test-webhook/radarr', { method: 'POST' });
}

export async function cleanupTempDirs(): Promise<{ cleaned: number; message: string }> {
  return request('/api/config/cleanup-temp', { method: 'POST' });
}

// Library analysis scan
export async function startMovieScan(): Promise<{ message: string }> {
  return request('/api/analyze/scan/movies', { method: 'POST' });
}

export async function startSeriesScan(): Promise<{ message: string }> {
  return request('/api/analyze/scan/series', { method: 'POST' });
}

export async function getScanProgress(): Promise<ScanProgress> {
  return request('/api/analyze/scan/progress');
}

export async function stopScan(): Promise<{ message: string }> {
  return request('/api/analyze/scan/stop', { method: 'POST' });
}
