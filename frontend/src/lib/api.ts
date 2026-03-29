import type { ConfigSummary, HealthStatus, Job } from './types';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
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
