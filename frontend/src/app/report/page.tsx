"use client";

import { useState } from "react";
import { CircleCheck, LoaderCircle, TriangleExclamationPoint } from "lucide-react";
import { useCapabilities } from "@/components/Providers";
import { api, toApiError } from "@/lib/api";
import type { ApiErrorBody } from "@/lib/types";

export default function ReportPage() {
  const { caps } = useCapabilities();
  const [kind, setKind] = useState("copyright");
  const [url, setUrl] = useState("");
  const [details, setDetails] = useState("");
  const [contact, setContact] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "sent">("idle");
  const [err, setErr] = useState<ApiErrorBody | null>(null);
  const detailsError = details.length > 0 && details.trim().length < 10 ? "Please describe the issue in at least 10 characters." : null;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (details.trim().length < 10) return;
    setState("sending");
    setErr(null);
    try {
      await api("/api/reports", { method: "POST", json: { kind, url, details, contact } });
      setState("sent");
    } catch (e2) {
      setErr(toApiError(e2));
      setState("idle");
    }
  };

  return (
    <div className="container container-narrow page">
      <h1>Report abuse or a copyright concern</h1>
      <p className="lead">If you believe OmniConvert is being used to infringe your rights or for abuse, tell us here. Reports go to the operator of this deployment.</p>
      {caps?.abuse_contact_email ? (
        <p className="subtle">You can also email <a href={`mailto:${caps.abuse_contact_email}`}>{caps.abuse_contact_email}</a>.</p>
      ) : (
        <p className="muted">The operator hasn&apos;t configured a contact email yet (<code>OC_ABUSE_CONTACT_EMAIL</code>). Reports submitted below are stored for the operator to review.</p>
      )}
      {state === "sent" ? (
        <div className="notice notice-success" role="status"><CircleCheck size={16} aria-hidden /><p style={{ margin: 0 }}>Thanks, your report was received.</p></div>
      ) : (
        <form className="panel panel-pad stack" onSubmit={submit} style={{ marginTop: 18 }} noValidate>
          <div className="field">
            <label htmlFor="r-kind">Type</label>
            <select id="r-kind" className="select" value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="copyright">Copyright</option>
              <option value="abuse">Abuse or misuse</option>
              <option value="security">Security issue</option>
              <option value="other">Other</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="r-url">Related link (optional)</label>
            <input id="r-url" className="input" type="url" value={url} onChange={(e) => setUrl(e.target.value)} maxLength={2048} />
          </div>
          <div className="field">
            <label htmlFor="r-details">Details</label>
            <textarea id="r-details" className="input" value={details} onChange={(e) => setDetails(e.target.value)} maxLength={5000} required aria-invalid={!!detailsError} aria-describedby={detailsError ? "r-details-err" : undefined} />
            {detailsError && <span id="r-details-err" className="field-error">{detailsError}</span>}
          </div>
          <div className="field">
            <label htmlFor="r-contact">Your contact email (optional)</label>
            <input id="r-contact" className="input" type="email" value={contact} onChange={(e) => setContact(e.target.value)} maxLength={320} autoComplete="email" />
            <span className="field-hint">Only used to follow up on this report.</span>
          </div>
          {err && <div className="notice notice-danger" role="alert"><TriangleExclamationPoint size={16} aria-hidden /><p style={{ margin: 0 }}>{err.message} {err.action}</p></div>}
          <div><button className="btn btn-primary" type="submit" disabled={state === "sending" || details.trim().length < 10}>{state === "sending" && <LoaderCircle size={16} className="spin" aria-hidden />} Submit report</button></div>
        </form>
      )}
    </div>
  );
}
