import type {
  ActiveJobsMap,
  AnalyzeResult,
  ConfigSummary,
  HealthStatus,
  Job,
  MoviesResponse,
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
      throw new Error(`${res.status}: ${body}`);
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
export async function getJobs(): Promise<{ jobs: Job[] }> {
  return request('/api/jobs');
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

export async function cancelAllJobs(): Promise<{ message: string; cancelled: number }> {
  return request('/api/jobs/cancel-all', { method: 'POST' });
}

// Convert
export async function convertFile(
  path: string,
  type: string = 'full',
): Promise<{ message: string; job_id: string }> {
  return request('/api/convert', {
    method: 'POST',
    body: JSON.stringify({ path, type }),
  });
}

// Config
export async function getConfig(): Promise<ConfigSummary> {
  return request('/api/config');
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
export async function getMovies(search?: string, filter?: string): Promise<MoviesResponse> {
  const params = new URLSearchParams();
  if (search) params.set('search', search);
  if (filter && filter !== 'any') params.set('filter', filter);
  params.set('analyze', 'false');
  const qs = params.toString();
  return request(`/api/movies${qs ? `?${qs}` : ''}`);
}

export async function getSeries(search?: string, filter?: string): Promise<SeriesResponse> {
  const params = new URLSearchParams();
  if (search) params.set('search', search);
  if (filter && filter !== 'any') params.set('filter', filter);
  params.set('analyze', 'false');
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
