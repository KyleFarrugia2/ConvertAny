export type OutputFormat = "mp4" | "webm" | "mov" | "mkv" | "mp3" | "m4a" | "wav" | "flac" | "ogg" | "gif";
export type Quality = "original" | "high" | "balanced" | "small";
export type Resolution = "original" | "360" | "480" | "720" | "1080" | "1440" | "2160";

export interface Crop { x: number; y: number; w: number; h: number }

export interface EditOptions {
  trim_start: number | null;
  trim_end: number | null;
  crop: Crop | null;
  rotate: 0 | 90 | 180 | 270;
  aspect: "original" | "16:9" | "9:16" | "1:1";
  aspect_mode: "fit" | "fill" | "stretch";
  volume: number;
  fade_in: number;
  fade_out: number;
  strip_metadata: boolean;
}

export interface Options {
  output: OutputFormat;
  quality: Quality;
  video_codec: "auto" | "h264" | "hevc" | "vp9" | "av1" | "prores";
  audio_codec: "auto" | "aac" | "mp3" | "opus" | "vorbis" | "flac" | "pcm";
  resolution: Resolution;
  video_bitrate_kbps: number | null;
  keep_aspect: boolean;
  include_audio: boolean;
  fps_mode: "original" | "standard" | "interpolate";
  target_fps: number | null;
  interp_engine: "auto" | "minterpolate" | "rife";
  upscale: "none" | "standard" | "ai";
  audio_bitrate_kbps: number | null;
  sample_rate: number | null;
  channels: "original" | "mono" | "stereo";
  normalize: boolean;
  wav_bit_depth: 16 | 24;
  flac_compression: number;
  gif_fps: number;
  gif_width: number;
  edit: EditOptions;
  filename: string | null;
  preview: boolean;
}

export const DEFAULT_OPTIONS: Options = {
  output: "mp4",
  quality: "high",
  video_codec: "auto",
  audio_codec: "auto",
  resolution: "original",
  video_bitrate_kbps: null,
  keep_aspect: true,
  include_audio: true,
  fps_mode: "original",
  target_fps: null,
  interp_engine: "auto",
  upscale: "none",
  audio_bitrate_kbps: null,
  sample_rate: null,
  channels: "original",
  normalize: false,
  wav_bit_depth: 16,
  flac_compression: 5,
  gif_fps: 12,
  gif_width: 480,
  edit: { trim_start: null, trim_end: null, crop: null, rotate: 0, aspect: "original", aspect_mode: "fit", volume: 100, fade_in: 0, fade_out: 0, strip_metadata: false },
  filename: null,
  preview: false,
};

export interface MediaInfo {
  format?: string;
  duration: number | null;
  size?: number | null;
  has_video: boolean;
  has_audio: boolean;
  video?: { codec?: string | null; width?: number | null; height?: number | null; fps?: number | null; bit_rate?: number | null } | null;
  audio?: { codec?: string | null; sample_rate?: number | null; channels?: number | null; bit_rate?: number | null } | null;
}

export interface Quality_ { short_side: number; width: number; height: number; fps: number; codecs: string[]; size: number | null }

export interface Entry {
  id: string;
  title: string;
  url?: string;
  thumbnail?: string | null;
  duration?: number | null;
  uploader?: string | null;
  resolved: boolean;
  qualities?: Quality_[];
  audio_streams?: { codec: string; bitrate_kbps: number | null; sample_rate: number | null; channels: number | null; language?: string | null }[];
  drm?: boolean;
  media?: MediaInfo;
}

export interface Analysis {
  id: string;
  adapter: string;
  platform: string;
  source_url: string;
  kind: "single" | "multi" | "collection";
  title: string;
  thumbnail?: string | null;
  entries: Entry[];
  entry_count: number;
  truncated?: boolean;
}

export interface ApiErrorBody { code: string; message: string; action: string; retryable: boolean; fields: Record<string, string> }

export interface PlanResult {
  valid: boolean;
  errors: Record<string, string>;
  warnings: string[];
  notes: string[];
  stages: string[];
  summary: {
    container: string; video_codec: string | null; audio_codec: string | null; resolution: string | null; source_resolution: string | null;
    fps_in: number | null; fps_out: number | null; fps_method: string | null; upscale: string | null; duration: number; stream_copy: boolean; audio: string | null;
  };
  estimate: { size_low?: number; size_high?: number; seconds_low?: number; seconds_high?: number; basis?: string };
  filename: string;
  queue: "cpu" | "gpu";
  stream_copy: boolean;
  interp: { engine: string; label: string; source_fps: number; target_fps: number; scene_detection: boolean; gpu: boolean } | null;
  upscale: { mode: string; label: string; from: number; to: number } | null;
  out_width: number;
  out_height: number;
}

export interface Job {
  id: string;
  batch_id: string | null;
  status: "pending" | "analyzing" | "queued" | "processing" | "completed" | "failed" | "cancelled" | "expired";
  stage: string;
  stages: string[];
  progress: number | null;
  message: string | null;
  error: { code: string; message: string; action: string; retryable: boolean } | null;
  source: { type: "upload" | "url"; title: string; thumbnail: string | null; platform: string | null };
  options: Options;
  summary: PlanResult["summary"] | null;
  estimate: PlanResult["estimate"] | null;
  notes: string[];
  warnings: string[];
  output: {
    filename: string; size: number; format: string; duration: number | null; width: number | null; height: number | null; fps: number | null;
    video_codec: string | null; audio_codec: string | null; sample_rate: number | null; channels: number | null; audio_bitrate: number | null;
    upscaled: boolean; interpolated: boolean;
  } | null;
  queue_position: number | null;
  attempts: number;
  can_cancel: boolean;
  can_retry: boolean;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  expires_at: string | null;
}

export interface CodecCap { id: string; label: string; note?: string; lossy?: boolean; available: boolean }
export interface FormatCap { id: OutputFormat; label: string; category: "video" | "audio" | "gif"; ext: string; recommended: boolean; available: boolean; video_codecs: CodecCap[]; audio_codecs: CodecCap[] }
export interface InterpCap { available: boolean; label: string; reason: string; speed: string; max_height: number; max_seconds: Record<string, number>; scene_detection: boolean }

export interface Capabilities {
  ffmpeg: string;
  formats: Record<OutputFormat, FormatCap>;
  enhancement: {
    interpolation: { minterpolate: InterpCap; rife: InterpCap; fps120_enabled: boolean };
    upscale: { standard: { available: boolean; label: string }; ai: { available: boolean; label: string; reason: string; max_input_height: number; max_seconds: number } };
    loudnorm: boolean;
    gpu: string;
  };
  sources: { id: string; name: string; status: "supported" | "limited" | "temporarily_unavailable" | "unsupported"; notes: string; domains: string[] }[];
  limits: {
    max_upload_bytes: number; max_url_download_bytes: number; max_duration_seconds: number; max_gif_seconds: number; upload_chunk_bytes: number;
    max_files_per_batch: number; max_url_items_per_batch: number; file_ttl_seconds: number; max_output_height: number; preview_seconds: number;
  };
  accepted_inputs: string[];
  abuse_contact_email: string;
}
