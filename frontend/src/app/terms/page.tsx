import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = { title: "Terms of use" };

export default function TermsPage() {
  return (
    <div className="container container-narrow page prose">
      <h1>Terms of use</h1>
      <p className="lead">By using OmniConvert you agree to the following. The operator of this deployment may add terms that apply where they operate.</p>

      <h2>Your responsibility</h2>
      <ul>
        <li>Only upload or submit media that you own or are authorized to download and convert.</li>
        <li>Respect copyright, the terms of the platforms you link to, and the rights of people who appear in media.</li>
        <li>Don&apos;t use the service to circumvent DRM, paywalls, private accounts, login requirements, or other access controls. The service is designed not to do this, and attempts may be blocked.</li>
        <li>Don&apos;t submit unlawful content or use the service to harm others.</li>
      </ul>

      <h2>Service limits</h2>
      <ul>
        <li>Platform support depends on the platform&apos;s accessibility rules, which can change without notice. We don&apos;t promise that any given link will work.</li>
        <li>File size, duration, batch and rate limits apply. Enhancement features have additional limits and depend on the server&apos;s hardware.</li>
        <li>Files are stored temporarily and deleted automatically. Keep your own copies of anything important.</li>
        <li>The service is provided as is, without warranties about availability or fitness for a particular purpose, to the extent permitted by applicable law.</li>
      </ul>

      <h2>Abuse and copyright</h2>
      <p>To report abuse or a copyright concern, use the <Link href="/report">report page</Link>. Access may be restricted for misuse.</p>
    </div>
  );
}
