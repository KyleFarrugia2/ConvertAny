import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = { title: "How it works" };

export default function HowItWorks() {
  return (
    <div className="container container-narrow page prose">
      <h1>How it works</h1>
      <p className="lead">OmniConvert runs real media processing on our servers using FFmpeg. Every progress bar reflects measured progress, and a file is only offered for download after it passes verification.</p>

      <h2>The conversion flow</h2>
      <ol className="steps">
        <li><div><strong>Add media.</strong> Paste a link from a supported source, or upload files. Uploads are sent in chunks, so a dropped connection can resume where it stopped.</div></li>
        <li><div><strong>We inspect it.</strong> Uploaded files are identified by their contents (not their extension) and checked for duration, resolution, codecs and frame rate. Links are analyzed to list the available qualities and streams.</div></li>
        <li><div><strong>Choose settings.</strong> Pick a format and a quality preset, or open Advanced for codecs, bitrates, frame rate, editing and enhancement. Settings are validated as you go, and only valid combinations are offered.</div></li>
        <li><div><strong>We queue the job.</strong> Conversions run on background workers, not in your browser tab, so you can keep working or add more files.</div></li>
        <li><div><strong>Processing.</strong> The stages you see (fetching, converting, interpolating, finalizing) match what&apos;s actually happening. If a step&apos;s progress can&apos;t be measured, we show an indeterminate indicator rather than an invented percentage.</div></li>
        <li><div><strong>Verification.</strong> The output is probed and decoded to confirm it has the expected streams, size, frame rate and duration before it&apos;s marked ready.</div></li>
        <li><div><strong>Download, then deletion.</strong> Files expire automatically after a few hours. You can also delete them immediately.</div></li>
      </ol>

      <h2>No re-encoding when possible</h2>
      <p>If you choose <em>Original / best available</em> and the target format can hold the source streams as they are (for example MOV with H.264/AAC to MP4), we copy the streams into the new container instead of re-encoding them. That&apos;s fast and preserves the original quality exactly. When re-encoding is required, &ldquo;original&rdquo; means very high quality settings, which is not the same as lossless.</p>

      <h2>Frame rate: conversion vs. interpolation</h2>
      <p><strong>Standard conversion</strong> reaches a target frame rate by dropping or duplicating frames. A 24 FPS video converted to 60 FPS this way plays at 60 FPS but doesn&apos;t look any smoother.</p>
      <p><strong>Motion interpolation</strong> generates new in-between frames by estimating motion. On CPU we use FFmpeg&apos;s motion-compensated interpolation; on a server with a supported GPU, RIFE AI interpolation is used. Both detect scene cuts so frames aren&apos;t blended across shots. Interpolation can create artifacts around fast motion, text, and complex movement, so for long videos we suggest processing a short preview first.</p>
      <p>We test this claim directly: our automated tests hash every output frame and confirm that interpolated output contains genuinely new frames, while standard conversion produces duplicates.</p>

      <h2>Upscaling</h2>
      <p>Upscaling enlarges video beyond its source resolution. Standard resizing uses high-quality Lanczos scaling. AI upscaling (Real-ESRGAN) is offered only when the server has a working GPU model installed. Neither can recover detail that wasn&apos;t captured. Upscaled outputs are always labeled as upscaled.</p>

      <h2>Audio quality</h2>
      <p>Raising the bitrate or converting to a lossless format (WAV, FLAC) preserves what&apos;s in the source, but it can&apos;t restore detail that an earlier lossy encoding already removed.</p>

      <p style={{ marginTop: 28 }}>See <Link href="/supported">Supported sources &amp; formats</Link> for exactly what this server can do.</p>
    </div>
  );
}
