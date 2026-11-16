"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { Capabilities, Job } from "@/lib/types";

/* ---------------- capabilities ---------------- */
interface CapsState { caps: Capabilities | null; error: string | null }
const CapsCtx = createContext<CapsState>({ caps: null, error: null });
export const useCapabilities = () => useContext(CapsCtx);

/* ---------------- announcements (aria-live) ---------------- */
const AnnounceCtx = createContext<(msg: string) => void>(() => {});
export const useAnnounce = () => useContext(AnnounceCtx);

/* ---------------- jobs ---------------- */
interface JobsState {
  jobs: Job[];
  loaded: boolean;
  refresh: () => Promise<void>;
  upsert: (jobs: Job[]) => void;
  remove: (id: string) => void;
}
const JobsCtx = createContext<JobsState>({ jobs: [], loaded: false, refresh: async () => {}, upsert: () => {}, remove: () => {} });
export const useJobs = () => useContext(JobsCtx);

const ACTIVE = new Set(["pending", "analyzing", "queued", "processing"]);

export function Providers({ children }: { children: React.ReactNode }) {
  const [caps, setCaps] = useState<CapsState>({ caps: null, error: null });
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [message, setMessage] = useState("");
  const prevStatus = useRef<Map<string, string>>(new Map());

  const announce = useCallback((msg: string) => {
    setMessage("");
    requestAnimationFrame(() => setMessage(msg));
  }, []);

  useEffect(() => {
    api<Capabilities>("/api/capabilities")
      .then((c) => setCaps({ caps: c, error: null }))
      .catch(() => setCaps({ caps: null, error: "The conversion service is unreachable right now." }));
  }, []);

  const applyJobs = useCallback((list: Job[]) => {
    // Announce only meaningful terminal transitions, not every progress tick.
    for (const j of list) {
      const before = prevStatus.current.get(j.id);
      if (before && before !== j.status) {
        if (j.status === "completed") announce(`${j.source.title || "Conversion"} is ready to download.`);
        else if (j.status === "failed") announce(`${j.source.title || "Conversion"} failed: ${j.error?.message ?? ""}`);
      }
      prevStatus.current.set(j.id, j.status);
    }
    setJobs(list);
  }, [announce]);

  const refresh = useCallback(async () => {
    try {
      const r = await api<{ jobs: Job[] }>("/api/jobs");
      applyJobs(r.jobs);
    } catch {
      /* keep last known state; polling retries */
    } finally {
      setLoaded(true);
    }
  }, [applyJobs]);

  const upsert = useCallback((list: Job[]) => {
    setJobs((cur) => {
      const map = new Map(cur.map((j) => [j.id, j]));
      for (const j of list) {
        map.set(j.id, j);
        prevStatus.current.set(j.id, j.status);
      }
      return Array.from(map.values()).sort((a, b) => b.created_at.localeCompare(a.created_at));
    });
  }, []);

  const remove = useCallback((id: string) => setJobs((cur) => cur.filter((j) => j.id !== id)), []);

  // Controlled polling: fast while something is active, slow otherwise, paused in background tabs.
  const anyActive = jobs.some((j) => ACTIVE.has(j.status));
  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      if (stop) return;
      if (document.visibilityState === "visible") await refresh();
      timer = setTimeout(tick, anyActive ? 1200 : 15000);
    };
    tick();
    const onVis = () => document.visibilityState === "visible" && refresh();
    document.addEventListener("visibilitychange", onVis);
    return () => {
      stop = true;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [anyActive, refresh]);

  const jobsValue = useMemo(() => ({ jobs, loaded, refresh, upsert, remove }), [jobs, loaded, refresh, upsert, remove]);

  return (
    <CapsCtx.Provider value={caps}>
      <AnnounceCtx.Provider value={announce}>
        <JobsCtx.Provider value={jobsValue}>
          {children}
          <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">{message}</div>
        </JobsCtx.Provider>
      </AnnounceCtx.Provider>
    </CapsCtx.Provider>
  );
}
