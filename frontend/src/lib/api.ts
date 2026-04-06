import type {
  ActiveJobsMap,
  AnalysisStats,
  AnalyzeResult,
  ConfigSummary,
  HealthStatus,
  JobsResponse,
  MoviesResponse,
  ScanProgress,
  SeriesDetail,
  SeriesResponse,
} from './types';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 60000);
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

// Convert
export async function convertFile(
  path: string,
  type: string = 'full',
  poster_url?: string,
  media_type?: string,
): Promise<{ message: string; job_id: string }> {
  return request('/api/convert', {
    method: 'POST',
    body: JSON.stringify({ path, type, poster_url, media_type }),
  });
}

// Config
export async function getConfig(): Promise<ConfigSummary> {
  return request('/api/config');
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
export async function analyzeFile(path: string): Promise<AnalyzeResult> {
  return request(`/api/analyze?path=${encodeURIComponent(path)}`);
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

export async function getAnalysisStats(): Promise<AnalysisStats> {
  return request('/api/analyze/stats');
}
