import type {
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

export async function getJob(id: string): Promise<Job> {
  return request(`/api/jobs/${encodeURIComponent(id)}`);
}

export async function deleteJob(id: string): Promise<{ message: string }> {
  return request(`/api/jobs/${encodeURIComponent(id)}`, { method: 'DELETE' });
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

export async function regenerateApiKey(): Promise<{ api_key: string }> {
  return request('/api/config/api-key/regenerate', { method: 'POST' });
}

// Browse
export async function getMovies(
  search?: string,
  filter?: string,
): Promise<MoviesResponse> {
  const params = new URLSearchParams();
  if (search) params.set('search', search);
  if (filter && filter !== 'any') params.set('filter', filter);
  params.set('analyze', 'false');
  const qs = params.toString();
  return request(`/api/movies${qs ? `?${qs}` : ''}`);
}

export async function getSeries(
  search?: string,
  filter?: string,
): Promise<SeriesResponse> {
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
