export function formatBytes(n: number | null | undefined): string {
  if (n == null || !isFinite(n)) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v >= 100 || i === 0 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`;
}

export function formatDuration(sec: number | null | undefined): string {
  if (sec == null || !isFinite(sec)) return "—";
  const s = Math.round(sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}` : `${m}:${String(r).padStart(2, "0")}`;
}

export function formatClock(sec: number): string {
  const ms = Math.round((sec % 1) * 10);
  return `${formatDuration(Math.floor(sec))}.${ms}`;
}

/** Parses "1:23.4", "83.4" or "1:02:03" into seconds. */
export function parseClock(text: string): number | null {
  const t = text.trim();
  if (!t) return null;
  if (!/^[\d:.]+$/.test(t)) return NaN;
  const parts = t.split(":").map(Number);
  if (parts.some((p) => isNaN(p))) return NaN;
  return parts.reduce((acc, p) => acc * 60 + p, 0);
}

export function formatRelative(iso: string | null): string {
  if (!iso) return "";
  const diff = new Date(iso).getTime() - Date.now();
  const abs = Math.abs(diff);
  const mins = Math.round(abs / 60000);
  const text = mins < 1 ? "less than a minute" : mins < 60 ? `${mins} min` : `${Math.floor(mins / 60)} h ${mins % 60 ? `${mins % 60} min` : ""}`.trim();
  return diff >= 0 ? `in ${text}` : `${text} ago`;
}

export function formatRange(low?: number, high?: number, fmt: (n: number) => string = formatBytes): string | null {
  if (low == null || high == null) return null;
  return `${fmt(low)} – ${fmt(high)}`;
}

export function formatSeconds(s: number): string {
  if (s < 60) return `${Math.max(1, Math.round(s))} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  return `${(s / 3600).toFixed(1)} h`;
}

export const STAGE_LABELS: Record<string, string> = {
  uploading: "Uploading",
  analyzing: "Analyzing",
  queued: "Waiting in queue",
  fetching: "Fetching source",
  converting: "Converting",
  enhancing: "Enhancing resolution",
  interpolating: "Interpolating frames",
  finalizing: "Finalizing",
  ready: "Ready to download",
};

export function uid(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}
