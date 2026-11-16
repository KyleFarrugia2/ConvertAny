import { Converter } from "@/components/Converter";
import { JobList } from "@/components/JobList";

export default function Home() {
  return (
    <>
      <div className="container">
        <section className="hero">
          <h1>Your media. <span className="accent">Any format.</span></h1>
          <p>Convert supported links and your own files into high-quality video or audio. Choose your format, resolution, sound quality, and frame rate.</p>
        </section>
        <Converter />
      </div>
      <section id="queue" className="container" aria-labelledby="queue-heading" style={{ marginTop: 40, scrollMarginTop: 80 }}>
        <div className="row" style={{ marginBottom: 14 }}>
          <h2 id="queue-heading" style={{ fontSize: 20, margin: 0 }}>Your conversions</h2>
          <span className="spacer" />
          <a href="/queue" className="muted">Open queue</a>
        </div>
        <JobList limit={8} />
      </section>
    </>
  );
}
