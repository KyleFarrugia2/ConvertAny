import type { Metadata } from "next";
import { JobList } from "@/components/JobList";

export const metadata: Metadata = { title: "Conversion queue" };

export default function QueuePage() {
  return (
    <div className="container page">
      <h1>Conversion queue</h1>
      <p className="lead">Everything you&apos;ve converted in this browser. Jobs keep running while you browse — you can close this page and come back later in the same browser.</p>
      <div style={{ marginTop: 24 }}><JobList /></div>
    </div>
  );
}
