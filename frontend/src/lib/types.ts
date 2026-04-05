export type JobStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
export type JobType = 'audio' | 'video' | 'cleanup' | 'full';

export type JobPhase = 'audio' | 'video' | 'cleanup';

export type MediaType = 'movie' | 'episode';

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
  current_phase: JobPhase | null;
  status_detail: string | null;
  completed_phases: JobPhase[] | null;
  planned_phases: JobPhase[] | null;
  poster_url: string | null;
  media_type: MediaType | null;
}

export interface JobsCounts {
  all: number;
  pending: number;
  running: number;
  completed: number;
  failed: number;
  cancelled: number;
}

export interface JobsResponse {
  jobs: Job[];
  total?: number;
  offset?: number;
  limit?: number | null;
  has_more?: boolean;
  counts?: JobsCounts;
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
  converted_streams?: ConvertedStream[] | null;
  original_size?: number;
  new_size?: number;
  error: string | null;
}

export interface ConvertedStream {
  index: number;
  from_codec: string;
  to_codec: string;
  channels: number;
  bitrate: number;
  language: string | null;
}

export interface VideoResult {
  success: boolean;
  codec_from: string | null;
  codec_to: string | null;
  content_type: string | null;
  original_size?: number;
  new_size?: number;
  size_change_percent: number | null;
  error: string | null;
}

export interface CleanupResult {
  success: boolean;
  audio_removed: number;
  audio_kept: number;
  subtitle_removed: number;
  subtitle_kept: number;
  original_size?: number;
  new_size?: number;
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
    // Advanced
    anime_auto_detect: boolean;
    anime_preset: string;
    anime_tune: string;
    anime_framerate: string;
    live_action_preset: string;
    live_action_tune: string;
    live_action_framerate: string;
    av1_anime_crf: number;
    av1_anime_preset: number;
    av1_anime_framerate: string;
    av1_live_action_crf: number;
    av1_live_action_preset: number;
    av1_live_action_framerate: string;
    vbv_maxrate: number;
    vbv_bufsize: number;
    level: string;
    profile: string;
    pix_fmt: string;
    hw_accel: string;
    qsv_anime_quality: number;
    qsv_live_action_quality: number;
    qsv_preset: string;
    vaapi_anime_quality: number;
    vaapi_live_action_quality: number;
    nvenc_anime_quality: number;
    nvenc_live_action_quality: number;
    nvenc_preset: string;
  };
  audio: {
    enabled: boolean;
    anime_only: boolean;
    convert_dts: boolean;
    convert_dts_x: boolean;
    convert_truehd: boolean;
    keep_original: boolean;
    keep_original_dts_x: boolean;
    original_as_secondary: boolean;
    prefer_ac3: boolean;
    // Advanced
    ac3_bitrate: number;
    eac3_bitrate: number;
    aac_surround_bitrate: number;
    aac_stereo_bitrate: number;
  };
  cleanup: {
    enabled: boolean;
    anime_only: boolean;
    clean_audio: boolean;
    clean_subtitles: boolean;
    keep_languages: string[];
    keep_commentary: boolean;
    anime_keep_original_audio: boolean;
    // Advanced
    keep_undefined: boolean;
    keep_audio_description: boolean;
    keep_sdh: boolean;
  };
  sonarr: {
    configured: boolean;
    url: string;
    api_key: string;
  };
  radarr: {
    configured: boolean;
    url: string;
    api_key: string;
  };
  path_mappings: { container: string; host: string }[];
  workers: number;
  ffmpeg_threads: number;
  effective_ffmpeg_threads: number;
  job_history_days: number;
  api_key: string;
}

export interface HWAccelCaps {
  render_devices: string[];
  gpu_vendor: string;
  vaapi_available: boolean;
  qsv_available: boolean;
  nvenc_available: boolean;
  hevc_encoders: string[];
  av1_encoders: string[];
}

export interface SystemInfo {
  cpu_count: number;
  hw_accel: HWAccelCaps;
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
  has_dts_x: boolean;
  video_codec: string;
  audio_codec: string;
  audio_channels: number | null;
  audio_languages: string[];
  subtitles: string[];
  resolution: string;
  needs_cleanup: boolean;
  needs_audio_conversion?: boolean;
  needs_video_conversion?: boolean;
  audio_codecs_to_convert?: string[];
  is_anime?: boolean;
  analyzed?: boolean;
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
  dts_x_count?: number;
  audio_codecs: string[];
  video_codecs: string[];
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
  has_dts_x?: boolean;
  analyzed?: boolean;
  needs_audio_conversion?: boolean;
  needs_video_conversion?: boolean;
  audio_codecs_to_convert?: string[];
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

// Analyze types (ffprobe detail)

export interface AnalyzeVideoStream {
  index: number;
  codec: string;
  codec_long: string;
  profile: string | null;
  width: number;
  height: number;
  resolution: string;
  pix_fmt: string;
  bit_depth: number;
  frame_rate: string;
  bitrate: number | null;
  is_hevc: boolean;
  is_h264: boolean;
}

export interface AnalyzeAudioStream {
  index: number;
  codec: string;
  codec_long: string;
  channels: number;
  channel_layout: string | null;
  sample_rate: number;
  bitrate: number | null;
  language: string | null;
  title: string | null;
  is_default: boolean;
  is_dts: boolean;
  is_truehd: boolean;
  is_lossless: boolean;
}

export interface AnalyzeSubtitleStream {
  index: number;
  codec: string;
  language: string | null;
  title: string | null;
  is_default: boolean;
  is_forced: boolean;
  is_sdh: boolean;
}

export interface AnalyzeResult {
  file: string;
  format: string;
  duration: number;
  size: number;
  bitrate: number;
  chapters: number;
  is_anime: boolean;
  content_type: string;
  needs_audio_conversion: boolean;
  needs_video_conversion: boolean;
  video_streams: AnalyzeVideoStream[];
  audio_streams: AnalyzeAudioStream[];
  subtitle_streams: AnalyzeSubtitleStream[];
  format_tags: Record<string, string>;
}

export interface ActiveJob {
  job_id: string;
  status: 'pending' | 'running';
  progress: number;
}

export type ActiveJobsMap = Record<string, ActiveJob>;

export interface ScanProgress {
  running: boolean;
  type: string | null;
  total: number;
  analyzed: number;
  skipped: number;
  failed: number;
  current_file: string | null;
}

export interface AnalysisStats {
  total_analyzed: number;
  radarr_files: number;
  sonarr_files: number;
}
