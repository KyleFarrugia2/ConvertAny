"use client";

import { LoaderCircle } from "lucide-react";
import { useCapabilities } from "@/components/Providers";
import { formatBytes } from "@/lib/format";

const STATUS: Record<string, [string, string]> = {
  supported: ["Supported", "badge-success"],
  limited: ["Limited support", "badge-warning"],
  temporarily_unavailable: ["Temporarily unavailable", "badge-danger"],
  unsupported: ["Unsupported", ""],
};

function Avail({ ok, reason }: { ok: boolean; reason?: string }) {
  return ok ? <span className="badge badge-success">Available</span> : <span className="badge" title={reason}>Not available</span>;
}

export default function SupportedPage() {
  const { caps, error } = useCapabilities();
  return (
    <div className="container page">
      <h1>Supported sources &amp; formats</h1>
      <p className="lead">This page is generated live from the server&apos;s capabilities, so it always reflects what this deployment can actually do.</p>
      {error && <div className="notice notice-danger">{error}</div>}
      {!caps && !error && <div className="row muted"><LoaderCircle size={16} className="spin" aria-hidden /> Loading…</div>}
      {caps && (
        <>
          <h2>Link sources</h2>
          <p className="subtle">Support depends on each platform&apos;s accessibility rules, which change often. We only fetch media that&apos;s publicly available without logging in, and we never bypass DRM, paywalls, private accounts, regional locks or other access controls. When a link can&apos;t be processed, download the file from the platform yourself (if you&apos;re allowed to) and upload it.</p>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th scope="col">Source</th><th scope="col">Status</th><th scope="col">Notes</th></tr></thead>
              <tbody>
                {caps.sources.map((s) => (
                  <tr key={s.id}>
                    <td><strong>{s.name}</strong>{s.domains.length > 0 && <div className="muted">{s.domains.join(", ")}</div>}</td>
                    <td><span className={`badge ${STATUS[s.status]?.[1] ?? ""}`}>{STATUS[s.status]?.[0] ?? s.status}</span></td>
                    <td className="subtle">{s.notes}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted" style={{ marginTop: 8 }}><strong>Limited support</strong> means public content often works, but the platform frequently blocks logged-out or server requests. A source switches to <strong>temporarily unavailable</strong> automatically after repeated upstream failures.</p>

          <h2>Upload formats</h2>
          <p className="subtle">Accepted inputs: {caps.accepted_inputs.map((x) => x.toUpperCase()).join(", ")}. Files are identified by their contents, not their extension. Limit: {formatBytes(caps.limits.max_upload_bytes)} and {Math.round(caps.limits.max_duration_seconds / 60)} minutes per file.</p>

          <h2>Output formats</h2>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th scope="col">Format</th><th scope="col">Type</th><th scope="col">Video codecs</th><th scope="col">Audio codecs</th><th scope="col">Status</th></tr></thead>
              <tbody>
                {Object.values(caps.formats).map((f) => (
                  <tr key={f.id}>
                    <td><strong>{f.label}</strong> {f.recommended && <span className="badge badge-accent">Recommended</span>}</td>
                    <td className="subtle">{f.category === "gif" ? "Additional" : f.category === "video" ? "Video" : "Audio"}</td>
                    <td className="subtle">{f.video_codecs.map((c) => `${c.label}${c.available ? "" : " (unavailable)"}`).join(", ") || "—"}</td>
                    <td className="subtle">{f.audio_codecs.map((c) => `${c.label}${c.available ? "" : " (unavailable)"}`).join(", ") || "—"}</td>
                    <td><Avail ok={f.available} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h2>Enhancements</h2>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th scope="col">Feature</th><th scope="col">Status</th><th scope="col">Details</th></tr></thead>
              <tbody>
                <tr><td><strong>Standard frame-rate conversion</strong></td><td><Avail ok /></td><td className="subtle">Drops or duplicates frames. Doesn&apos;t create smoother motion.</td></tr>
                <tr><td><strong>{caps.enhancement.interpolation.minterpolate.label}</strong></td><td><Avail ok={caps.enhancement.interpolation.minterpolate.available} /></td>
                  <td className="subtle">60 FPS up to {caps.enhancement.interpolation.minterpolate.max_seconds["60"] / 60} min, 120 FPS up to {caps.enhancement.interpolation.minterpolate.max_seconds["120"]} s, at most {caps.enhancement.interpolation.minterpolate.max_height}p. Scene-change detection avoids blending across cuts. {caps.enhancement.interpolation.minterpolate.speed}</td></tr>
                <tr><td><strong>{caps.enhancement.interpolation.rife.label}</strong></td><td><Avail ok={caps.enhancement.interpolation.rife.available} reason={caps.enhancement.interpolation.rife.reason} /></td>
                  <td className="subtle">{caps.enhancement.interpolation.rife.available ? `Verified by a self-test at startup. Up to ${caps.enhancement.interpolation.rife.max_height}p.` : caps.enhancement.interpolation.rife.reason} Requires a Vulkan-capable GPU and the rife-ncnn-vulkan binary.</td></tr>
                <tr><td><strong>120 FPS output</strong></td><td><Avail ok={caps.enhancement.interpolation.fps120_enabled} /></td><td className="subtle">Subject to stricter duration and resolution limits.</td></tr>
                <tr><td><strong>{caps.enhancement.upscale.standard.label}</strong></td><td><Avail ok={caps.enhancement.upscale.standard.available} /></td><td className="subtle">Labeled as upscaled — never as native HD or 4K.</td></tr>
                <tr><td><strong>{caps.enhancement.upscale.ai.label}</strong></td><td><Avail ok={caps.enhancement.upscale.ai.available} reason={caps.enhancement.upscale.ai.reason} /></td>
                  <td className="subtle">{caps.enhancement.upscale.ai.available ? `Sources up to ${caps.enhancement.upscale.ai.max_input_height}p and ${caps.enhancement.upscale.ai.max_seconds}s.` : caps.enhancement.upscale.ai.reason} Requires a Vulkan-capable GPU.</td></tr>
                <tr><td><strong>Loudness normalization</strong></td><td><Avail ok={caps.enhancement.loudnorm} /></td><td className="subtle">EBU R128 to −16 LUFS.</td></tr>
              </tbody>
            </table>
          </div>
          <p className="muted" style={{ marginTop: 12 }}>Processing engine: {caps.ffmpeg || "unknown"}{caps.enhancement.gpu ? ` · GPU detected: ${caps.enhancement.gpu}` : ""}</p>
        </>
      )}
    </div>
  );
}
