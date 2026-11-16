"use client";

import { useEffect, useId, useRef, useState } from "react";
import { formatClock, parseClock } from "@/lib/format";

interface Props {
  duration: number;
  start: number | null;
  end: number | null;
  onChange: (start: number | null, end: number | null) => void;
  previewUrl?: string;
  hasVideo: boolean;
  errorStart?: string;
  errorEnd?: string;
}

function ClockInput({ label, value, onCommit, error, id }: { label: string; value: number; onCommit: (v: number) => void; error?: string; id: string }) {
  const [text, setText] = useState(formatClock(value));
  const [invalid, setInvalid] = useState(false);
  useEffect(() => setText(formatClock(value)), [value]);
  const commit = () => {
    const v = parseClock(text);
    if (v == null || isNaN(v)) {
      setInvalid(true);
      return;
    }
    setInvalid(false);
    onCommit(v);
  };
  const errId = `${id}-err`;
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input id={id} className="input" inputMode="decimal" value={text} onChange={(e) => setText(e.target.value)} onBlur={commit}
        onKeyDown={(e) => e.key === "Enter" && commit()} aria-invalid={invalid || !!error} aria-describedby={invalid || error ? errId : undefined} />
      {(invalid || error) && <span id={errId} className="field-error">{invalid ? "Use mm:ss.s or seconds." : error}</span>}
    </div>
  );
}

export function TrimEditor({ duration, start, end, onChange, previewUrl, hasVideo, errorStart, errorEnd }: Props) {
  const id = useId();
  const s = start ?? 0;
  const e = end ?? duration;
  const videoRef = useRef<HTMLVideoElement>(null);
  const step = duration > 600 ? 1 : 0.1;
  const set = (ns: number, ne: number) => {
    ns = Math.max(0, Math.min(ns, duration));
    ne = Math.max(0, Math.min(ne, duration));
    onChange(ns <= 0.05 ? null : Math.round(ns * 10) / 10, ne >= duration - 0.05 ? null : Math.round(ne * 10) / 10);
  };
  const pct = (v: number) => (duration ? (v / duration) * 100 : 0);

  return (
    <div className="stack">
      {previewUrl && (
        hasVideo ? (
          <video ref={videoRef} className="trim-video" src={previewUrl} controls preload="metadata" aria-label="Preview of your file for choosing trim points" />
        ) : (
          <audio ref={videoRef as unknown as React.RefObject<HTMLAudioElement>} src={previewUrl} controls preload="metadata" style={{ width: "100%" }} aria-label="Preview of your audio for choosing trim points" />
        )
      )}
      <div className="trim-track" aria-hidden={false}>
        <div className="rail" />
        <div className="sel" style={{ left: `${pct(s)}%`, width: `${pct(e) - pct(s)}%` }} />
        <input type="range" min={0} max={duration} step={step} value={s} aria-label="Trim start" aria-valuetext={formatClock(s)}
          onChange={(ev) => set(Math.min(Number(ev.target.value), e - step), e)} />
        <input type="range" min={0} max={duration} step={step} value={e} aria-label="Trim end" aria-valuetext={formatClock(e)}
          onChange={(ev) => set(s, Math.max(Number(ev.target.value), s + step))} />
      </div>
      <div className="grid-3">
        <ClockInput id={`${id}-s`} label="Start" value={s} onCommit={(v) => set(v, e)} error={errorStart} />
        <ClockInput id={`${id}-e`} label="End" value={e} onCommit={(v) => set(s, v)} error={errorEnd} />
        <div className="field">
          <span className="field-hint" style={{ marginTop: 22 }}>Selected: <strong>{formatClock(Math.max(0, e - s))}</strong> of {formatClock(duration)}</span>
        </div>
      </div>
      <div className="row">
        {previewUrl && (
          <>
            <button type="button" className="btn btn-sm" onClick={() => videoRef.current && set(videoRef.current.currentTime, e)}>Set start at playhead</button>
            <button type="button" className="btn btn-sm" onClick={() => videoRef.current && set(s, videoRef.current.currentTime)}>Set end at playhead</button>
          </>
        )}
        {(start != null || end != null) && <button type="button" className="btn btn-sm btn-ghost" onClick={() => onChange(null, null)}>Reset trim</button>}
      </div>
    </div>
  );
}
