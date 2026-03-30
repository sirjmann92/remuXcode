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

// Browse types

export interface BrowseMovie {
  id: number;
  title: string;
  year: number;
  path: string;
  size: number | null;
  genres: string[];
  poster: string;
  has_dts: boolean;
  has_truehd: boolean;
  video_codec: string;
  audio_codec: string;
  audio_channels: number | null;
  audio_languages: string[];
  subtitles: string[];
  resolution: string;
  needs_cleanup: boolean;
  needs_audio_conversion?: boolean;
  needs_video_conversion?: boolean;
  is_anime?: boolean;
  video?: { codec: string | null; bit_depth: number | null };
}

export interface MoviesResponse {
  total: number;
  summary: {
    needs_video_conversion: number;
    needs_audio_conversion: number;
    needs_cleanup: number;
    anime: number;
  };
  movies: BrowseMovie[];
}

export interface BrowseSeries {
  id: number;
  title: string;
  year: number;
  path: string;
  genres: string[];
  poster: string;
  season_count: number;
  episode_file_count: number;
  size_on_disk: number;
  status: string;
  series_type: string;
  is_anime: boolean;
  audio_convert_count?: number;
  video_convert_count?: number;
  cleanup_count?: number;
}

export interface SeriesResponse {
  total: number;
  summary: {
    needs_audio_conversion: number;
    needs_video_conversion: number;
    needs_cleanup: number;
    anime_series: number;
  };
  series: BrowseSeries[];
}

export interface EpisodeFile {
  episode_number: number;
  title: string;
  path: string;
  size: number | null;
  video_codec: string;
  audio_codec: string;
  audio_channels: number | null;
  audio_languages: string[];
  subtitles: string[];
  resolution: string;
  needs_cleanup: boolean;
  has_dts: boolean;
  has_truehd: boolean;
  needs_audio_conversion?: boolean;
  needs_video_conversion?: boolean;
  is_anime?: boolean;
}

export interface Season {
  season_number: number;
  episode_count: number;
  needs_audio: number;
  needs_cleanup: number;
  needs_work: number;
  size: number;
  episodes: EpisodeFile[];
}

export interface SeriesDetail {
  id: number;
  title: string;
  year: number;
  path: string;
  genres: string[];
  poster: string;
  status: string;
  is_anime: boolean;
  seasons: Season[];
}
