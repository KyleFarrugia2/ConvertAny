import type { ApiErrorBody } from "./types";

export class ApiError extends Error {
  body: ApiErrorBody;
  status: number;
  constructor(status: number, body: ApiErrorBody) {
    super(body.message);
    this.status = status;
    this.body = body;
  }
}

const NETWORK_ERROR: ApiErrorBody = {
  code: "network",
  message: "We couldn't reach the server.",
  action: "Check your connection and try again.",
  retryable: true,
  fields: {},
};

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { "x-omniconvert": "1", ...(init.headers as Record<string, string>) };
  let body = init.body;
  if (init.json !== undefined) {
    headers["content-type"] = "application/json";
    body = JSON.stringify(init.json);
  }
  let res: Response;
  try {
    res = await fetch(path, { ...init, headers, body, credentials: "same-origin" });
  } catch {
    throw new ApiError(0, NETWORK_ERROR);
  }
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    /* non-JSON */
  }
  if (!res.ok) {
    const err = (data as { error?: ApiErrorBody })?.error;
    throw new ApiError(res.status, err ?? { ...NETWORK_ERROR, code: `http_${res.status}`, message: `The server returned an error (${res.status}).`, action: "Try again in a moment." });
  }
  return data as T;
}

export function toApiError(e: unknown): ApiErrorBody {
  if (e instanceof ApiError) return e.body;
  return { ...NETWORK_ERROR, message: e instanceof Error ? e.message : String(e) };
}

/* ------------------------------------------------------------------------ */
/* Resumable chunked upload                                                  */
/* ------------------------------------------------------------------------ */

interface UploadDto {
  id: string;
  chunk_bytes: number;
  total_chunks: number;
  received_chunks: number[];
  status: string;
  transport: "api" | "s3";
  media: import("./types").MediaInfo | null;
}

export interface UploadHandle {
  promise: Promise<UploadDto>;
  cancel: () => void;
  uploadId: () => string | null;
}

function putWithProgress(url: string, blob: Blob, direct: boolean, onProgress: (loaded: number) => void, signal: { aborted: boolean; xhr?: XMLHttpRequest }): Promise<string | null> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    signal.xhr = xhr;
    xhr.open("PUT", url);
    if (!direct) xhr.setRequestHeader("x-omniconvert", "1");
    xhr.upload.onprogress = (ev) => onProgress(ev.loaded);
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve(xhr.getResponseHeader("ETag"));
      else {
        let body: ApiErrorBody | null = null;
        try {
          body = JSON.parse(xhr.responseText).error;
        } catch {
          /* ignore */
        }
        reject(new ApiError(xhr.status, body ?? { ...NETWORK_ERROR, message: `Upload failed (${xhr.status}).` }));
      }
    };
    xhr.onerror = () => reject(new ApiError(0, NETWORK_ERROR));
    xhr.onabort = () => reject(new ApiError(0, { ...NETWORK_ERROR, code: "cancelled", message: "Upload cancelled.", action: "" }));
    xhr.send(blob);
  });
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * Uploads a file in chunks with real per-byte progress. Chunks are retried with
 * backoff; a failed upload can be resumed later with `resumeId` and only the
 * missing chunks are sent.
 */
export function uploadFile(file: File, onProgress: (fraction: number, phase: "uploading" | "verifying") => void, resumeId?: string): UploadHandle {
  const signal: { aborted: boolean; xhr?: XMLHttpRequest } = { aborted: false };
  let currentId: string | null = resumeId ?? null;

  const promise = (async () => {
    const up: UploadDto = resumeId
      ? await api<UploadDto>(`/api/uploads/${resumeId}`)
      : await api<UploadDto>("/api/uploads", { method: "POST", json: { filename: file.name, size: file.size } });
    currentId = up.id;
    const done = new Set(up.received_chunks);
    const etags: string[] = new Array(up.total_chunks).fill("");
    const loadedPerChunk = new Map<number, number>();
    for (const i of done) loadedPerChunk.set(i, Math.min(up.chunk_bytes, file.size - i * up.chunk_bytes));
    const report = () => {
      let total = 0;
      loadedPerChunk.forEach((v) => (total += v));
      onProgress(Math.min(0.999, total / file.size), "uploading");
    };
    report();
    const pending = Array.from({ length: up.total_chunks }, (_, i) => i).filter((i) => !done.has(i) || up.transport === "s3");
    const PARALLEL = 3;
    let cursor = 0;
    const workers = Array.from({ length: Math.min(PARALLEL, pending.length) }, async () => {
      while (cursor < pending.length) {
        const i = pending[cursor++];
        const blob = file.slice(i * up.chunk_bytes, Math.min(file.size, (i + 1) * up.chunk_bytes));
        for (let attempt = 0; ; attempt++) {
          if (signal.aborted) throw new ApiError(0, { ...NETWORK_ERROR, code: "cancelled", message: "Upload cancelled.", action: "" });
          try {
            let url = `/api/uploads/${up.id}/chunks/${i}`;
            let direct = false;
            if (up.transport === "s3") {
              const r = await api<{ url: string; direct: boolean }>(`/api/uploads/${up.id}/parts/${i}/url`, { method: "POST" });
              url = r.url;
              direct = r.direct;
            }
            const etag = await putWithProgress(url, blob, direct, (loaded) => {
              loadedPerChunk.set(i, loaded);
              report();
            }, signal);
            if (etag) etags[i] = etag;
            loadedPerChunk.set(i, blob.size);
            report();
            break;
          } catch (e) {
            if (signal.aborted || (e instanceof ApiError && e.status >= 400 && e.status < 500 && e.status !== 429)) throw e;
            if (attempt >= 4) throw e;
            await sleep(500 * 2 ** attempt);
          }
        }
      }
    });
    await Promise.all(workers);
    onProgress(1, "verifying");
    return api<UploadDto>(`/api/uploads/${up.id}/complete`, { method: "POST", json: up.transport === "s3" ? { etags } : {} });
  })();

  return {
    promise,
    cancel: () => {
      signal.aborted = true;
      signal.xhr?.abort();
    },
    uploadId: () => currentId,
  };
}
