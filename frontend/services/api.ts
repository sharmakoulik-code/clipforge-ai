export type User = { id: string; email: string; display_name: string | null; is_admin: boolean; created_at: string };
export type Project = {
  id: string; name: string; description: string | null; status: string;
  source_filename: string | null; source_url: string | null; source_metadata: Record<string, unknown> | null;
  created_at: string; updated_at: string;
};
export type ProcessingJob = {
  id: string; project_id: string; status: string; current_stage: string | null;
  progress: number; attempt: number; error_code: string | null; error_message: string | null;
  created_at: string; updated_at: string;
};
export type DashboardData = {
  total_projects: number; total_clips: number; processing_videos: number; available_credits: number;
  subscription_status: string | null; recent_projects: Project[];
  recent_exports: Array<{ clip_title: string; output_key: string; created_at: string }>;
  processing_history: Array<{ id: string; project_id: string; project_name: string; status: string; current_stage: string | null; progress: number; created_at: string }>;
};

export class ApiError extends Error {
  constructor(message: string, public readonly status: number) { super(message); this.name = "ApiError"; }
}

const API_BASE = "/api";

function csrfToken(): string | undefined {
  if (typeof document === "undefined") return undefined;
  const encoded = document.cookie.split("; ").find(part => part.startsWith("clipforge_csrf="))?.slice("clipforge_csrf=".length);
  return encoded ? decodeURIComponent(encoded) : undefined;
}

export async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    const headers = new Headers(init.headers);
    if (!(init.body instanceof FormData) && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    if (["POST", "PUT", "PATCH", "DELETE"].includes((init.method ?? "GET").toUpperCase())) {
      const csrf = csrfToken();
      if (csrf) headers.set("X-CSRF-Token", csrf);
    }
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      credentials: "include",
      headers,
    });
  } catch {
    throw new ApiError("Network error. Check your connection and try again.", 0);
  }
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const payload = await response.json() as { detail?: string };
      message = payload.detail ?? message;
    } catch { /* response had no JSON body */ }
    if (response.status === 401 && typeof window !== "undefined") window.dispatchEvent(new Event("auth:expired"));
    throw new ApiError(message, response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function uploadProject(
  file: File,
  projectName: string,
  onProgress: (percent: number) => void,
  signal: AbortSignal,
  options: { numClips: number; minClipSeconds: number; maxClipSeconds: number; captionStyle: string; aiProvider: string; transcriptionProvider: string; captionOptions: Record<string, unknown> },
): Promise<{ project: Project; job: ProcessingJob }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const form = new FormData();
    form.append("file", file);
    form.append("project_name", projectName);
    form.append("num_clips", String(options.numClips));
    form.append("min_clip_seconds", String(options.minClipSeconds));
    form.append("max_clip_seconds", String(options.maxClipSeconds));
    form.append("caption_style", options.captionStyle);
    form.append("ai_provider", options.aiProvider);
    form.append("transcription_provider", options.transcriptionProvider);
    form.append("caption_options_json", JSON.stringify(options.captionOptions));
    xhr.open("POST", `${API_BASE}/projects/upload`);
    xhr.withCredentials = true;
    const csrf = csrfToken();
    if (csrf) xhr.setRequestHeader("X-CSRF-Token", csrf);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.min(99, Math.round(event.loaded / event.total * 100)));
    };
    xhr.onload = () => {
      let data: unknown;
      try { data = JSON.parse(xhr.responseText); } catch { data = {}; }
      if (xhr.status >= 200 && xhr.status < 300) {
        onProgress(100);
        resolve(data as { project: Project; job: ProcessingJob });
      } else {
        if (xhr.status === 401) window.dispatchEvent(new Event("auth:expired"));
        const detail = (data as { detail?: string }).detail ?? `Upload failed (${xhr.status})`;
        reject(new ApiError(detail, xhr.status));
      }
    };
    xhr.onerror = () => reject(new ApiError("Network error while uploading. Try again.", 0));
    xhr.onabort = () => reject(new DOMException("Upload cancelled", "AbortError"));
    signal.addEventListener("abort", () => xhr.abort(), { once: true });
    xhr.send(form);
  });
}
