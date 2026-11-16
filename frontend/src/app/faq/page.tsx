import type { Metadata } from "next";
import Link from "next/link";
import { ChevronDown } from "lucide-react";

export const metadata: Metadata = { title: "FAQ" };

const FAQ: [string, React.ReactNode][] = [
  ["Which links work?", <>Public media from the sources listed on <Link href="/supported">Supported sources</Link>, plus direct links to media files. Many platforms restrict logged-out or automated access, so some links won&apos;t work even though the media plays in your browser. In that case, download the file from the platform (if you&apos;re allowed to) and upload it here.</>],
  ["Why can't you process private, members-only, or region-locked content?", "We don't bypass access controls of any kind — no logins, cookies, account tokens, DRM circumvention, or region workarounds. We will never ask for your social media password or session cookies."],
  ["What should I choose if I'm not sure?", "MP4 for video and MP3 for audio with the High quality preset. These play almost everywhere."],
  ["Does “Original / best available” mean lossless?", "No. If the streams can be copied into the new format unchanged, we copy them and quality is identical. If re-encoding is needed, we use very high quality settings, but that is still a lossy encode."],
  ["Will converting an MP3 to WAV or FLAC improve the sound?", "No. Lossless formats keep exactly what's in the source, but detail removed by an earlier lossy encoding can't be recovered. The same applies to raising the bitrate."],
  ["What's the difference between 60 FPS conversion and 60 FPS interpolation?", "Standard conversion duplicates frames, so the video plays at 60 FPS but doesn't look smoother. Interpolation creates new in-between frames for smoother motion, but it can introduce artifacts and takes much longer to process."],
  ["Is upscaled video real 4K?", "No. Upscaling enlarges the picture and can make it look cleaner, but it can't add detail the source didn't capture. We label upscaled outputs as upscaled."],
  ["How long are my files kept?", "Uploaded sources and converted files are deleted automatically after a few hours (see the Privacy page for the exact period on this server). You can delete them immediately with “Delete now”."],
  ["Can someone else download my files?", "No. Conversions are tied to your browser session, and download links are signed, short-lived, and only work in the session that created them. Knowing a job ID isn't enough."],
  ["Do I need an account?", "No. Conversions work anonymously within the server's limits."],
  ["Why is there no time estimate?", "We only estimate processing time when this server has measured enough similar conversions. Otherwise we show the current stage and real progress instead of guessing."],
  ["A conversion failed — what now?", "The error message explains what happened and what to try. Temporary problems are retried automatically a limited number of times, and you can retry manually. Settings that can never work (for example, an audio format for a file with no audio) are rejected before processing starts."],
];

export default function FaqPage() {
  return (
    <div className="container container-narrow page">
      <h1>Frequently asked questions</h1>
      <div style={{ marginTop: 18 }}>
        {FAQ.map(([q, a]) => (
          <details key={q} className="faq">
            <summary>{q}<ChevronDown size={18} className="chev" aria-hidden /></summary>
            <div className="a">{a}</div>
          </details>
        ))}
      </div>
    </div>
  );
}
