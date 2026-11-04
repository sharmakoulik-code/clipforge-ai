"use client";

import { useEffect, useRef, useState, type MouseEvent } from "react";
import { apiRequest, ApiError } from "@/services/api";

type Word = { word: string; start: number; end: number };
type CaptionSegment = { start: number; end: number; text: string; words?: Word[] | null };
type Clip = {
  id: string; project_id: string; title: string; start_time: number; end_time: number;
  original_start_time?: number; original_end_time?: number; caption_style: string;
  caption_options: Record<string, unknown>; description?: string; hashtags?: string[];
  export_filename?: string; crop_settings?: { mode?: string; center_x?: number; center_y?: number; zoom?: number };
  hide_captions?: boolean;
};
type Version = { version: number; status: string; created_at: string; video_url: string; settings: Record<string, unknown> };

const fmt = (seconds: number) => `${Math.floor(Math.max(0, seconds) / 60)}:${String(Math.floor(Math.max(0, seconds) % 60)).padStart(2, "0")}.${String(Math.floor(Math.max(0, seconds) % 1 * 10))}`;
const field = "mt-1.5 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm dark:border-white/10 dark:bg-[#0c0f16]";

export function ClipEditor({ clip, sourceDuration, onClose, onSaved }: {
  clip: Clip; sourceDuration: number; onClose: () => void; onSaved: () => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [title, setTitle] = useState(clip.title);
  const [description, setDescription] = useState(clip.description ?? "");
  const [hashtags, setHashtags] = useState((clip.hashtags ?? []).join(" "));
  const [filename, setFilename] = useState(clip.export_filename || `${clip.title.replace(/[^\w -]/g, "_")}.mp4`);
  const [start, setStart] = useState(clip.start_time);
  const [end, setEnd] = useState(clip.end_time);
  const [now, setNow] = useState(clip.start_time);
  const [cropMode, setCropMode] = useState(clip.crop_settings?.mode ?? "auto");
  const [centerX, setCenterX] = useState(clip.crop_settings?.center_x ?? 0.5);
  const [centerY, setCenterY] = useState(clip.crop_settings?.center_y ?? 0.5);
  const [zoom, setZoom] = useState(clip.crop_settings?.zoom ?? 1);
  const [captionStyle, setCaptionStyle] = useState(clip.caption_style || "creator");
  const [fontSize, setFontSize] = useState(Number(clip.caption_options?.font_size ?? 64));
  const [fontFamily, setFontFamily] = useState(String(clip.caption_options?.font_family ?? "Arial"));
  const [textColor, setTextColor] = useState(String(clip.caption_options?.text_color ?? "#ffffff"));
  const [activeColor, setActiveColor] = useState(String(clip.caption_options?.active_word_color ?? "#ffd700"));
  const [position, setPosition] = useState(String(clip.caption_options?.position ?? "bottom"));
  const [animation, setAnimation] = useState(String(clip.caption_options?.animation ?? "none"));
  const [hidden, setHidden] = useState(Boolean(clip.hide_captions));
  const [segments, setSegments] = useState<CaptionSegment[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [suggestions, setSuggestions] = useState<{ title_suggestions: string[]; description_suggestion: string; hashtag_suggestions: string[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const { data: captionData } = useQueryShim(clip.id);
  const { data: versionsData, refresh: refreshVersions } = useVersions(clip.id);

  useEffect(() => { if (captionData?.segments) setSegments(captionData.segments as CaptionSegment[]); }, [captionData]);
  const currentCaption = segments.find(row => now >= row.start && now <= row.end)?.text ?? "";
  const duration = Math.max(0, end - start);

  function seek(value: number) {
    const bounded = Math.max(0, Math.min(sourceDuration, value));
    setNow(bounded);
    if (videoRef.current) videoRef.current.currentTime = bounded;
  }

  function setBoundary(which: "start" | "end", value: number) {
    const bounded = Math.max(0, Math.min(sourceDuration, value));
    if (which === "start") { setStart(bounded); if (now < bounded) seek(bounded); }
    else setEnd(bounded);
  }

  function updateWord(segmentIndex: number, wordIndex: number, value: string) {
    setSegments(previous => previous.map((segment, i) => {
      if (i !== segmentIndex || !segment.words) return segment;
      const words = segment.words.map((word, j) => j === wordIndex ? { ...word, word: value } : word);
      return { ...segment, words, text: words.map(word => word.word).join(" ") };
    }));
  }

  function updateWordTime(segmentIndex: number, wordIndex: number, fieldName: "start" | "end", value: number) {
    setSegments(previous => previous.map((segment, i) => i !== segmentIndex || !segment.words ? segment : {
      ...segment, words: segment.words.map((word, j) => j === wordIndex ? { ...word, [fieldName]: value } : word),
    }));
  }

  async function save() {
    setBusy(true); setError("");
    try {
      await apiRequest(`/clips/${clip.id}`, { method: "PATCH", body: JSON.stringify({
        title, description, hashtags: hashtags.split(/[\s,]+/).filter(Boolean), export_filename: filename,
        start_time: start, end_time: end, caption_style: captionStyle,
        caption_options: { font_family: fontFamily, font_size: fontSize, text_color: textColor, active_word_color: activeColor, position, animation },
        caption_segments: segments, hide_captions: hidden,
        crop_settings: { mode: cropMode, center_x: centerX, center_y: centerY, zoom },
      }) });
      await apiRequest(`/clips/${clip.id}/render`, { method: "POST" });
      await refreshVersions(); onSaved(); onClose();
    } catch (cause) { setError(cause instanceof ApiError ? cause.message : "Could not save editor changes."); }
    finally { setBusy(false); }
  }

  async function action(path: string, success: string) {
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await apiRequest<Clip>(path, { method: "POST" });
      if (path.endsWith("restore-ai-boundaries")) { setStart(result.start_time); setEnd(result.end_time); seek(result.start_time); }
      if (path.endsWith("restore-ai-crop")) { setCropMode("auto"); setCenterX(0.5); setCenterY(0.5); setZoom(1); }
      setNotice(success); onSaved(); await refreshVersions();
    } catch (cause) { setError(cause instanceof ApiError ? cause.message : "Action could not be completed."); }
    finally { setBusy(false); }
  }

  async function loadSuggestions() {
    setBusy(true); setError(""); setNotice("");
    try {
      setSuggestions(await apiRequest<{ title_suggestions: string[]; description_suggestion: string; hashtag_suggestions: string[] }>(`/clips/${clip.id}/suggestions`));
      setNotice("Suggestions came from the saved AI highlight analysis; review and apply them manually.");
    } catch (cause) { setError(cause instanceof ApiError ? cause.message : "Could not load suggestions."); }
    finally { setBusy(false); }
  }

  async function restore(version: number) {
    setBusy(true); setError("");
    try { await apiRequest(`/clips/${clip.id}/versions/${version}/restore`, { method: "POST" }); onSaved(); onClose(); }
    catch (cause) { setError(cause instanceof ApiError ? cause.message : "Could not restore this render."); }
    finally { setBusy(false); }
  }

  function setCropFromPointer(event: MouseEvent<HTMLDivElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    setCenterX(Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)));
    setCenterY(Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)));
    setCropMode("manual");
  }

  return <div className="fixed inset-0 z-50 overflow-y-auto bg-black/70 p-3 backdrop-blur-sm sm:p-6" role="dialog" aria-modal="true" aria-label={`Edit ${clip.title}`}>
    <div className="mx-auto max-w-6xl rounded-2xl border border-slate-200 bg-white shadow-2xl dark:border-white/10 dark:bg-[#11141d]">
      <header className="sticky top-0 z-10 flex items-center justify-between border-b border-slate-100 bg-white/95 px-5 py-4 backdrop-blur dark:border-white/5 dark:bg-[#11141d]/95"><div><p className="text-[10px] font-semibold uppercase tracking-[.18em] text-violet-600">Clip editor</p><h2 className="mt-1 text-lg font-semibold">{clip.title}</h2></div><button onClick={onClose} className="rounded-lg px-3 py-2 text-sm text-slate-500 hover:bg-slate-100 dark:hover:bg-white/5">Close</button></header>
      <div className="grid gap-6 p-4 sm:p-6 lg:grid-cols-[minmax(280px,.8fr)_minmax(0,1.2fr)]">
        <div className="space-y-3">
          <div className="relative mx-auto aspect-[9/16] w-full max-w-[320px] overflow-hidden rounded-xl bg-black" onClick={setCropFromPointer} title="Click to reposition the crop">
            <video ref={videoRef} src={`/api/projects/${clip.project_id}/source`} controls playsInline preload="metadata" className="h-full w-full object-cover"
              style={{ objectPosition: `${centerX * 100}% ${centerY * 100}%`, transform: `scale(${zoom})` }}
              onLoadedMetadata={event => seek(start)} onTimeUpdate={event => { const time = event.currentTarget.currentTime; setNow(time); if (end > start && time >= end) event.currentTarget.pause(); }} />
            {currentCaption && !hidden && <div className="pointer-events-none absolute bottom-[18%] left-3 right-3 rounded bg-black/60 px-2 py-1 text-center text-sm font-bold text-white" style={{ color: textColor, fontFamily, fontSize: Math.min(32, fontSize / 2) }}>{currentCaption}</div>}
          </div>
          <div className="flex items-center justify-between text-xs text-slate-500"><span>Source time {fmt(now)}</span><span>Clip {fmt(duration)} · Source {fmt(sourceDuration)}</span></div>
          <label className="block text-xs font-medium">Scrub source <input type="range" min={0} max={sourceDuration || 1} step={0.1} value={Math.min(now, sourceDuration || 1)} onChange={event => seek(Number(event.target.value))} className="mt-2 w-full accent-violet-600"/></label>
          <div className="grid grid-cols-2 gap-2"><label className="text-xs font-medium">In point (seconds)<input type="number" min={0} max={sourceDuration} step={0.1} value={start} onChange={event => setBoundary("start", Number(event.target.value))} className={field}/></label><label className="text-xs font-medium">Out point (seconds)<input type="number" min={0} max={sourceDuration} step={0.1} value={end} onChange={event => setBoundary("end", Number(event.target.value))} className={field}/></label></div>
          <div className="flex gap-2"><button type="button" onClick={() => seek(start)} className="rounded-lg border border-slate-200 px-3 py-2 text-xs dark:border-white/10">Preview selection</button><button type="button" onClick={() => void action(`/clips/${clip.id}/restore-ai-boundaries`, "AI clip boundaries restored; save and rerender to apply.")} className="rounded-lg border border-slate-200 px-3 py-2 text-xs dark:border-white/10">Restore AI boundaries</button></div>
          <section className="rounded-xl border border-slate-200 p-3 dark:border-white/10"><div className="flex items-center justify-between"><h3 className="text-xs font-semibold">Crop &amp; framing</h3><button type="button" onClick={() => void action(`/clips/${clip.id}/restore-ai-crop`, "AI speaker-centered crop restored.")} className="text-[10px] text-violet-600">Restore AI crop</button></div>
            <label className="mt-3 flex items-center justify-between text-xs">Automatic speaker-centering <input type="checkbox" checked={cropMode === "auto"} onChange={event => setCropMode(event.target.checked ? "auto" : "manual")}/></label>
            <label className="mt-3 block text-xs">Zoom {zoom.toFixed(1)}×<input type="range" min={1} max={3} step={0.1} value={zoom} onChange={event => { setZoom(Number(event.target.value)); setCropMode("manual"); }} className="mt-2 w-full accent-violet-600"/></label>
            <p className="mt-2 text-[10px] text-slate-500">Click or drag focus in the portrait preview for manual framing. Manual crop overrides automatic tracking.</p>
          </section>
        </div>
        <div className="space-y-5">
          <section className="grid gap-3 sm:grid-cols-2"><label className="text-xs font-medium">Title<input maxLength={200} value={title} onChange={event => setTitle(event.target.value)} className={field}/></label><label className="text-xs font-medium">Export filename<input maxLength={160} value={filename} onChange={event => setFilename(event.target.value)} className={field}/></label><label className="text-xs font-medium sm:col-span-2">Description<textarea rows={2} maxLength={2000} value={description} onChange={event => setDescription(event.target.value)} className={field}/></label><label className="text-xs font-medium sm:col-span-2">Hashtags<input value={hashtags} onChange={event => setHashtags(event.target.value)} placeholder="#education #creator" className={field}/></label><div className="sm:col-span-2"><button type="button" disabled={busy} onClick={() => void loadSuggestions()} className="rounded-lg border border-violet-200 px-3 py-2 text-xs font-semibold text-violet-700 dark:border-violet-500/20 dark:text-violet-300">Load AI suggestions</button>{suggestions&&<div className="mt-2 grid gap-2 rounded-lg bg-violet-50 p-3 text-xs dark:bg-violet-500/[.05]"><div className="flex flex-wrap gap-2">{suggestions.title_suggestions.map((item,index)=><button key={index} type="button" onClick={()=>setTitle(item)} className="rounded border border-violet-200 bg-white px-2 py-1 dark:border-white/10 dark:bg-[#11141d]">Use title: {item}</button>)}</div><button type="button" onClick={()=>setDescription(suggestions.description_suggestion)} className="text-left text-violet-700 dark:text-violet-300">Use suggested description: {suggestions.description_suggestion}</button><button type="button" onClick={()=>setHashtags(suggestions.hashtag_suggestions.join(" "))} className="text-left text-violet-700 dark:text-violet-300">Use suggested hashtags: {suggestions.hashtag_suggestions.join(" ")}</button></div>}</div></section>
          <section className="rounded-xl border border-slate-200 p-3 dark:border-white/10"><div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-xs font-semibold">Captions &amp; transcription</h3><label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={hidden} onChange={event => setHidden(event.target.checked)}/> Hide captions</label></div><div className="mt-3 grid gap-3 sm:grid-cols-2"><label className="text-xs">Style<select value={captionStyle} onChange={event => setCaptionStyle(event.target.value)} className={field}>{["classic","creator","minimal","dynamic","custom"].map(item => <option key={item}>{item}</option>)}</select></label><label className="text-xs">Font<input value={fontFamily} onChange={event => setFontFamily(event.target.value)} className={field}/></label><label className="text-xs">Font size<input type="number" min={18} max={120} value={fontSize} onChange={event => setFontSize(Number(event.target.value))} className={field}/></label><label className="text-xs">Position<select value={position} onChange={event => setPosition(event.target.value)} className={field}><option>bottom</option><option>middle</option><option>top</option></select></label><label className="text-xs">Text color<input type="color" value={textColor} onChange={event => setTextColor(event.target.value)} className={`${field} h-9 p-1`}/></label><label className="text-xs">Active word color<input type="color" value={activeColor} onChange={event => setActiveColor(event.target.value)} className={`${field} h-9 p-1`}/></label><label className="text-xs">Animation<select value={animation} onChange={event => setAnimation(event.target.value)} className={field}><option>none</option><option>fade</option><option>pop</option></select></label></div>
            <div className="mt-4 max-h-72 space-y-2 overflow-y-auto">{segments.map((segment, index) => <div key={`${segment.start}-${index}`} className="grid gap-2 rounded-lg bg-slate-50 p-2 dark:bg-white/[.03]"><div className="grid grid-cols-[1fr_1fr_2fr] gap-2"><label className="text-[10px] text-slate-500">Start<input type="number" step="0.05" value={segment.start} onChange={event => setSegments(rows => rows.map((row, i) => i === index ? { ...row, start: Number(event.target.value) } : row))} className={field}/></label><label className="text-[10px] text-slate-500">End<input type="number" step="0.05" value={segment.end} onChange={event => setSegments(rows => rows.map((row, i) => i === index ? { ...row, end: Number(event.target.value) } : row))} className={field}/></label><label className="text-[10px] text-slate-500">Caption text<input value={segment.text} onChange={event => setSegments(rows => rows.map((row, i) => i === index ? { ...row, text: event.target.value } : row))} className={field}/></label></div>
                {segment.words?.length ? <div className="flex flex-wrap gap-2">{segment.words.map((word, wordIndex) => <div key={`${word.start}-${wordIndex}`} className="grid grid-cols-[1fr_1fr_1fr] gap-1"><label className="text-[10px] text-slate-500">Word<input value={word.word} onChange={event => updateWord(index, wordIndex, event.target.value)} className="mt-1 w-full rounded border border-slate-200 bg-white px-2 py-1 text-xs dark:border-white/10 dark:bg-[#0c0f16]"/></label><label className="text-[10px] text-slate-500">In<input type="number" step="0.01" value={word.start} onChange={event => updateWordTime(index, wordIndex, "start", Number(event.target.value))} className="mt-1 w-full rounded border border-slate-200 bg-white px-2 py-1 text-xs dark:border-white/10 dark:bg-[#0c0f16]"/></label><label className="text-[10px] text-slate-500">Out<input type="number" step="0.01" value={word.end} onChange={event => updateWordTime(index, wordIndex, "end", Number(event.target.value))} className="mt-1 w-full rounded border border-slate-200 bg-white px-2 py-1 text-xs dark:border-white/10 dark:bg-[#0c0f16]"/></label></div>)}</div> : <p className="text-[10px] text-slate-400">Phrase timing only (provider supplied no word-level timestamps).</p>}</div>)}</div>
            {!segments.length && <p className="mt-3 text-xs text-slate-500">No transcript segments are available yet.</p>}
          </section>
          <section className="rounded-xl border border-slate-200 p-3 dark:border-white/10"><h3 className="text-xs font-semibold">Saved render versions</h3>{versionsData?.versions?.length ? <ul className="mt-2 space-y-2">{versionsData.versions.map(version => <li key={version.version} className="flex items-center justify-between gap-2 text-xs"><span>Version {version.version} · {new Date(version.created_at).toLocaleString()}</span><button type="button" disabled={busy} onClick={() => void restore(version.version)} className="rounded border border-slate-200 px-2 py-1 text-violet-600 disabled:opacity-50 dark:border-white/10">Restore</button></li>)}</ul> : <p className="mt-2 text-[11px] text-slate-500">Previous validated exports are preserved here after each successful render.</p>}</section>
          {error && <p role="alert" className="rounded-lg bg-rose-50 p-3 text-xs text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">{error}</p>}
          {notice && <p role="status" className="rounded-lg bg-emerald-50 p-3 text-xs text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300">{notice}</p>}
          <div className="flex flex-wrap justify-end gap-2"><button type="button" onClick={onClose} className="rounded-lg border border-slate-200 px-4 py-2 text-xs dark:border-white/10">Cancel</button><button disabled={busy} onClick={() => void save()} className="rounded-lg bg-violet-600 px-4 py-2 text-xs font-semibold text-white disabled:opacity-50">{busy ? "Saving & rendering…" : "Save & render new version"}</button></div>
        </div>
      </div>
    </div>
  </div>;
}

function useQueryShim(clipId: string) {
  const [data, setData] = useState<{ segments: unknown[] } | undefined>();
  useEffect(() => { let mounted = true; void apiRequest<{ segments: unknown[] }>(`/clips/${clipId}/captions`).then(value => { if (mounted) setData(value); }).catch(() => undefined); return () => { mounted = false; }; }, [clipId]);
  return { data };
}

function useVersions(clipId: string) {
  const [data, setData] = useState<{ versions: Version[] } | undefined>();
  const refresh = async () => { try { setData(await apiRequest<{ versions: Version[] }>(`/clips/${clipId}/versions`)); } catch { /* visible actions report failures */ } };
  useEffect(() => { void refresh(); }, [clipId]);
  return { data, refresh };
}
