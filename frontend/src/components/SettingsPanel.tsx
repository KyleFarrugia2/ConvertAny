"use client";

import { useId } from "react";
import { AudioLines, ChevronDown, Clapperboard, Cpu, Film, Gauge, Image as ImageIcon, Info, Maximize2, RotateCw, Scissors, SlidersHorizontal, Sparkles, TriangleExclamationPoint, Volume2, Zap } from "lucide-react";
import type { Capabilities, MediaInfo, Options, OutputFormat, PlanResult, Quality_ } from "@/lib/types";
import { formatBytes, formatSeconds } from "@/lib/format";
import { TrimEditor } from "./TrimEditor";

export type Category = "video" | "audio" | "additional";
export type Extra = "gif" | "audio-only" | "video-only";

const VIDEO_FORMATS: OutputFormat[] = ["mp4", "webm", "mov", "mkv"];
const AUDIO_FORMATS: OutputFormat[] = ["mp3", "m4a", "wav", "flac", "ogg"];
const RESOLUTIONS = ["360", "480", "720", "1080", "1440", "2160"] as const;
const MP3_BITRATES = [64, 96, 128, 192, 256, 320];
const LOSSY_BITRATES: Record<string, number[]> = { mp3: MP3_BITRATES, aac: [64, 96, 128, 160, 192, 256, 320], opus: [64, 96, 128, 160, 192, 256], vorbis: [64, 96, 128, 160, 192, 256, 320] };

const QUALITY_PRESETS = [
  { id: "original", t: "Original / best available", d: "Preserves source quality where possible — copies streams without re-encoding when it can. Not lossless when re-encoding is required." },
  { id: "high", t: "High quality", d: "Visually close to the source with sensible file sizes." },
  { id: "balanced", t: "Balanced", d: "Good quality, noticeably smaller files." },
  { id: "small", t: "Smaller file", d: "Prioritizes size. Some visible or audible loss." },
] as const;

export function categoryOf(o: Options): { cat: Category; extra: Extra | null } {
  if (o.output === "gif") return { cat: "additional", extra: "gif" };
  if (VIDEO_FORMATS.includes(o.output) && !o.include_audio) return { cat: "additional", extra: "video-only" };
  if (VIDEO_FORMATS.includes(o.output)) return { cat: "video", extra: null };
  return { cat: "audio", extra: null };
}

interface Props {
  options: Options;
  onChange: (patch: Partial<Options>) => void;
  onEdit: (patch: Partial<Options["edit"]>) => void;
  media: MediaInfo | null | undefined;
  qualities?: Quality_[];
  plan?: PlanResult | null;
  caps: Capabilities;
  mode: "simple" | "advanced";
  setMode: (m: "simple" | "advanced") => void;
  category: Category;
  extra: Extra | null;
  setCategory: (c: Category, e?: Extra | null) => void;
  previewUrl?: string;
  sourceKind: "upload" | "url";
}

function FieldError({ id, msg }: { id: string; msg?: string }) {
  return msg ? <span id={id} className="field-error" role="alert">{msg}</span> : null;
}

function Radio<T extends string | number>({ value, current, onSelect, children, disabled, title, className = "chip" }: { value: T; current: T; onSelect: (v: T) => void; children: React.ReactNode; disabled?: boolean; title?: string; className?: string }) {
  return (
    <button type="button" role="radio" aria-checked={value === current} className={className} disabled={disabled} title={title} onClick={() => onSelect(value)}
      tabIndex={value === current ? 0 : -1}
      onKeyDown={(e) => {
        if (!["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp"].includes(e.key)) return;
        e.preventDefault();
        const group = (e.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="radio"]:not(:disabled)')) ?? [];
        const arr = Array.from(group);
        const i = arr.indexOf(e.currentTarget);
        const next = arr[(i + (e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : arr.length - 1)) % arr.length];
        next?.focus();
        next?.click();
      }}>
      {children}
    </button>
  );
}

export function SettingsPanel(p: Props) {
  const { options: o, onChange, onEdit, media, caps, plan, mode } = p;
  const uid = useId();
  const err = plan?.errors ?? {};
  const fmt = caps.formats[o.output];
  const isVideoOut = VIDEO_FORMATS.includes(o.output);
  const isAudioOut = AUDIO_FORMATS.includes(o.output);
  const isGif = o.output === "gif";
  const lossless = o.output === "wav" || o.output === "flac";
  const srcV = media?.video;
  const srcShort = srcV?.width && srcV?.height ? Math.min(srcV.width, srcV.height) : null;
  const srcFps = srcV?.fps ?? null;
  const duration = media?.duration ?? null;
  const hasVideo = !!media?.has_video;
  const hasAudio = !!media?.has_audio;
  const interp = caps.enhancement.interpolation;
  const aiUp = caps.enhancement.upscale.ai;

  const chosenACodec = o.audio_codec !== "auto" ? o.audio_codec : (fmt.audio_codecs[0]?.id as string | undefined);
  const audioBitrates = chosenACodec ? LOSSY_BITRATES[chosenACodec] : undefined;

  const pickFormat = (f: OutputFormat) => onChange({ output: f, video_codec: "auto", audio_codec: "auto", audio_bitrate_kbps: null, sample_rate: null });

  const formatChips = (list: OutputFormat[]) => (
    <div className="chips" role="radiogroup" aria-label="Output format" aria-describedby={err.output ? `${uid}-out-err` : undefined}>
      {list.map((f) => {
        const c = caps.formats[f];
        const blocked = !c.available || (c.category === "video" && !hasVideo && !!media) || (c.category === "audio" && !hasAudio && !!media);
        return (
          <Radio key={f} value={f} current={o.output} onSelect={pickFormat} disabled={blocked}
            title={!c.available ? "Not available on this server" : blocked ? (c.category === "audio" ? "This source has no audio" : "This source has no video") : undefined}>
            {c.label}
            {c.recommended && <span className="chip-tag">Recommended</span>}
          </Radio>
        );
      })}
    </div>
  );

  /* ------------------------------ resolution ------------------------------ */
  const resolutionSelect = (
    <div className="field">
      <label htmlFor={`${uid}-res`}>Resolution</label>
      <select id={`${uid}-res`} className="select" value={o.resolution} aria-invalid={!!err.resolution} aria-describedby={err.resolution ? `${uid}-res-err` : `${uid}-res-hint`}
        onChange={(e) => {
          const r = e.target.value as Options["resolution"];
          const needsUp = srcShort != null && r !== "original" && Number(r) > srcShort;
          onChange({ resolution: r, upscale: needsUp ? (o.upscale === "none" ? "standard" : o.upscale) : "none" });
        }}>
        <option value="original">Original{srcShort ? ` (${srcShort}p)` : ""}</option>
        {RESOLUTIONS.map((r) => {
          const n = Number(r);
          const up = srcShort != null && n > srcShort;
          const tooBig = n > caps.limits.max_output_height;
          const q = p.qualities?.find((x) => x.short_side === n);
          return (
            <option key={r} value={r} disabled={tooBig}>
              {r === "2160" ? "2160p / 4K" : `${r}p`}
              {up ? " — upscaled (not native)" : q ? ` — native${q.fps ? ` · ${q.fps} fps` : ""}${q.size ? ` · ~${formatBytes(q.size)}` : ""}` : srcShort ? " — downscaled" : ""}
            </option>
          );
        })}
      </select>
      <span id={`${uid}-res-hint`} className="field-hint">Resolution refers to the shorter side (e.g. 1080p vertical video is 1080×1920).</span>
      <FieldError id={`${uid}-res-err`} msg={err.resolution} />
    </div>
  );

  /* ------------------------------ quality ------------------------------ */
  const qualityCards = !lossless && (
    <div className="field">
      <span id={`${uid}-q`} className="sr-only">Quality</span>
      <div className="option-cards cards-2" role="radiogroup" aria-labelledby={`${uid}-q`}>
        {QUALITY_PRESETS.map((q) => (
          <Radio key={q.id} value={q.id} current={o.quality} onSelect={(v) => onChange({ quality: v as Options["quality"] })} className="option-card">
            <span className="t">{q.t}</span>
            <span className="d">{q.d}</span>
          </Radio>
        ))}
      </div>
    </div>
  );

  return (
    <div className="stack" style={{ gap: 16 }}>
      {/* ---------- category ---------- */}
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div className="segmented" role="tablist" aria-label="Output type">
          {([["video", "Video", Film], ["audio", "Audio", AudioLines], ["additional", "Additional", Sparkles]] as const).map(([c, label, Icon]) => (
            <button key={c} type="button" role="tab" aria-selected={p.category === c} onClick={() => p.setCategory(c)}
              disabled={(c === "video" && !!media && !hasVideo) || (c === "audio" && !!media && !hasAudio)}>
              <Icon size={16} aria-hidden /> {label}
            </button>
          ))}
        </div>
        <div className="segmented" role="radiogroup" aria-label="Settings mode">
          <button type="button" role="radio" aria-checked={mode === "simple"} onClick={() => p.setMode("simple")}>Simple</button>
          <button type="button" role="radio" aria-checked={mode === "advanced"} onClick={() => p.setMode("advanced")}><SlidersHorizontal size={15} aria-hidden /> Advanced</button>
        </div>
      </div>

      {p.category === "video" && formatChips(VIDEO_FORMATS)}
      {p.category === "audio" && formatChips(AUDIO_FORMATS)}
      {p.category === "additional" && (
        <div className="stack">
          <div className="option-cards" role="radiogroup" aria-label="Additional output">
            <Radio value="gif" current={p.extra ?? "gif"} onSelect={() => p.setCategory("additional", "gif")} className="option-card" disabled={!!media && !hasVideo}>
              <span className="t"><ImageIcon size={16} aria-hidden /> GIF</span>
              <span className="d">Short silent loops, up to {caps.limits.max_gif_seconds}s.</span>
            </Radio>
            <Radio value="audio-only" current={p.extra ?? "gif"} onSelect={() => p.setCategory("additional", "audio-only")} className="option-card" disabled={!!media && !hasAudio}>
              <span className="t"><AudioLines size={16} aria-hidden /> Audio only</span>
              <span className="d">Extract the soundtrack from a video.</span>
            </Radio>
            <Radio value="video-only" current={p.extra ?? "gif"} onSelect={() => p.setCategory("additional", "video-only")} className="option-card" disabled={!!media && !hasVideo}>
              <span className="t"><Clapperboard size={16} aria-hidden /> Video only</span>
              <span className="d">Keep the picture, remove the audio.</span>
            </Radio>
          </div>
          {p.extra === "audio-only" && formatChips(AUDIO_FORMATS)}
          {p.extra === "video-only" && formatChips(VIDEO_FORMATS)}
        </div>
      )}
      <FieldError id={`${uid}-out-err`} msg={err.output} />

      {/* ---------- simple controls ---------- */}
      {!isGif && qualityCards}
      {lossless && (
        <div className="notice"><Info size={16} aria-hidden /><p>{o.output.toUpperCase()} is lossless, so there are no quality presets. Converting a compressed source to a lossless format keeps what&apos;s there but can&apos;t restore detail that was already removed.</p></div>
      )}
      {mode === "simple" && isVideoOut && resolutionSelect}
      {mode === "simple" && o.output === "mp3" && (
        <div className="field">
          <label htmlFor={`${uid}-mp3`}>MP3 bitrate</label>
          <select id={`${uid}-mp3`} className="select" value={o.audio_bitrate_kbps ?? ""} onChange={(e) => onChange({ audio_bitrate_kbps: e.target.value ? Number(e.target.value) : null })}>
            <option value="">Automatic (from quality)</option>
            {MP3_BITRATES.map((b) => <option key={b} value={b}>{b} kbps</option>)}
          </select>
        </div>
      )}

      {isGif && (
        <div className="grid-2">
          <div className="field">
            <label htmlFor={`${uid}-gfps`}>GIF frame rate</label>
            <select id={`${uid}-gfps`} className="select" value={o.gif_fps} onChange={(e) => onChange({ gif_fps: Number(e.target.value) })}>
              {[8, 10, 12, 15, 20, 24, 30].map((f) => <option key={f} value={f}>{f} fps</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor={`${uid}-gw`}>GIF width</label>
            <select id={`${uid}-gw`} className="select" value={o.gif_width} onChange={(e) => onChange({ gif_width: Number(e.target.value) })}>
              {[240, 320, 400, 480, 640, 800, 1080].map((w) => <option key={w} value={w}>{w}px</option>)}
            </select>
          </div>
        </div>
      )}

      {/* ---------- advanced ---------- */}
      {mode === "advanced" && (
        <div>
          {isVideoOut && (
            <details className="section">
              <summary><Film size={17} aria-hidden /> Video settings <span className="sum-hint">{o.resolution === "original" ? "Original" : `${o.resolution}p`} · {o.video_codec === "auto" ? "Auto codec" : o.video_codec.toUpperCase()}</span><ChevronDown size={18} className="chev" aria-hidden /></summary>
              <div className="section-body">
                <div className="grid-2">
                  <div className="field">
                    <label htmlFor={`${uid}-vc`}>Video codec</label>
                    <select id={`${uid}-vc`} className="select" value={o.video_codec} aria-invalid={!!err.video_codec} aria-describedby={err.video_codec ? `${uid}-vc-err` : `${uid}-vc-hint`}
                      onChange={(e) => onChange({ video_codec: e.target.value as Options["video_codec"] })}>
                      <option value="auto">Automatic ({fmt.video_codecs[0]?.label ?? "default"})</option>
                      {fmt.video_codecs.map((c) => <option key={c.id} value={c.id} disabled={!c.available}>{c.label}{!c.available ? " — unavailable" : ""}</option>)}
                    </select>
                    <span id={`${uid}-vc-hint`} className="field-hint">{fmt.video_codecs.find((c) => c.id === o.video_codec)?.note ?? (o.output === "mp4" ? "H.264 with AAC audio plays almost everywhere." : "The most compatible codec for this container is used.")}</span>
                    <FieldError id={`${uid}-vc-err`} msg={err.video_codec} />
                  </div>
                  {resolutionSelect}
                </div>
                <div className="grid-2">
                  <div className="field">
                    <label htmlFor={`${uid}-vbr`}>Target bitrate (optional)</label>
                    <input id={`${uid}-vbr`} className="input" type="number" min={100} max={200000} step={100} placeholder="Automatic"
                      value={o.video_bitrate_kbps ?? ""} onChange={(e) => onChange({ video_bitrate_kbps: e.target.value ? Number(e.target.value) : null })} />
                    <span className="field-hint">In kbps. Leave empty to use the quality preset (recommended).</span>
                  </div>
                  <div className="stack" style={{ paddingTop: 22 }}>
                    <label className="check"><input type="checkbox" checked={o.keep_aspect} onChange={(e) => onChange({ keep_aspect: e.target.checked })} /> <span>Preserve aspect ratio<span className="field-hint">Never stretches the picture.</span></span></label>
                    <label className="check"><input type="checkbox" checked={o.include_audio} disabled={!hasAudio && !!media} onChange={(e) => onChange({ include_audio: e.target.checked })} /> <span>Include audio</span></label>
                  </div>
                </div>
              </div>
            </details>
          )}

          {(isAudioOut || (isVideoOut && o.include_audio)) && hasAudio !== false && (
            <details className="section">
              <summary><Volume2 size={17} aria-hidden /> Audio settings <span className="sum-hint">{lossless ? "Lossless" : o.audio_bitrate_kbps ? `${o.audio_bitrate_kbps} kbps` : "Auto bitrate"}</span><ChevronDown size={18} className="chev" aria-hidden /></summary>
              <div className="section-body">
                <div className="grid-2">
                  {fmt.audio_codecs.length > 1 && (
                    <div className="field">
                      <label htmlFor={`${uid}-ac`}>Audio codec</label>
                      <select id={`${uid}-ac`} className="select" value={o.audio_codec} aria-invalid={!!err.audio_codec} onChange={(e) => onChange({ audio_codec: e.target.value as Options["audio_codec"], audio_bitrate_kbps: null })}>
                        <option value="auto">Automatic ({fmt.audio_codecs[0]?.label})</option>
                        {fmt.audio_codecs.map((c) => <option key={c.id} value={c.id} disabled={!c.available}>{c.label}</option>)}
                      </select>
                      <FieldError id={`${uid}-ac-err`} msg={err.audio_codec} />
                    </div>
                  )}
                  {audioBitrates && (
                    <div className="field">
                      <label htmlFor={`${uid}-abr`}>Bitrate</label>
                      <select id={`${uid}-abr`} className="select" value={o.audio_bitrate_kbps ?? ""} onChange={(e) => onChange({ audio_bitrate_kbps: e.target.value ? Number(e.target.value) : null })}>
                        <option value="">Automatic (from quality)</option>
                        {audioBitrates.map((b) => <option key={b} value={b}>{b} kbps</option>)}
                      </select>
                      <span className="field-hint">A higher bitrate can&apos;t restore detail missing from a compressed source.</span>
                    </div>
                  )}
                  {o.output === "wav" && (
                    <div className="field">
                      <label htmlFor={`${uid}-bd`}>Bit depth</label>
                      <select id={`${uid}-bd`} className="select" value={o.wav_bit_depth} onChange={(e) => onChange({ wav_bit_depth: Number(e.target.value) as 16 | 24 })}>
                        <option value={16}>16-bit</option><option value={24}>24-bit</option>
                      </select>
                    </div>
                  )}
                  {o.output === "flac" && (
                    <div className="field">
                      <label htmlFor={`${uid}-fc`}>Compression level</label>
                      <select id={`${uid}-fc`} className="select" value={o.flac_compression} onChange={(e) => onChange({ flac_compression: Number(e.target.value) })}>
                        {[0, 2, 5, 8, 12].map((l) => <option key={l} value={l}>{l}{l === 5 ? " (default)" : l === 12 ? " (smallest, slowest)" : l === 0 ? " (fastest)" : ""}</option>)}
                      </select>
                      <span className="field-hint">Affects file size and speed only — FLAC is always lossless.</span>
                    </div>
                  )}
                  <div className="field">
                    <label htmlFor={`${uid}-sr`}>Sample rate</label>
                    <select id={`${uid}-sr`} className="select" value={o.sample_rate ?? ""} aria-invalid={!!err.sample_rate} aria-describedby={err.sample_rate ? `${uid}-sr-err` : undefined}
                      onChange={(e) => onChange({ sample_rate: e.target.value ? Number(e.target.value) : null })}>
                      <option value="">Original{media?.audio?.sample_rate ? ` (${media.audio.sample_rate / 1000} kHz)` : ""}</option>
                      <option value={44100}>44.1 kHz</option>
                      <option value={48000}>48 kHz</option>
                      {o.output !== "mp3" && <option value={96000}>96 kHz</option>}
                      <option value={32000}>32 kHz</option>
                      <option value={22050}>22.05 kHz</option>
                    </select>
                    <FieldError id={`${uid}-sr-err`} msg={err.sample_rate} />
                  </div>
                  <div className="field">
                    <label htmlFor={`${uid}-ch`}>Channels</label>
                    <select id={`${uid}-ch`} className="select" value={o.channels} onChange={(e) => onChange({ channels: e.target.value as Options["channels"] })}>
                      <option value="original">Original{media?.audio?.channels ? ` (${media.audio.channels === 1 ? "mono" : media.audio.channels === 2 ? "stereo" : `${media.audio.channels} ch`})` : ""}</option>
                      <option value="stereo">Stereo</option>
                      <option value="mono">Mono</option>
                    </select>
                  </div>
                </div>
                <label className="check">
                  <input type="checkbox" checked={o.normalize} disabled={!caps.enhancement.loudnorm} onChange={(e) => onChange({ normalize: e.target.checked })} />
                  <span>Loudness normalization<span className="field-hint">Evens out overall loudness to a streaming-friendly level (EBU R128, −16 LUFS).</span></span>
                </label>
              </div>
            </details>
          )}

          {isVideoOut && (
            <details className="section" open={o.fps_mode !== "original" || undefined}>
              <summary><Gauge size={17} aria-hidden /> Motion enhancement <span className="sum-hint">{o.fps_mode === "original" ? "Original frame rate" : o.fps_mode === "standard" ? `${o.target_fps ?? "?"} fps (standard)` : `${o.target_fps} fps interpolated`}</span><ChevronDown size={18} className="chev" aria-hidden /></summary>
              <div className="section-body">
                <div className="option-cards cards-2" role="radiogroup" aria-label="Frame rate mode" aria-describedby={err.target_fps ? `${uid}-fps-err` : undefined}>
                  <Radio value="original" current={o.fps_mode === "interpolate" ? `i${o.target_fps}` : o.fps_mode} onSelect={() => onChange({ fps_mode: "original", target_fps: null, preview: false })} className="option-card">
                    <span className="t">Preserve original</span><span className="d">Keep the source frame rate{srcFps ? ` (${srcFps} fps)` : ""}.</span>
                  </Radio>
                  <Radio value="standard" current={o.fps_mode === "interpolate" ? `i${o.target_fps}` : o.fps_mode} onSelect={() => onChange({ fps_mode: "standard", target_fps: o.target_fps && o.fps_mode === "standard" ? o.target_fps : 30 })} className="option-card">
                    <span className="t">Standard conversion</span><span className="d">Drops or duplicates frames to hit a target. Doesn&apos;t create smoother motion.</span>
                  </Radio>
                  {[60, 120].map((t) => {
                    const engineOk = interp.rife.available || interp.minterpolate.available;
                    const disabled = !engineOk || (t === 120 && !interp.fps120_enabled) || (srcFps != null && srcFps >= t - 0.5);
                    const why = !engineOk ? "No interpolation engine is installed." : t === 120 && !interp.fps120_enabled ? "120 FPS is disabled on this server." : srcFps != null && srcFps >= t - 0.5 ? `Source is already ${srcFps} fps.` : "";
                    return (
                      <Radio key={t} value={`i${t}`} current={o.fps_mode === "interpolate" ? `i${o.target_fps}` : o.fps_mode} disabled={disabled} title={why || undefined}
                        onSelect={() => onChange({ fps_mode: "interpolate", target_fps: t })} className="option-card">
                        <span className="t"><Zap size={15} aria-hidden /> Motion-interpolated {t} FPS</span>
                        <span className="d">{why || "Generates intermediate frames for smoother apparent motion."}</span>
                      </Radio>
                    );
                  })}
                </div>
                <FieldError id={`${uid}-fps-err`} msg={err.target_fps} />
                {o.fps_mode === "standard" && (
                  <div className="field" style={{ maxWidth: 260 }}>
                    <label htmlFor={`${uid}-tfps`}>Target frame rate</label>
                    <select id={`${uid}-tfps`} className="select" value={o.target_fps ?? 30} onChange={(e) => onChange({ target_fps: Number(e.target.value) })}>
                      {[24, 25, 30, 48, 50, 60, 120].map((f) => <option key={f} value={f}>{f} fps</option>)}
                    </select>
                  </div>
                )}
                {o.fps_mode === "interpolate" && (
                  <>
                    <div className="notice notice-warning"><TriangleExclamationPoint size={16} aria-hidden /><p>Motion interpolation generates intermediate frames. It may introduce artifacts around fast motion, scene cuts, text, or complex movement.</p></div>
                    <div className="grid-2">
                      <div className="field">
                        <label htmlFor={`${uid}-eng`}>Interpolation method</label>
                        <select id={`${uid}-eng`} className="select" value={o.interp_engine} aria-invalid={!!err.interp_engine} aria-describedby={`${uid}-eng-hint`}
                          onChange={(e) => onChange({ interp_engine: e.target.value as Options["interp_engine"] })}>
                          <option value="auto">Best available ({interp.rife.available ? "GPU AI" : "CPU"})</option>
                          <option value="rife" disabled={!interp.rife.available}>{interp.rife.label}{!interp.rife.available ? " — unavailable" : ""}</option>
                          <option value="minterpolate" disabled={!interp.minterpolate.available}>{interp.minterpolate.label}</option>
                        </select>
                        <span id={`${uid}-eng-hint`} className="field-hint">
                          {!interp.rife.available ? <>GPU AI interpolation: {interp.rife.reason}</> : interp.rife.speed}
                        </span>
                        <FieldError id={`${uid}-eng-err`} msg={err.interp_engine} />
                      </div>
                      <label className="check" style={{ paddingTop: 24 }}>
                        <input type="checkbox" checked={o.preview} onChange={(e) => onChange({ preview: e.target.checked })} />
                        <span>Preview first ({caps.limits.preview_seconds}s clip)<span className="field-hint">Check the result before processing the whole video.</span></span>
                      </label>
                    </div>
                    <BeforeProcessing plan={plan} options={o} srcFps={srcFps} caps={caps} duration={duration} />
                  </>
                )}
              </div>
            </details>
          )}

          {isVideoOut && (
            <details className="section" open={o.upscale !== "none" || undefined}>
              <summary><Maximize2 size={17} aria-hidden /> Resolution enhancement <span className="sum-hint">{o.upscale === "none" ? "No upscaling" : o.upscale === "standard" ? "Standard resize" : "AI upscaling"}</span><ChevronDown size={18} className="chev" aria-hidden /></summary>
              <div className="section-body">
                <div className="option-cards" role="radiogroup" aria-label="Resolution enhancement" aria-describedby={err.upscale ? `${uid}-up-err` : undefined}>
                  <Radio value="none" current={o.upscale} onSelect={() => onChange({ upscale: "none", resolution: srcShort && o.resolution !== "original" && Number(o.resolution) > srcShort ? "original" : o.resolution })} className="option-card">
                    <span className="t">No upscaling</span><span className="d">Output at or below the source resolution.</span>
                  </Radio>
                  <Radio value="standard" current={o.upscale} onSelect={() => onChange({ upscale: "standard" })} className="option-card">
                    <span className="t">Standard resize</span><span className="d">High-quality Lanczos scaling. Bigger, not more detailed.</span>
                  </Radio>
                  <Radio value="ai" current={o.upscale} onSelect={() => onChange({ upscale: "ai" })} className="option-card" disabled={!aiUp.available} title={aiUp.available ? undefined : aiUp.reason}>
                    <span className="t"><Cpu size={15} aria-hidden /> AI upscaling</span><span className="d">{aiUp.available ? `Real-ESRGAN on GPU. Sources up to ${aiUp.max_input_height}p, ${aiUp.max_seconds}s.` : `Unavailable: ${aiUp.reason}`}</span>
                  </Radio>
                </div>
                <FieldError id={`${uid}-up-err`} msg={err.upscale} />
                {o.upscale !== "none" && (
                  <>
                    {resolutionSelect}
                    <p className="muted" style={{ margin: 0 }}>Upscaling may improve appearance but can&apos;t guarantee recovery of original detail. Upscaled output is labeled as upscaled, never as native HD or 4K.</p>
                  </>
                )}
              </div>
            </details>
          )}

          <details className="section" open={o.edit.trim_start != null || o.edit.trim_end != null || undefined}>
            <summary><Scissors size={17} aria-hidden /> Edit <span className="sum-hint">{o.edit.trim_start != null || o.edit.trim_end != null ? "Trimmed" : "Trim, crop, rotate, volume"}</span><ChevronDown size={18} className="chev" aria-hidden /></summary>
            <div className="section-body">
              {duration ? (
                <TrimEditor duration={duration} start={o.edit.trim_start} end={o.edit.trim_end} onChange={(s, e) => onEdit({ trim_start: s, trim_end: e })}
                  previewUrl={p.previewUrl} hasVideo={hasVideo} errorStart={err.trim_start} errorEnd={err.trim_end} />
              ) : (
                <p className="muted">Trimming becomes available once the media duration is known.</p>
              )}
              {(isVideoOut || isGif) && hasVideo && <VideoEdits o={o} onEdit={onEdit} onChange={onChange} media={media} err={err} uid={uid} />}
              {!isGif && (o.include_audio || isAudioOut) && hasAudio && (
                <div className="grid-3">
                  <div className="field">
                    <label htmlFor={`${uid}-vol`}>Volume: {o.edit.volume}%</label>
                    <input id={`${uid}-vol`} type="range" min={0} max={300} step={5} value={o.edit.volume} onChange={(e) => onEdit({ volume: Number(e.target.value) })} />
                  </div>
                  <div className="field">
                    <label htmlFor={`${uid}-fi`}>Fade in (s)</label>
                    <input id={`${uid}-fi`} className="input" type="number" min={0} max={30} step={0.5} value={o.edit.fade_in} onChange={(e) => onEdit({ fade_in: Math.max(0, Number(e.target.value) || 0) })} />
                  </div>
                  <div className="field">
                    <label htmlFor={`${uid}-fo`}>Fade out (s)</label>
                    <input id={`${uid}-fo`} className="input" type="number" min={0} max={30} step={0.5} value={o.edit.fade_out} onChange={(e) => onEdit({ fade_out: Math.max(0, Number(e.target.value) || 0) })} />
                  </div>
                </div>
              )}
            </div>
          </details>

          <details className="section">
            <summary><Info size={17} aria-hidden /> Output <span className="sum-hint">{plan?.filename ?? "Filename & metadata"}</span><ChevronDown size={18} className="chev" aria-hidden /></summary>
            <div className="section-body">
              <div className="field">
                <label htmlFor={`${uid}-fn`}>File name</label>
                <input id={`${uid}-fn`} className="input" maxLength={150} placeholder={plan?.filename?.replace(/\.[^.]+$/, "") ?? "Same as source"} value={o.filename ?? ""}
                  onChange={(e) => onChange({ filename: e.target.value || null })} aria-describedby={`${uid}-fn-hint`} />
                <span id={`${uid}-fn-hint`} className="field-hint">The .{fmt.ext} extension is added automatically. Unsafe characters are replaced.</span>
              </div>
              <label className="check">
                <input type="checkbox" checked={o.edit.strip_metadata} onChange={(e) => onEdit({ strip_metadata: e.target.checked })} />
                <span>Remove metadata<span className="field-hint">Strips tags such as title, creation date, device and location from the output.</span></span>
              </label>
            </div>
          </details>
        </div>
      )}
    </div>
  );
}

function VideoEdits({ o, onEdit, onChange, media, err, uid }: { o: Options; onEdit: Props["onEdit"]; onChange: Props["onChange"]; media: MediaInfo | null | undefined; err: Record<string, string>; uid: string }) {
  const w = media?.video?.width ?? 0;
  const h = media?.video?.height ?? 0;
  const crop = o.edit.crop;
  const presets: [string, (() => Options["edit"]["crop"]) | null][] = [
    ["None", null],
    ["Center square", () => { const s = Math.min(w, h); return { x: Math.floor((w - s) / 2), y: Math.floor((h - s) / 2), w: s, h: s }; }],
    ["Center 9:16", () => { const cw = Math.min(w, Math.floor((h * 9) / 16)); const ch = Math.min(h, Math.floor((cw * 16) / 9)); return { x: Math.floor((w - cw) / 2), y: Math.floor((h - ch) / 2), w: cw, h: ch }; }],
    ["Center 16:9", () => { const ch = Math.min(h, Math.floor((w * 9) / 16)); const cw = Math.min(w, Math.floor((ch * 16) / 9)); return { x: Math.floor((w - cw) / 2), y: Math.floor((h - ch) / 2), w: cw, h: ch }; }],
  ];
  return (
    <>
      <div className="field">
        <span className="row" style={{ gap: 6 }}><RotateCw size={15} aria-hidden /><label id={`${uid}-rot`}>Rotate</label></span>
        <div className="segmented" role="radiogroup" aria-labelledby={`${uid}-rot`}>
          {([0, 90, 180, 270] as const).map((r) => <button key={r} type="button" role="radio" aria-checked={o.edit.rotate === r} onClick={() => onEdit({ rotate: r })}>{r === 0 ? "None" : `${r}°`}</button>)}
        </div>
      </div>
      {w > 0 && (
        <div className="field">
          <label id={`${uid}-crop`}>Crop <span className="field-hint">(source is {w}×{h})</span></label>
          <div className="chips" role="group" aria-labelledby={`${uid}-crop`}>
            {presets.map(([label, fn]) => (
              <button key={label} type="button" className="chip" aria-pressed={label === "None" ? !crop : false} onClick={() => onEdit({ crop: fn ? fn() : null })}>{label}</button>
            ))}
          </div>
          {crop && (
            <div className="grid-4">
              {(["x", "y", "w", "h"] as const).map((k) => (
                <div className="field" key={k}>
                  <label htmlFor={`${uid}-c${k}`}>{{ x: "Left (x)", y: "Top (y)", w: "Width", h: "Height" }[k]}</label>
                  <input id={`${uid}-c${k}`} className="input" type="number" min={k === "w" || k === "h" ? 16 : 0} value={crop[k]} aria-invalid={!!err.crop}
                    onChange={(e) => onEdit({ crop: { ...crop, [k]: Math.max(0, Math.round(Number(e.target.value) || 0)) } })} />
                </div>
              ))}
            </div>
          )}
          {err.crop && <span className="field-error" role="alert">{err.crop}</span>}
        </div>
      )}
      <div className="grid-2">
        <div className="field">
          <label htmlFor={`${uid}-ar`}>Aspect ratio</label>
          <select id={`${uid}-ar`} className="select" value={o.edit.aspect} onChange={(e) => onEdit({ aspect: e.target.value as Options["edit"]["aspect"] })}>
            <option value="original">Original</option>
            <option value="16:9">16:9 landscape</option>
            <option value="9:16">9:16 vertical</option>
            <option value="1:1">1:1 square</option>
          </select>
        </div>
        {o.edit.aspect !== "original" && (
          <div className="field">
            <label htmlFor={`${uid}-am`}>Fit</label>
            <select id={`${uid}-am`} className="select" value={o.edit.aspect_mode} aria-invalid={!!err.aspect_mode}
              onChange={(e) => { const m = e.target.value as Options["edit"]["aspect_mode"]; onEdit({ aspect_mode: m }); if (m === "stretch") onChange({ keep_aspect: false }); }}>
              <option value="fit">Fit (add bars)</option>
              <option value="fill">Fill (crop edges)</option>
              <option value="stretch">Stretch (distorts)</option>
            </select>
            {err.aspect_mode && <span className="field-error" role="alert">{err.aspect_mode}</span>}
          </div>
        )}
      </div>
    </>
  );
}

function BeforeProcessing({ plan, options, srcFps, caps, duration }: { plan?: PlanResult | null; options: Options; srcFps: number | null; caps: Capabilities; duration: number | null }) {
  const it = caps.enhancement.interpolation;
  const engine = plan?.interp?.engine ?? (options.interp_engine === "auto" ? (it.rife.available ? "rife" : "minterpolate") : options.interp_engine);
  const ec = engine === "rife" ? it.rife : it.minterpolate;
  const maxSec = ec.max_seconds[String(options.target_fps)] ?? 0;
  const est = plan?.estimate;
  return (
    <div className="panel" style={{ padding: 14, boxShadow: "none", background: "var(--panel-2)" }}>
      <h4 style={{ fontSize: 13.5, marginBottom: 8 }}>Before processing</h4>
      <dl className="kv">
        <dt>Original frame rate</dt><dd>{srcFps ? `${srcFps} fps` : "Detected after fetching"}</dd>
        <dt>Target frame rate</dt><dd>{options.target_fps} fps</dd>
        <dt>Method</dt><dd>{plan?.interp?.label ?? ec.label}</dd>
        <dt>Scene-cut handling</dt><dd>{ec.scene_detection ? "Yes — no blending across cuts" : "No"}</dd>
        <dt>Processing time</dt><dd>{est?.seconds_low != null && est.seconds_high != null ? `~${formatSeconds(est.seconds_low)} – ${formatSeconds(est.seconds_high)}` : "Not enough data to estimate"}</dd>
        <dt>Estimated size</dt><dd>{est?.size_low != null ? `${formatBytes(est.size_low)} – ${formatBytes(est.size_high)} (estimate)` : "—"}</dd>
        <dt>Limits</dt><dd>Up to {ec.max_height}p, {maxSec >= 120 ? `${Math.round(maxSec / 60)} min` : `${maxSec}s`}{duration && duration > maxSec ? " — trim or preview" : ""}</dd>
      </dl>
      <p className="muted" style={{ margin: "8px 0 0" }}>{ec.speed}</p>
    </div>
  );
}
