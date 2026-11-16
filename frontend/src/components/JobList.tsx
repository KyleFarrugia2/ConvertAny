"use client";

import { useMemo, useState } from "react";
import { Archive, AudioLines, Ban, Check, Clock, Download, Film, Globe, ListChecks, LoaderCircle, Plus, RefreshCw, Trash, TriangleExclamationPoint } from "lucide-react";
import { api, resolveApiUrl, toApiError } from "@/lib/api";
import { formatBytes, formatDuration, formatRelative, STAGE_LABELS } from "@/lib/format";
import type { ApiErrorBody, Job } from "@/lib/types";
import { useAnnounce, useJobs } from "./Providers";

const STATUS_BADGE: Record<Job["status"], [string, string]> = {
  pending: ["Pending", ""],
  analyzing: ["Analyzing", "badge-accent"],
  queued: ["Queued", ""],
  processing: ["Processing", "badge-accent"],
  completed: ["Ready", "badge-success"],
  failed: ["Failed", "badge-danger"],
  cancelled: ["Cancelled", ""],
  expired: ["Expired", ""],
};

async function startDownload(url: string) {
  const a = document.createElement("a");
  a.href = url;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function StageList({ job }: { job: Job }) {
  const stages = job.stages.filter((s) => s !== "ready" || job.status === "completed");
  const idx = job.status === "completed" ? stages.length : stages.indexOf(job.stage);
  return (
    <ol className="job-stages" aria-label="Processing steps">
      {stages.map((s, i) => {
        const state = i < idx ? "done" : i === idx && !["failed", "cancelled", "expired"].includes(job.status) ? "current" : "todo";
        return (
          <li key={s} className={state} aria-current={state === "current" ? "step" : undefined}>
            {state === "done" ? <Check size={13} aria-hidden /> : <span className="dot" aria-hidden />}
            {STAGE_LABELS[s] ?? s}
            {state === "done" && <span className="sr-only"> (done)</span>}
          </li>
        );
      })}
    </ol>
  );
}

function JobCard({ job }: { job: Job }) {
  const { upsert, remove, refresh } = useJobs();
  const announce = useAnnounce();
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<ApiErrorBody | null>(null);
  const active = ["pending", "analyzing", "queued", "processing"].includes(job.status);
  const isAudio = job.output ? !job.output.width : ["mp3", "m4a", "wav", "flac", "ogg"].includes(job.options.output);
  const Icon = job.source.type === "url" ? Globe : isAudio ? AudioLines : Film;

  const act = async (name: string, fn: () => Promise<void>) => {
    setBusy(name);
    setErr(null);
    try {
      await fn();
    } catch (e) {
      setErr(toApiError(e));
    } finally {
      setBusy(null);
    }
  };

  const cancel = () => act("cancel", async () => { upsert([await api<Job>(`/api/jobs/${job.id}/cancel`, { method: "POST" })]); announce("Cancellation requested."); });
  const retry = () => act("retry", async () => { upsert([await api<Job>(`/api/jobs/${job.id}/retry`, { method: "POST" })]); announce("Retrying conversion."); });
  const del = () => act("delete", async () => { await api(`/api/jobs/${job.id}`, { method: "DELETE" }); remove(job.id); announce("Files deleted."); refresh(); });
  const download = () => act("download", async () => { const r = await api<{ url: string }>(`/api/jobs/${job.id}/download-link`, { method: "POST" }); await startDownload(resolveApiUrl(r.url)); });

  const pct = job.progress != null ? Math.round(job.progress * 100) : null;
  const stageLabel = STAGE_LABELS[job.stage] ?? job.stage;
  const o = job.output;

  return (
    <article className="panel job fade-in" aria-labelledby={`job-${job.id}`}>
      <div className="job-head">
        <div className="thumb">{job.source.thumbnail ? <img src={job.source.thumbnail} alt="" referrerPolicy="no-referrer" /> : <Icon size={20} aria-hidden />}</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="row" style={{ gap: 8 }}>
            <h3 id={`job-${job.id}`} className="job-title" style={{ margin: 0 }}>{job.source.title || "Untitled"}</h3>
            <span className={`badge ${STATUS_BADGE[job.status][1]}`}>{STATUS_BADGE[job.status][0]}</span>
          </div>
          <div className="item-meta">
            <span>→ {job.options.output.toUpperCase()}</span>
            {job.summary?.resolution && <span>{job.summary.resolution}{job.summary.upscale ? " (upscaled)" : ""}</span>}
            {job.summary?.fps_out && job.options.fps_mode !== "original" && <span>{job.summary.fps_out} fps{job.options.fps_mode === "interpolate" ? " interpolated" : ""}</span>}
            {job.options.preview && <span>Preview clip</span>}
            {job.status === "queued" && job.queue_position != null && <span>Position {job.queue_position} in queue</span>}
          </div>
        </div>
      </div>

      {(active || job.status === "completed") && <StageList job={job} />}

      {active && (
        <div>
          <div className="row" style={{ justifyContent: "space-between", fontSize: 13, marginBottom: 6 }}>
            <span>{stageLabel}{job.stage === "queued" && job.message ? "" : "…"}</span>
            <span className="muted">{pct != null && job.stage !== "queued" ? `${pct}%` : job.stage === "queued" ? "" : "Progress not measurable for this step"}</span>
          </div>
          <div className={`progress ${pct == null || job.stage === "queued" ? "indeterminate" : ""}`} role="progressbar" aria-label={`${job.source.title}: ${stageLabel}`}
            aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct != null && job.stage !== "queued" ? pct : undefined} aria-valuetext={pct != null ? `${stageLabel} ${pct}%` : stageLabel}>
            <span style={pct != null && job.stage !== "queued" ? { width: `${pct}%` } : undefined} />
          </div>
          {job.message && <p className="muted" style={{ margin: "8px 0 0" }}>{job.message}</p>}
        </div>
      )}

      {job.status === "failed" && job.error && (
        <div className="notice notice-danger" style={{ marginTop: 12 }} role="alert">
          <TriangleExclamationPoint size={16} aria-hidden />
          <div><p style={{ marginBottom: 2 }}><strong>{job.error.message}</strong></p>{job.error.action && <p style={{ margin: 0 }}>{job.error.action}</p>}</div>
        </div>
      )}
      {job.status === "expired" && <p className="muted" style={{ marginTop: 10 }}>This file has expired and was deleted. Convert the media again if you still need it.</p>}

      {job.status === "completed" && o && (
        <div className="job-result">
          <div><span>File</span><strong>{o.filename}</strong></div>
          <div><span>Format</span><strong>{o.format}{o.video_codec ? ` · ${o.video_codec.toUpperCase()}` : ""}</strong></div>
          <div><span>Size</span><strong>{formatBytes(o.size)}</strong></div>
          <div><span>Duration</span><strong>{formatDuration(o.duration)}</strong></div>
          {o.width && <div><span>Resolution</span><strong>{o.width}×{o.height}{o.upscaled ? " (upscaled)" : ""}</strong></div>}
          {o.fps && <div><span>Frame rate</span><strong>{Math.round(o.fps * 100) / 100} fps{o.interpolated ? " (interpolated)" : ""}</strong></div>}
          {o.audio_codec && <div><span>Audio</span><strong>{o.audio_codec.toUpperCase()}{o.sample_rate ? ` · ${o.sample_rate / 1000} kHz` : ""}{o.channels ? ` · ${o.channels === 1 ? "mono" : o.channels === 2 ? "stereo" : `${o.channels} ch`}` : ""}{o.audio_bitrate ? ` · ${Math.round(o.audio_bitrate / 1000)} kbps` : ""}</strong></div>}
          <div><span>Expires</span><strong><Clock size={12} aria-hidden style={{ verticalAlign: -1 }} /> {formatRelative(job.expires_at)}</strong></div>
        </div>
      )}

      {err && <div className="notice notice-danger" style={{ marginTop: 10 }} role="alert"><TriangleExclamationPoint size={16} aria-hidden /><p style={{ margin: 0 }}>{err.message} {err.action}</p></div>}

      <div className="job-actions">
        {job.status === "completed" && (
          <button className="btn btn-primary" onClick={download} disabled={busy === "download"}>
            {busy === "download" ? <LoaderCircle size={16} className="spin" aria-hidden /> : <Download size={16} aria-hidden />} Download<span className="sr-only"> {o?.filename}</span>
          </button>
        )}
        {job.can_cancel && <button className="btn" onClick={cancel} disabled={!!busy}><Ban size={15} aria-hidden /> Cancel</button>}
        {job.can_retry && <button className="btn" onClick={retry} disabled={!!busy}><RefreshCw size={15} aria-hidden /> Retry</button>}
        {job.status === "completed" && (
          <button className="btn btn-ghost" onClick={() => { window.scrollTo({ top: 0 }); window.dispatchEvent(new Event("oc:convert-another")); }}><Plus size={15} aria-hidden /> Convert another</button>
        )}
        {!active && (
          <button className="btn btn-ghost btn-danger" onClick={del} disabled={!!busy} aria-label={`Delete ${job.source.title} and its files now`}>
            <Trash size={15} aria-hidden /> Delete now
          </button>
        )}
      </div>
    </article>
  );
}

export function JobList({ limit }: { limit?: number }) {
  const { jobs, loaded } = useJobs();
  const [zipBusy, setZipBusy] = useState<string | null>(null);
  const shown = limit ? jobs.slice(0, limit) : jobs;
  const batches = useMemo(() => {
    const m = new Map<string, number>();
    for (const j of jobs) if (j.batch_id && j.status === "completed") m.set(j.batch_id, (m.get(j.batch_id) ?? 0) + 1);
    return Array.from(m.entries()).filter(([, n]) => n > 1);
  }, [jobs]);

  if (!loaded) return <div className="row muted"><LoaderCircle size={16} className="spin" aria-hidden /> Loading your conversions…</div>;
  if (!jobs.length) {
    return (
      <div className="panel empty">
        <ListChecks size={28} aria-hidden />
        <p style={{ margin: 0 }}>No conversions yet. Paste a link or upload a file to get started.</p>
      </div>
    );
  }
  return (
    <div className="jobs">
      {batches.length > 0 && (
        <div className="row">
          {batches.map(([bid, n]) => (
            <button key={bid} className="btn btn-sm" disabled={zipBusy === bid} onClick={async () => {
              setZipBusy(bid);
              try {
                const r = await api<{ url: string }>(`/api/batches/${bid}/zip-link`, { method: "POST" });
                await startDownload(resolveApiUrl(r.url));
              } finally {
                setZipBusy(null);
              }
            }}>
              {zipBusy === bid ? <LoaderCircle size={15} className="spin" aria-hidden /> : <Archive size={15} aria-hidden />} Download batch as ZIP ({n} files)
            </button>
          ))}
        </div>
      )}
      {shown.map((j) => <JobCard key={j.id} job={j} />)}
      {limit && jobs.length > limit && <a href="/queue" className="btn btn-ghost">View all {jobs.length} conversions</a>}
    </div>
  );
}
