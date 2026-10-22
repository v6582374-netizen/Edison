import { yt } from "./text";
import { getCurrentLanguage } from "../../i18n";
export type Layout = "timeline" | "library" | "channels";
export type Asset = {
  video_id: string;
  channel_id: string;
  channel_title: string;
  title: string;
  url: string;
  published_at: string;
  duration_seconds?: number | null;
  preparation_state: string;
  reading_state: string;
  manuscript_version: number | null;
  failure_reason: string | null;
  excerpt?: string;
  manuscript_characters?: number;
};
export type Inspection = Asset & {
  summary?: string | null;
  source_trace: { video_url: string; transcript_available: boolean; translation_available?: boolean };
  generation_records: { manuscript_version: number; created_at: number }[];
};
export type Source = {
  channel_id: string;
  title: string;
  description?: string;
  thumbnail_url?: string | null;
};
export type Preferences = { sources: Source[]; excluded_channels: string[]; last_synced_at?: number | null; sync_error?: string; reconnect_required?: boolean };
export type Connection = {
  managed_content?: boolean;
  disconnection?: { local: string; google: string };
  configured: boolean;
  authorized: boolean;
  subscription_count: number;
  subscription_capability?: { state: "unverified" | "available" | "interrupted"; last_success_at?: number | null; last_failure_at?: number | null; failure_kind?: string | null; reason?: string | null; last_failure_kind?: string | null; last_failure_reason?: string | null };
};
export type DiscoveryState = {
  status?: string; error?: string | null; scanning?: boolean;
  started_at?: number; finished_at?: number; next_scan_at?: number;
  budget?: {used_units: number; local_daily_budget: number; minimum_daily_units: number;
    over_capacity: boolean; requests_by_endpoint: Record<string, number>};
  sources?: {channel_id: string; title: string; complete: number; error: string; coverage_reason: string;
    valid_from?: number | null; valid_until?: number | null}[];
};
export type Activity = {
  cutover?: {held: boolean; state: "idle" | "draining" | "ready" | "aborted"; reason?: string; deadline_at?: number; drained?: boolean; active_requests?: number;
    migration?: {stage: string; stage_label: string; finished: boolean; client_match: boolean | null; permission_error: boolean; identity_withdrawn: boolean;
      unknown: {video_id: string; title: string}[]}};
  discovery?: DiscoveryState;
  document_count?: number;
  cancelled?: number;
  filtered?: number;
  awaiting_classification?: number;
  rate_limited?: number;
  waiting_retry?: number;
  expired?: number;
  awaiting_timing?: number;
  awaiting_completion?: number;
  resources?: { captions_waiting: boolean; reason?: string } | null;
  youtube_requests?: { cooldown_until: number; strikes: number; reason?: string };
  current?: CurrentPreparation[];
  events?: ProgressEvent[];
  console?: ConsoleEntry[];
  runtime?: {heartbeat_at: number | null; worker_alive: boolean; discovering: boolean};
  queued: number;
  acquiring: number;
  generating: number;
  ready: number;
  failed: number;
  unavailable: number;
  drain_paused: boolean;
  model_ready: boolean;
  discovery_error: string | null;
  batch: { completed: number; limit: number };
  failures: {
    video_id: string;
    title: string;
    reason: string;
    state: string;
  }[];
};
export const readingTime = (asset: Asset) =>
  asset.manuscript_characters
    ? yt("预计阅读 {{value1}} 分钟", { value1: Math.max(1, Math.ceil(asset.manuscript_characters / 500)) })
    : "";

export const publicationLabel = (value?: string | null) => {
  const date = new Date(value || "");
  if (!value || Number.isNaN(date.getTime())) return yt("发布日期未知");
  // A date-only source has no timezone; retain its calendar date.
  const day = /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : [
    date.getFullYear(), String(date.getMonth() + 1).padStart(2, "0"),
    String(date.getDate()).padStart(2, "0"),
  ].join("-");
  return yt("发布于 {{date}}", { date: day });
};

export const videoDurationLabel = (value?: number | null) => {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0)
    return yt("时长未知");
  const total = Math.max(1, Math.round(value));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor(total % 3600 / 60);
  const seconds = total % 60;
  if (hours) return yt("视频 {{hours}} 小时 {{minutes}} 分 {{seconds}} 秒", { hours, minutes, seconds });
  if (minutes) return yt("视频 {{minutes}} 分 {{seconds}} 秒", { minutes, seconds });
  return yt("视频 {{seconds}} 秒", { seconds });
};

export const videoMetadataText = (video: {
  channel_title: string; published_at?: string | null; duration_seconds?: number | null;
}) => [video.channel_title, publicationLabel(video.published_at), videoDurationLabel(video.duration_seconds)].filter(Boolean).join(" · ");
export const dateLabel = (value: string) => {
  const date = new Date(value);
  if (!value || Number.isNaN(date.getTime())) return yt("日期未知");
  const now = new Date();
  const yesterday = new Date();
  yesterday.setDate(now.getDate() - 1);
  if (date.toDateString() === now.toDateString()) return yt("今天");
  if (date.toDateString() === yesterday.toDateString()) return yt("昨天");
  return date.toLocaleDateString(getCurrentLanguage(), {
    month: "long",
    day: "numeric",
    ...(date.getFullYear() === now.getFullYear() ? {} : { year: "numeric" }),
  });
};
export const excerpt = (value = "", length = 110) =>
  value
    .replace(/^#+\s+.*$/gm, "")
    .replace(/[`*_>#\[\]]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, length);

export type ProgressEvent = {
  sequence: number; video_id: string; title: string; stage: string; detail: string; occurred_at: number;
};
export type CurrentPreparation = {
  video_id: string; title: string; channel_title: string; preparation_state: string;
  published_at?: string; duration_seconds?: number | null;
  observed_stages?: string[];
  preparation_stage: string | null; preparation_started_at: number | null; stage_updated_at: number | null;
};
export type QueueItem = {
  caption_checks_ended_at?: number | null;
  acquisition_failure_kind?: string | null;
  retry_at?: number | null;
  video_id: string; title: string; channel_title: string; preparation_state: string;
  published_at?: string; duration_seconds?: number | null;
  failure_reason: string | null; manuscript_version: number | null;
  commenced_at?: number | null; request_kind?: "automatic" | "manual";
};

export type ConsoleEntry = {
  sequence: number; video_id: string; message: string; occurred_at: number;
};
