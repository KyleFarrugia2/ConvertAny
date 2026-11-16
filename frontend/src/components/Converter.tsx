"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowRight, AudioLines, CircleCheck, ClipboardPaste, Film, Globe, Info, Link2, LoaderCircle, Plus, RefreshCw, Sparkles, Trash, TriangleExclamationPoint, Upload, X } from "lucide-react";
import { api, toApiError, uploadFile, type UploadHandle } from "@/lib/api";
import { formatBytes, formatDuration, formatRange, formatSeconds, STAGE_LABELS, uid } from "@/lib/format";
import { DEFAULT_OPTIONS, type Analysis, type ApiErrorBody, type Entry, type Job, type MediaInfo, type Options, type PlanResult, type Quality_ } from "@/lib/types";
import { useAnnounce, useCapabilities, useJobs } from "./Providers";
import { categoryOf, SettingsPanel, type Category, type Extra } from "./SettingsPanel";

interface Item {
  key: string;
  kind: "upload" | "url";
  name: string;
  file?: File;
  previewUrl?: string;
  size?: number;
  status: "uploading" | "verifying" | "resolving" | "ready" | "error";
  progress: number;
  uploadId?: string;
  analysisId?: string;
  entryId?: string;
  platform?: string;
  thumbnail?: string | null;
  media?: MediaInfo | null;
  qualities?: Quality_[];
  error?: ApiErrorBody;
  options: Options;
  plan?: PlanResult | null;
  planError?: ApiErrorBody | null;
}

const ACCEPT = ".mp4,.mov,.mkv,.webm,.avi,.m4v,.mp3,.wav,.aac,.m4a,.flac,.ogg,.oga,.opus,video/*,audio/*";

function optionsFor(template: Options, media?: MediaInfo | null): Options {
  const o = structuredClone(template);
  o.edit = { ...DEFAULT_OPTIONS.edit, strip_metadata: template.edit.strip_metadata };
  o.filename = null;
  o.preview = false;
  // Format and quality carry over between items; costly enhancements never do silently.
  o.fps_mode = "original";
  o.target_fps = null;
  o.interp_engine = "auto";
  o.upscale = "none";
  if (o.resolution !== "original") o.resolution = DEFAULT_OPTIONS.resolution;
  if (media && !media.has_video && ["mp4", "webm", "mov", "mkv", "gif"].includes(o.output)) {
    o.output = "mp3";
    o.video_codec = "auto";
    o.audio_codec = "auto";
  }
  if (media && !media.has_audio && ["mp3", "m4a", "wav", "flac", "ogg"].includes(o.output)) o.output = "mp4";
  return o;
}

function ErrorNotice({ error, children }: { error: ApiErrorBody; children?: React.ReactNode }) {
  return (
    <div className="notice notice-danger" role="alert">
      <TriangleExclamationPoint size={16} aria-hidden />
      <div>
        <p style={{ marginBottom: 4 }}><strong>{error.message}</strong></p>
        {error.action && <p style={{ margin: 0 }}>{error.action}</p>}
        {children}
      </div>
    </div>
  );
}

export function Converter() {
  const { caps, error: capsError } = useCapabilities();
  const { upsert } = useJobs();
  const announce = useAnnounce();
  const [tab, setTab] = useState<"link" | "upload">("link");
  const [items, setItems] = useState<Item[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [template, setTemplate] = useState<Options>(DEFAULT_OPTIONS);
  const [mode, setMode] = useState<"simple" | "advanced">("simple");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<ApiErrorBody | null>(null);
  const [submitted, setSubmitted] = useState<{ count: number; failed: number } | null>(null);
  const idemKey = useRef<string>(uid());
  const handles = useRef<Map<string, UploadHandle>>(new Map());
  const urlInputRef = useRef<HTMLInputElement>(null);

  const patchItem = useCallback((key: string, patch: Partial<Item> | ((it: Item) => Partial<Item>)) => {
    setItems((cur) => cur.map((it) => (it.key === key ? { ...it, ...(typeof patch === "function" ? patch(it) : patch) } : it)));
  }, []);

  // Fall back to the first item if the selected one was removed or submitted.
  const sel = items.find((i) => i.key === selected) ?? items[0] ?? null;

  /* ------------------------------- uploads ------------------------------- */
  const startUpload = useCallback((key: string, file: File, resumeId?: string) => {
    patchItem(key, { status: "uploading", error: undefined });
    const h = uploadFile(file, (frac, phase) => patchItem(key, { progress: frac, status: phase === "verifying" ? "verifying" : "uploading" }), resumeId);
    handles.current.set(key, h);
    h.promise
      .then((up) => {
        setItems((cur) => cur.map((it) => (it.key === key ? { ...it, status: "ready", progress: 1, uploadId: up.id, media: up.media, options: optionsFor(template, up.media) } : it)));
        announce(`${file.name} uploaded and inspected.`);
      })
      .catch((e) => {
        const err = toApiError(e);
        if (err.code === "cancelled") return;
        patchItem(key, { status: "error", error: err, uploadId: h.uploadId() ?? undefined });
        announce(`${file.name}: ${err.message}`);
      })
      .finally(() => handles.current.delete(key));
  }, [patchItem, template, announce]);

  const addFiles = useCallback((files: FileList | File[]) => {
    if (!caps) return;
    const list = Array.from(files);
    const room = caps.limits.max_files_per_batch - items.length;
    const accepted = list.slice(0, Math.max(0, room));
    const newItems: Item[] = accepted.map((f) => {
      const tooBig = f.size > caps.limits.max_upload_bytes;
      return {
        key: uid(), kind: "upload", name: f.name, file: f, size: f.size, previewUrl: URL.createObjectURL(f),
        status: tooBig ? "error" : "uploading", progress: 0, options: structuredClone(template),
        error: tooBig ? { code: "file_too_large", message: `This file is larger than the ${formatBytes(caps.limits.max_upload_bytes)} limit.`, action: "Trim or compress it first, or split it into parts.", retryable: false, fields: {} } : undefined,
      };
    });
    setItems((cur) => [...cur, ...newItems]);
    if (newItems.length && !items.some((i) => i.key === selected)) setSelected(newItems[0].key);
    for (const it of newItems) if (it.status === "uploading" && it.file) startUpload(it.key, it.file);
    if (list.length > accepted.length) setSubmitError({ code: "batch_too_large", message: `Only ${caps.limits.max_files_per_batch} items per batch.`, action: `${list.length - accepted.length} file(s) were not added.`, retryable: false, fields: {} });
  }, [caps, items, selected, startUpload, template]);

  const removeItem = useCallback((key: string) => {
    const it = items.find((i) => i.key === key);
    handles.current.get(key)?.cancel();
    if (it?.previewUrl) URL.revokeObjectURL(it.previewUrl);
    if (it?.uploadId) api(`/api/uploads/${it.uploadId}`, { method: "DELETE" }).catch(() => {});
    setItems((cur) => cur.filter((i) => i.key !== key));
    setSelected((s) => (s === key ? items.find((i) => i.key !== key)?.key ?? null : s));
  }, [items]);

  /* ------------------------------- url items ------------------------------ */
  const addEntries = useCallback((analysis: Analysis, entries: Entry[]) => {
    const newItems: Item[] = entries.map((e) => ({
      key: uid(), kind: "url", name: e.title, analysisId: analysis.id, entryId: e.id, platform: analysis.platform, thumbnail: e.thumbnail ?? analysis.thumbnail,
      status: e.resolved ? "ready" : "resolving", progress: 1, media: e.media ?? (e.duration ? { duration: e.duration, has_video: true, has_audio: true } : null),
      qualities: e.qualities, options: optionsFor(template, e.media),
    }));
    setItems((cur) => [...cur, ...newItems]);
    setSelected(newItems[0]?.key ?? null);
    for (const it of newItems) {
      if (it.status !== "resolving") continue;
      api<Entry>(`/api/analyze/${analysis.id}/entries/${encodeURIComponent(it.entryId!)}/resolve`, { method: "POST" })
        .then((full) => patchItem(it.key, (cur) => ({ status: "ready", media: full.media, qualities: full.qualities, thumbnail: full.thumbnail ?? cur.thumbnail, name: full.title || cur.name, options: optionsFor(cur.options, full.media) })))
        .catch((e) => patchItem(it.key, { status: "error", error: toApiError(e) }));
    }
  }, [patchItem, template]);

  /* ------------------------------ settings ------------------------------ */
  const setOptions = useCallback((key: string, fn: (o: Options) => Options) => {
    setItems((cur) => cur.map((it) => {
      if (it.key !== key) return it;
      const next = fn(it.options);
      setTemplate(next);
      return { ...it, options: next };
    }));
  }, []);

  const applyToAll = () => {
    if (!sel) return;
    const src = sel.options;
    setItems((cur) => cur.map((it) => it.key === sel.key ? it : {
      ...it,
      // format, quality and enhancement settings are shared; trims, crops and names stay per item
      options: optionsFor({ ...src, edit: { ...it.options.edit, strip_metadata: src.edit.strip_metadata, volume: src.edit.volume, fade_in: src.edit.fade_in, fade_out: src.edit.fade_out, rotate: src.edit.rotate, aspect: src.edit.aspect, aspect_mode: src.edit.aspect_mode }, filename: it.options.filename }, it.media),
    }));
    announce(`Settings applied to ${items.length - 1} other item(s).`);
  };

  // Debounced server-side validation + estimate for every ready item whose options changed.
  const planKeys = items.filter((i) => i.status === "ready").map((i) => `${i.key}:${JSON.stringify(i.options)}`).join("|");
  const lastPlanned = useRef<Map<string, string>>(new Map());
  useEffect(() => {
    const t = setTimeout(() => {
      for (const it of items) {
        if (it.status !== "ready") continue;
        const sig = JSON.stringify(it.options);
        if (lastPlanned.current.get(it.key) === sig) continue;
        lastPlanned.current.set(it.key, sig);
        const source = it.kind === "upload" ? { upload_id: it.uploadId } : { analysis_id: it.analysisId, entry_id: it.entryId };
        api<PlanResult>("/api/plan", { method: "POST", json: { source, options: it.options } })
          .then((plan) => patchItem(it.key, (cur) => (JSON.stringify(cur.options) === sig ? { plan, planError: null } : {})))
          .catch((e) => patchItem(it.key, { plan: null, planError: toApiError(e) }));
      }
    }, 350);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [planKeys]);

  /* ------------------------------- submit ------------------------------- */
  const ready = items.filter((i) => i.status === "ready");
  const busy = items.some((i) => ["uploading", "verifying", "resolving"].includes(i.status));
  const invalid = ready.filter((i) => i.plan && !i.plan.valid);
  const canSubmit = ready.length > 0 && !submitting && invalid.length === 0;

  const submit = async () => {
    setSubmitting(true);
    setSubmitError(null);
    try {
      const payload = {
        idempotency_key: idemKey.current,
        items: ready.map((it) => ({
          client_id: it.key,
          source: it.kind === "upload" ? { upload_id: it.uploadId } : { analysis_id: it.analysisId, entry_id: it.entryId },
          options: it.options,
        })),
      };
      const r = await api<{ batch_id: string; results: { client_id: string | null; job?: Job; error?: ApiErrorBody }[] }>("/api/jobs", { method: "POST", json: payload });
      const jobs = r.results.filter((x) => x.job).map((x) => x.job!) as Job[];
      upsert(jobs);
      const failedKeys = new Map(r.results.filter((x) => x.error && x.client_id).map((x) => [x.client_id!, x.error!]));
      setItems((cur) => cur.filter((it) => !ready.some((r2) => r2.key === it.key) || failedKeys.has(it.key)).map((it) => (failedKeys.has(it.key) ? { ...it, planError: failedKeys.get(it.key) } : it)));
      setSubmitted({ count: jobs.length, failed: failedKeys.size });
      idemKey.current = uid();
      announce(`${jobs.length} conversion${jobs.length === 1 ? "" : "s"} started.`);
      if (jobs.length) document.getElementById("queue")?.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
    } catch (e) {
      setSubmitError(toApiError(e));
    } finally {
      setSubmitting(false);
    }
  };

  useEffect(() => {
    const focus = () => { setTab("link"); setTimeout(() => urlInputRef.current?.focus(), 50); };
    window.addEventListener("oc:convert-another", focus);
    return () => window.removeEventListener("oc:convert-another", focus);
  }, []);

  if (capsError) return <ErrorNotice error={{ code: "unreachable", message: capsError, action: "Make sure the API service is running, then reload the page.", retryable: true, fields: {} }} />;
  if (!caps) return <div className="panel panel-pad"><div className="row muted"><LoaderCircle size={16} className="spin" aria-hidden /> Loading converter…</div></div>;

  const { cat, extra } = sel ? categoryOf(sel.options) : { cat: "video" as Category, extra: null };

  return (
    <div className="converter">
      <section className="panel panel-pad" aria-label="Add media">
        <div className="converter-tabs" role="tablist" aria-label="Input method">
          <button role="tab" id="tab-link" aria-controls="panel-link" aria-selected={tab === "link"} tabIndex={tab === "link" ? 0 : -1} onClick={() => setTab("link")}
            onKeyDown={(e) => (e.key === "ArrowRight" || e.key === "ArrowLeft") && (setTab("upload"), document.getElementById("tab-upload")?.focus())}>
            <Link2 size={17} aria-hidden /> Paste a link
          </button>
          <button role="tab" id="tab-upload" aria-controls="panel-upload" aria-selected={tab === "upload"} tabIndex={tab === "upload" ? 0 : -1} onClick={() => setTab("upload")}
            onKeyDown={(e) => (e.key === "ArrowRight" || e.key === "ArrowLeft") && (setTab("link"), document.getElementById("tab-link")?.focus())}>
            <Upload size={17} aria-hidden /> Upload a file
          </button>
        </div>
        <div id="panel-link" role="tabpanel" aria-labelledby="tab-link" hidden={tab !== "link"}>
          <LinkPanel inputRef={urlInputRef} onAdd={addEntries} onSwitchToUpload={() => setTab("upload")} maxItems={caps.limits.max_url_items_per_batch} />
        </div>
        <div id="panel-upload" role="tabpanel" aria-labelledby="tab-upload" hidden={tab !== "upload"}>
          <UploadPanel onFiles={addFiles} />
        </div>
      </section>

      {submitted && items.length === 0 && (
        <div className="notice notice-success fade-in" role="status">
          <CircleCheck size={16} aria-hidden />
          <p style={{ margin: 0 }}>{submitted.count} conversion{submitted.count === 1 ? "" : "s"} queued. Track progress in <a href="#queue">your queue</a> below — you can keep adding files meanwhile.</p>
        </div>
      )}

      {items.length > 0 && (
        <div className="workspace fade-in">
          <section className="panel panel-pad" aria-label="Conversion settings">
            <div className="row" style={{ marginBottom: 12 }}>
              <h2 style={{ fontSize: 17, margin: 0 }}>Your items <span className="muted">({items.length})</span></h2>
              <span className="spacer" />
              {items.length > 1 && sel?.status === "ready" && <button className="btn btn-sm" onClick={applyToAll}>Apply these settings to all</button>}
            </div>
            <ul className="items" style={{ listStyle: "none", padding: 0, margin: "0 0 18px" }}>
              {items.map((it) => <ItemRow key={it.key} it={it} selected={it.key === sel?.key} onSelect={() => setSelected(it.key)} onRemove={() => removeItem(it.key)}
                onRetry={() => it.file && startUpload(it.key, it.file, it.uploadId)} onCancel={() => { handles.current.get(it.key)?.cancel(); removeItem(it.key); }} />)}
            </ul>
            {sel && sel.status === "ready" ? (
              <>
                <hr className="divider" />
                <h3 style={{ fontSize: 15, marginBottom: 12 }} className="row"><span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: "100%" }}>Settings for “{sel.name}”</span></h3>
                <SettingsPanel
                  options={sel.options}
                  onChange={(patch) => setOptions(sel.key, (o) => ({ ...o, ...patch }))}
                  onEdit={(patch) => setOptions(sel.key, (o) => ({ ...o, edit: { ...o.edit, ...patch } }))}
                  media={sel.media} qualities={sel.qualities} plan={sel.plan} caps={caps} mode={mode} setMode={setMode}
                  category={cat} extra={extra} previewUrl={sel.previewUrl} sourceKind={sel.kind}
                  setCategory={(c, e) => setOptions(sel.key, (o) => switchCategory(o, c, e ?? null))}
                />
              </>
            ) : sel ? (
              <p className="muted">{sel.status === "error" ? "Fix or remove this item to continue." : "Settings appear once the file has been inspected."}</p>
            ) : null}
          </section>

          <aside className="summary-col" aria-label="Summary">
            {sel?.status === "ready" && <PlanSummary item={sel} />}
            <div className="panel panel-pad stack">
              {submitError && <ErrorNotice error={submitError} />}
              {invalid.length > 0 && <p className="field-error" role="alert">{invalid.length} item{invalid.length > 1 ? "s have" : " has"} settings that need attention.</p>}
              <button className="btn btn-primary btn-lg" disabled={!canSubmit} onClick={submit} aria-describedby="convert-hint">
                {submitting ? <LoaderCircle size={18} className="spin" aria-hidden /> : <ArrowRight size={18} aria-hidden />}
                {submitting ? "Starting…" : `Convert ${ready.length > 1 ? `${ready.length} items` : ready.length === 1 ? "" : ""}`.trim()}
              </button>
              <p id="convert-hint" className="muted" style={{ margin: 0 }}>
                {busy ? "Waiting for uploads and inspections to finish. Ready items can be converted now." : `Files are deleted automatically after ${Math.round(caps.limits.file_ttl_seconds / 3600)} hours, or immediately with “Delete now”.`}
              </p>
            </div>
          </aside>
        </div>
      )}
    </div>
  );
}

function switchCategory(o: Options, c: Category, e: Extra | null): Options {
  const n = { ...o };
  if (c === "video") Object.assign(n, { output: ["mp4", "webm", "mov", "mkv"].includes(o.output) ? o.output : "mp4", include_audio: true });
  if (c === "audio") Object.assign(n, { output: ["mp3", "m4a", "wav", "flac", "ogg"].includes(o.output) ? o.output : "mp3" });
  if (c === "additional") {
    const ex = e ?? "gif";
    if (ex === "gif") Object.assign(n, { output: "gif" });
    if (ex === "audio-only") Object.assign(n, { output: ["mp3", "m4a", "wav", "flac", "ogg"].includes(o.output) ? o.output : "mp3" });
    if (ex === "video-only") Object.assign(n, { output: ["mp4", "webm", "mov", "mkv"].includes(o.output) ? o.output : "mp4", include_audio: false });
  }
  if (n.output !== o.output) Object.assign(n, { video_codec: "auto", audio_codec: "auto", audio_bitrate_kbps: null, sample_rate: null });
  return n;
}

/* ============================== sub-components ============================== */

function ItemRow({ it, selected, onSelect, onRemove, onRetry, onCancel }: { it: Item; selected: boolean; onSelect: () => void; onRemove: () => void; onRetry: () => void; onCancel: () => void }) {
  const v = it.media?.video;
  const a = it.media?.audio;
  const meta = [
    it.platform,
    it.size != null ? formatBytes(it.size) : null,
    it.media?.duration ? formatDuration(it.media.duration) : null,
    v?.width ? `${v.width}×${v.height}` : null,
    v?.fps ? `${v.fps} fps` : null,
    v?.codec ? v.codec.toUpperCase() : null,
    a?.codec ? `${a.codec.toUpperCase()}${a.sample_rate ? ` ${a.sample_rate / 1000} kHz` : ""}` : null,
  ].filter(Boolean);
  const Icon = it.kind === "url" ? Globe : it.media && !it.media.has_video ? AudioLines : Film;
  return (
    <li>
      <div className="item" role="button" tabIndex={0} aria-current={selected} onClick={onSelect} onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onSelect())}
        aria-label={`${it.name}, ${it.status === "ready" ? "ready" : it.status}`}>
        <div className="thumb">{it.thumbnail ? <img src={it.thumbnail} alt="" referrerPolicy="no-referrer" /> : <Icon size={20} aria-hidden />}</div>
        <div style={{ minWidth: 0 }}>
          <div className="item-title" title={it.name}>{it.name}</div>
          <div className="item-meta">
            {it.status === "uploading" && <span>Uploading {Math.round(it.progress * 100)}% {it.size ? `of ${formatBytes(it.size)}` : ""}</span>}
            {it.status === "verifying" && <span>Inspecting file contents…</span>}
            {it.status === "resolving" && <span>Fetching item details…</span>}
            {it.status === "ready" && meta.map((m, i) => <span key={i}>{m}</span>)}
            {it.status === "error" && <span style={{ color: "var(--danger)" }}>{it.error?.message}</span>}
          </div>
          {(it.status === "uploading" || it.status === "verifying" || it.status === "resolving") && (
            <div className={`progress ${it.status !== "uploading" ? "indeterminate" : ""}`} role="progressbar" aria-label={`Upload progress for ${it.name}`}
              aria-valuemin={0} aria-valuemax={100} aria-valuenow={it.status === "uploading" ? Math.round(it.progress * 100) : undefined}>
              <span style={it.status === "uploading" ? { width: `${it.progress * 100}%` } : undefined} />
            </div>
          )}
          {it.status === "ready" && it.plan && !it.plan.valid && <div className="field-error" style={{ marginTop: 4 }}>{Object.values(it.plan.errors)[0]}</div>}
          {it.planError && <div className="field-error" style={{ marginTop: 4 }}>{it.planError.message}</div>}
          {it.status === "error" && it.error?.action && <div className="muted" style={{ marginTop: 2 }}>{it.error.action}</div>}
        </div>
        <div className="row" style={{ gap: 4 }} onClick={(e) => e.stopPropagation()}>
          {it.status === "ready" && (!it.plan || it.plan.valid) && <CircleCheck size={18} style={{ color: "var(--success)" }} aria-label="Ready" />}
          {it.status === "error" && it.file && it.error?.retryable !== false && it.error?.code !== "file_too_large" && it.error?.code !== "unsupported_input" && it.error?.code !== "unsupported_codec" && (
            <button className="btn btn-sm btn-ghost" onClick={onRetry} aria-label={`Resume upload of ${it.name}`}><RefreshCw size={15} aria-hidden /> Resume</button>
          )}
          {it.status === "uploading" ? (
            <button className="btn btn-sm btn-ghost btn-icon" onClick={onCancel} aria-label={`Cancel upload of ${it.name}`}><X size={16} aria-hidden /></button>
          ) : (
            <button className="btn btn-sm btn-ghost btn-icon" onClick={onRemove} aria-label={`Remove ${it.name}`}><Trash size={15} aria-hidden /></button>
          )}
        </div>
      </div>
    </li>
  );
}

function PlanSummary({ item }: { item: Item }) {
  const p = item.plan;
  if (item.planError) return <ErrorNotice error={item.planError} />;
  if (!p) return <div className="panel panel-pad"><div className="row muted"><LoaderCircle size={16} className="spin" aria-hidden /> Checking settings…</div></div>;
  const s = p.summary;
  const size = formatRange(p.estimate.size_low, p.estimate.size_high);
  const time = p.estimate.seconds_low != null ? `~${formatSeconds(p.estimate.seconds_low)} – ${formatSeconds(p.estimate.seconds_high!)}` : null;
  return (
    <div className="panel panel-pad stack" aria-live="polite">
      <div className="row"><h3 style={{ fontSize: 15, margin: 0 }}>Output</h3><span className="spacer" />{p.stream_copy && <span className="badge badge-success">No re-encode</span>}{p.queue === "gpu" && <span className="badge badge-accent">GPU</span>}</div>
      <dl className="kv">
        <dt>Format</dt><dd>{s.container}{s.video_codec ? ` · ${s.video_codec}` : ""}{s.audio_codec ? ` · ${s.audio_codec}` : ""}</dd>
        {s.resolution && <><dt>Resolution</dt><dd>{p.out_width ? s.resolution : "Same as source"}{s.upscale ? " (upscaled)" : ""}</dd></>}
        {s.fps_out != null && s.fps_method && <><dt>Frame rate</dt><dd>{s.fps_in && s.fps_in !== s.fps_out ? `${s.fps_in} → ` : ""}{s.fps_out} fps</dd></>}
        {s.fps_method && s.fps_method !== "Preserve original frame rate" && <><dt>Motion</dt><dd>{s.fps_method}</dd></>}
        {s.audio && <><dt>Audio</dt><dd>{s.audio}</dd></>}
        <dt>Duration</dt><dd>{formatDuration(s.duration)}</dd>
        <dt>Estimated size</dt><dd>{size ? <>{size} <span className="muted">(estimate)</span></> : "—"}</dd>
        <dt>Processing time</dt><dd>{time ?? <span className="muted">Not enough data</span>}</dd>
        <dt>File name</dt><dd>{p.filename}</dd>
      </dl>
      {p.stages.length > 0 && (
        <p className="muted" style={{ margin: 0 }}>Steps: {p.stages.filter((x) => x !== "queued" && x !== "ready").map((x) => STAGE_LABELS[x] ?? x).join(" → ")}</p>
      )}
      {Object.entries(p.errors).map(([k, v]) => <div key={k} className="notice notice-danger" role="alert"><TriangleExclamationPoint size={16} aria-hidden /><p style={{ margin: 0 }}>{v}</p></div>)}
      {p.warnings.map((w, i) => <div key={i} className="notice notice-warning"><TriangleExclamationPoint size={16} aria-hidden /><p style={{ margin: 0 }}>{w}</p></div>)}
      {p.notes.map((n, i) => <div key={i} className="notice"><Info size={16} aria-hidden /><p style={{ margin: 0 }}>{n}</p></div>)}
    </div>
  );
}

function LinkPanel({ inputRef, onAdd, onSwitchToUpload, maxItems }: { inputRef: React.RefObject<HTMLInputElement | null>; onAdd: (a: Analysis, e: Entry[]) => void; onSwitchToUpload: () => void; maxItems: number }) {
  const { caps } = useCapabilities();
  const [url, setUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ApiErrorBody | null>(null);
  const [inlineError, setInlineError] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const canPaste = typeof navigator !== "undefined" && !!navigator.clipboard?.readText;

  const validate = (v: string): string | null => {
    const t = v.trim();
    if (!t) return "Paste a link to analyze.";
    try {
      const u = new URL(t.includes("://") ? t : `https://${t}`);
      if (!["http:", "https:"].includes(u.protocol)) return "Only http:// and https:// links are supported.";
      if (!u.hostname.includes(".")) return "That doesn't look like a valid web address.";
    } catch {
      return "That doesn't look like a valid web address.";
    }
    return null;
  };

  const analyze = async (e?: React.FormEvent) => {
    e?.preventDefault();
    const v = validate(url);
    setInlineError(v);
    if (v) return;
    setLoading(true);
    setError(null);
    setAnalysis(null);
    try {
      const a = await api<Analysis>("/api/analyze", { method: "POST", json: { url: url.trim() } });
      setAnalysis(a);
      setPicked(new Set(a.kind === "single" ? [a.entries[0].id] : []));
    } catch (err) {
      setError(toApiError(err));
    } finally {
      setLoading(false);
    }
  };

  const paste = async () => {
    try {
      const t = await navigator.clipboard.readText();
      if (t) {
        setUrl(t.trim());
        setInlineError(null);
      }
    } catch {
      setInlineError("Clipboard access was blocked. Paste with Ctrl+V / ⌘V instead.");
    }
  };

  const add = () => {
    if (!analysis) return;
    onAdd(analysis, analysis.entries.filter((e) => picked.has(e.id)));
    setAnalysis(null);
    setUrl("");
  };

  const sources = caps?.sources.filter((s) => s.id !== "direct") ?? [];
  const single = analysis?.kind === "single" ? analysis.entries[0] : null;

  return (
    <div className="stack">
      <form onSubmit={analyze} noValidate>
        <div className="url-row">
          <div className="url-input-wrap">
            <Link2 size={18} className="lead" aria-hidden />
            <label htmlFor="url-input" className="sr-only">Media link</label>
            <input id="url-input" ref={inputRef} className="input" type="url" inputMode="url" autoComplete="off" spellCheck={false} placeholder="Paste a video or audio link"
              value={url} onChange={(e) => { setUrl(e.target.value); if (inlineError) setInlineError(null); }}
              aria-invalid={!!inlineError} aria-describedby={inlineError ? "url-error" : "url-hint"} />
            {canPaste && <button type="button" className="btn btn-sm btn-ghost paste" onClick={paste} aria-label="Paste from clipboard"><ClipboardPaste size={16} aria-hidden /></button>}
          </div>
          <button className="btn btn-primary btn-lg" type="submit" disabled={loading}>
            {loading ? <LoaderCircle size={18} className="spin" aria-hidden /> : <Sparkles size={18} aria-hidden />} {loading ? "Analyzing…" : "Analyze link"}
          </button>
        </div>
        {inlineError ? <p id="url-error" className="field-error" role="alert" style={{ margin: "8px 0 0" }}>{inlineError}</p>
          : <p id="url-hint" className="muted" style={{ margin: "8px 0 0" }}>Only convert media you own or are authorized to download. Private, DRM-protected, and login-only content isn&apos;t supported.</p>}
      </form>
      {!analysis && !error && (
        <div className="platforms" aria-label="Source platforms">
          {sources.map((s) => (
            <span key={s.id} className={`badge ${s.status === "supported" ? "badge-success" : s.status === "limited" ? "badge-warning" : "badge-danger"}`} title={s.notes}>
              {s.name}<span className="sr-only">: {s.status.replace("_", " ")}</span>
            </span>
          ))}
          <span className="badge">Direct media links</span>
          <a href="/supported" className="muted" style={{ marginLeft: 4 }}>What “limited” means</a>
        </div>
      )}
      {error && (
        <ErrorNotice error={error}>
          {!["invalid_url"].includes(error.code) && (
            <button className="btn btn-sm" style={{ marginTop: 10 }} onClick={onSwitchToUpload}><Upload size={15} aria-hidden /> Upload a file instead</button>
          )}
        </ErrorNotice>
      )}
      {analysis && (
        <div className="panel fade-in" style={{ padding: 16, boxShadow: "none", background: "var(--panel-2)" }}>
          <div className="analysis-card">
            <div className="thumb thumb-lg">{analysis.thumbnail ? <img src={analysis.thumbnail} alt="" referrerPolicy="no-referrer" /> : <Film size={28} aria-hidden />}</div>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div className="row" style={{ gap: 6, marginBottom: 4 }}>
                <span className="badge badge-accent">{analysis.platform}</span>
                {analysis.kind !== "single" && <span className="badge">{analysis.kind === "collection" ? "Collection" : "Multiple items"} · {analysis.entry_count}</span>}
              </div>
              <h3 style={{ fontSize: 16, margin: "0 0 6px", overflowWrap: "anywhere" }}>{analysis.title}</h3>
              {single && <SingleDetails e={single} />}
            </div>
          </div>
          {analysis.kind !== "single" && (
            <div className="stack" style={{ marginTop: 14 }}>
              <div className="notice notice-info"><Info size={16} aria-hidden /><p style={{ margin: 0 }}>
                {analysis.kind === "collection" ? "This is a playlist, channel, or account page. Nothing is selected automatically — " : "This post contains multiple media items. "}
                choose up to {maxItems} item{maxItems > 1 ? "s" : ""} to convert.{analysis.truncated ? ` Showing the first ${analysis.entries.length} of ${analysis.entry_count}.` : ""}
              </p></div>
              <div className="row">
                <span className="muted">{picked.size} of {analysis.entries.length} selected</span>
                <span className="spacer" />
                {picked.size > 0 && <button className="btn btn-sm btn-ghost" onClick={() => setPicked(new Set())}>Clear</button>}
              </div>
              <div className="entry-list" role="group" aria-label="Items in this link">
                {analysis.entries.map((e) => {
                  const on = picked.has(e.id);
                  const disabled = !on && picked.size >= maxItems;
                  return (
                    <label key={e.id} className="entry" style={disabled ? { opacity: 0.5 } : undefined}>
                      <input type="checkbox" checked={on} disabled={disabled} onChange={() => setPicked((s) => { const n = new Set(s); if (n.has(e.id)) n.delete(e.id); else n.add(e.id); return n; })} />
                      <div className="thumb">{e.thumbnail ? <img src={e.thumbnail} alt="" referrerPolicy="no-referrer" /> : <Film size={18} aria-hidden />}</div>
                      <span style={{ minWidth: 0, flex: 1 }}>
                        <span className="item-title" style={{ display: "block" }}>{e.title}</span>
                        <span className="muted">{e.duration ? formatDuration(e.duration) : "Duration unknown"}{e.qualities?.[0] ? ` · up to ${e.qualities[0].short_side}p` : ""}</span>
                      </span>
                    </label>
                  );
                })}
              </div>
            </div>
          )}
          <div className="row" style={{ marginTop: 14 }}>
            <button className="btn btn-primary" disabled={picked.size === 0} onClick={add}><Plus size={16} aria-hidden /> {picked.size > 1 ? `Add ${picked.size} items` : "Choose format & quality"}</button>
            <button className="btn btn-ghost" onClick={() => setAnalysis(null)}>Cancel</button>
          </div>
        </div>
      )}
    </div>
  );
}

function SingleDetails({ e }: { e: Entry }) {
  const m = e.media;
  const top = e.qualities?.[0];
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="item-meta" style={{ fontSize: 13 }}>
        {e.uploader && <span>{e.uploader}</span>}
        <span>{e.duration ? formatDuration(e.duration) : "Duration unknown"}</span>
        {top && <span>Up to {top.short_side}p{top.fps ? ` · ${top.fps} fps` : ""}</span>}
        {m?.has_audio && !m.has_video && <span>Audio only</span>}
        {top?.size && <span>~{formatBytes(top.size)} at best quality (estimate)</span>}
      </div>
      {e.qualities && e.qualities.length > 0 && (
        <div className="chips" aria-label="Available video qualities">
          {e.qualities.slice(0, 8).map((q) => <span key={q.short_side} className="badge">{q.short_side}p{q.fps > 30 ? q.fps : ""}{q.codecs.length ? ` · ${q.codecs.slice(0, 2).join("/")}` : ""}</span>)}
        </div>
      )}
      {e.audio_streams && e.audio_streams.length > 0 && (
        <div className="muted">Audio: {e.audio_streams.slice(0, 3).map((a) => `${a.codec}${a.bitrate_kbps ? ` ${a.bitrate_kbps} kbps` : ""}${a.language ? ` (${a.language})` : ""}`).join(", ")}</div>
      )}
      {e.drm && <div className="notice notice-warning"><TriangleExclamationPoint size={16} aria-hidden /><p style={{ margin: 0 }}>Some streams are DRM-protected and will be skipped.</p></div>}
    </div>
  );
}

function UploadPanel({ onFiles }: { onFiles: (f: FileList | File[]) => void }) {
  const { caps } = useCapabilities();
  const [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const limits = caps?.limits;
  return (
    <div className="stack">
      <div className={`dropzone ${drag ? "drag" : ""}`} role="button" tabIndex={0} aria-describedby="upload-limits"
        onClick={() => input.current?.click()} onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), input.current?.click())}
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); if (e.dataTransfer.files.length) onFiles(e.dataTransfer.files); }}>
        <span className="icon-circle"><Upload size={24} aria-hidden /></span>
        <strong>Drop files here or click to browse</strong>
        <span className="subtle">Video: MP4, MOV, MKV, WEBM, AVI, M4V · Audio: MP3, WAV, AAC, M4A, FLAC, OGG</span>
        <input ref={input} type="file" multiple accept={ACCEPT} className="sr-only" tabIndex={-1} aria-hidden onChange={(e) => { if (e.target.files?.length) onFiles(e.target.files); e.target.value = ""; }} />
      </div>
      {limits && (
        <p id="upload-limits" className="muted" style={{ margin: 0 }}>
          Up to {formatBytes(limits.max_upload_bytes)} and {Math.round(limits.max_duration_seconds / 60)} minutes per file · {limits.max_files_per_batch} files per batch · files are checked by content, not by extension · deleted automatically after {Math.round(limits.file_ttl_seconds / 3600)} h.
        </p>
      )}
    </div>
  );
}

export { ErrorNotice };
