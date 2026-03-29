export type JobStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
export type JobType = 'audio' | 'video' | 'cleanup' | 'full';

export interface Job {
  id: string;
  job_type: JobType;
  file_path: string;
  status: JobStatus;
  created_at: number;
  started_at: number | null;
  completed_at: number | null;
  progress: number;
  result: JobResult | null;
  error: string | null;
  source: string;
}

export interface JobResult {
  file: string;
  audio: ConversionResult | null;
  video: VideoResult | null;
  cleanup: CleanupResult | null;
}

export interface ConversionResult {
  success: boolean;
  streams_converted?: number;
  error: string | null;
}

export interface VideoResult {
  success: boolean;
  codec_from: string | null;
  codec_to: string | null;
  content_type: string | null;
  size_change_percent: number | null;
  error: string | null;
}

export interface CleanupResult {
  success: boolean;
  audio_removed: number;
  subtitle_removed: number;
  original_language: string | null;
  error: string | null;
}

export interface ConfigSummary {
  video: {
    enabled: boolean;
    codec: string;
    convert_10bit_x264: boolean;
    convert_8bit_x264: boolean;
    anime_only: boolean;
    anime_crf: number;
    live_action_crf: number;
  };
  audio: {
    enabled: boolean;
    convert_dts: boolean;
    convert_truehd: boolean;
    keep_original: boolean;
    prefer_ac3: boolean;
  };
  cleanup: {
    enabled: boolean;
    clean_audio: boolean;
    clean_subtitles: boolean;
    keep_languages: string[];
    keep_commentary: boolean;
  };
  sonarr: {
    configured: boolean;
    url: string;
  };
  radarr: {
    configured: boolean;
    url: string;
  };
  path_mappings: { container: string; host: string }[];
  workers: number;
  job_history_days: number;
  api_key: string;
}

export interface HealthStatus {
  status: string;
  service: string;
  version: string;
}
