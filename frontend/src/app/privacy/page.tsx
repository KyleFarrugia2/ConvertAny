"use client";

import { useCapabilities } from "@/components/Providers";

export default function PrivacyPage() {
  const { caps } = useCapabilities();
  const ttl = caps ? `${Math.round(caps.limits.file_ttl_seconds / 3600)} hours` : "a few hours (configured by the operator)";
  return (
    <div className="container container-narrow page prose">
      <h1>Privacy</h1>
      <p className="lead">This page describes how this OmniConvert deployment handles data, based on how the software works. The operator of this deployment is responsible for any additional policies required where they operate.</p>

      <h2>What we process</h2>
      <ul>
        <li><strong>Files you upload</strong> and <strong>media fetched from links you submit</strong>, solely to perform the conversion you request.</li>
        <li><strong>Technical metadata</strong> about that media (duration, resolution, codecs) and the settings you choose.</li>
        <li><strong>An anonymous session cookie</strong> that ties your conversions to your browser. It contains a random token; only a hash of it is stored on the server.</li>
      </ul>

      <h2>Retention</h2>
      <ul>
        <li>Uploaded files and converted outputs are deleted automatically after <strong>{ttl}</strong>.</li>
        <li>&ldquo;Delete now&rdquo; immediately removes a conversion&apos;s output, its source upload (if no other conversion is using it), and the job record.</li>
        <li>Records of expired conversions are removed about a day after expiry. Anonymous sessions with no conversions are removed after 30 days of inactivity.</li>
        <li>Partial files from failed or cancelled conversions are removed right away.</li>
      </ul>

      <h2>What we don&apos;t do</h2>
      <ul>
        <li>We don&apos;t ask for, and never use, passwords, session cookies, or account tokens for any platform.</li>
        <li>We don&apos;t log the signed media URLs that platforms hand out. Logs record only a redacted form of links.</li>
        <li>We don&apos;t share your files with other users. Download links are signed, expire quickly, and only work in your browser session.</li>
        <li>The site doesn&apos;t run advertising or third-party tracking scripts.</li>
      </ul>

      <h2>Metadata in your files</h2>
      <p>Output files keep the source&apos;s metadata unless you turn on <em>Remove metadata</em> under Advanced → Output, which strips tags such as creation date, device, and location.</p>

      <h2>Contact</h2>
      <p>{caps?.abuse_contact_email ? <>Questions about privacy: <a href={`mailto:${caps.abuse_contact_email}`}>{caps.abuse_contact_email}</a>.</> : <>The operator of this deployment hasn&apos;t configured a contact address yet (<code>OC_ABUSE_CONTACT_EMAIL</code>). Use the <a href="/report">report form</a> in the meantime.</>}</p>
    </div>
  );
}
