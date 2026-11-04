"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Edit3, Film, LoaderCircle, RefreshCw, Sparkles } from "lucide-react";
import { FormEvent, useState } from "react";
import { ApiError, apiRequest } from "@/services/api";

type GeneratedClip = {
  id: string; project_id: string; title: string; viral_score: number; score_components: Record<string, number>;
  duration_seconds: number; start_time: number; end_time: number; category: string; hook: string; reason: string;
  transcript: string; status: string; caption_style: string; caption_options: Record<string, unknown>;
  thumbnail_url: string | null; preview_url: string | null; video_url: string | null;
};

const formatTime = (seconds: number) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;

export function GeneratedClips({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const [sort, setSort] = useState("score");
  const [category, setCategory] = useState("all");
  const [selected, setSelected] = useState<string[]>([]);
  const [editing, setEditing] = useState<GeneratedClip | null>(null);
  const [title, setTitle] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [captionStyle, setCaptionStyle] = useState("creator");
  const [fontFamily, setFontFamily] = useState("Arial");
  const [fontSize, setFontSize] = useState(64);
  const [textColor, setTextColor] = useState("#ffffff");
  const [activeColor, setActiveColor] = useState("#ffd700");
  const [backgroundOpacity, setBackgroundOpacity] = useState(55);
  const [outline, setOutline] = useState(3);
  const [position, setPosition] = useState("bottom");
  const [animation, setAnimation] = useState("none");
  const [wordsPerLine, setWordsPerLine] = useState(7);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const { data, isLoading, refetch } = useQuery({
    queryKey: ["clips", projectId, sort, category],
    queryFn: () => apiRequest<{ project_id: string; categories: string[]; clips: GeneratedClip[] }>(`/projects/${projectId}/clips?sort=${sort}&category=${encodeURIComponent(category)}`),
    refetchInterval: query => query.state.data?.clips.some(clip => ["rendering", "needs_render"].includes(clip.status)) ? 4000 : false,
  });
  const clips = data?.clips ?? [];
  const finished = clips.filter(clip => clip.video_url);

  function beginEdit(clip: GeneratedClip) {
    const custom = clip.caption_options ?? {};
    setError(""); setEditing(clip); setTitle(clip.title); setStart(String(clip.start_time)); setEnd(String(clip.end_time)); setCaptionStyle(clip.caption_style || "creator");
    setFontFamily(String(custom.font_family ?? "Arial")); setFontSize(Number(custom.font_size ?? 64));
    setTextColor(String(custom.text_color ?? "#ffffff")); setActiveColor(String(custom.active_word_color ?? "#ffd700"));
    setBackgroundOpacity(Number(custom.background_opacity ?? 55)); setOutline(Number(custom.outline ?? 3));
    setPosition(String(custom.position ?? "bottom")); setAnimation(String(custom.animation ?? "none")); setWordsPerLine(Number(custom.words_per_line ?? 7));
  }

  async function saveEdit(event: FormEvent) {
    event.preventDefault();
    if (!editing) return;
    setBusy(true); setError("");
    try {
      await apiRequest(`/clips/${editing.id}`, { method: "PATCH", body: JSON.stringify({ title, start_time: Number(start), end_time: Number(end), caption_style: captionStyle, caption_options: { font_family: fontFamily, font_size: fontSize, text_color: textColor, active_word_color: activeColor, background_opacity: backgroundOpacity, outline, position, animation, words_per_line: wordsPerLine } }) });
      await apiRequest(`/clips/${editing.id}/render`, { method: "POST" });
      setEditing(null);
      await queryClient.invalidateQueries({ queryKey: ["clips", projectId] });
    } catch (cause) { setError(cause instanceof ApiError ? cause.message : "Could not update or queue this clip."); }
    finally { setBusy(false); }
  }

  async function downloadSelected() {
    if (!selected.length) return;
    setBusy(true); setError("");
    try {
      const csrfValue = document.cookie.split("; ").find(part => part.trim().startsWith("clipforge_csrf="))?.trim().slice("clipforge_csrf=".length);
      const response = await fetch(`/api/projects/${projectId}/clips/download`, {
        method: "POST", credentials: "include", headers: { "Content-Type": "application/json", ...(csrfValue ? { "X-CSRF-Token": decodeURIComponent(csrfValue) } : {}) }, body: JSON.stringify(selected),
      });
      if (!response.ok) throw new Error(`Could not download clips (${response.status}).`);
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement("a"); anchor.href = url; anchor.download = "clips.zip"; anchor.click(); URL.revokeObjectURL(url);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not download selected clips."); }
    finally { setBusy(false); }
  }

  return <section className="rounded-2xl border border-slate-200 bg-white dark:border-white/10 dark:bg-[#11141d]">
    <div className="flex flex-col justify-between gap-3 border-b border-slate-100 px-5 py-4 dark:border-white/5 sm:flex-row sm:items-center">
      <div className="flex items-center gap-2"><Film size={16} className="text-violet-600"/><div><h2 className="text-sm font-semibold">Generated clips <span className="ml-1 text-xs font-normal text-slate-500">({clips.length})</span></h2><p className="mt-1 text-xs text-slate-500">Rendered vertical clips selected from your source video</p></div></div>
      <div className="flex flex-wrap items-center gap-2"><label className="sr-only" htmlFor="clips-sort">Sort clips</label><select id="clips-sort" value={sort} onChange={e=>setSort(e.target.value)} className="rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-xs dark:border-white/10 dark:bg-[#0c0f16]"><option value="score">Highest score</option><option value="newest">Newest</option><option value="duration">Duration</option><option value="category">Category order</option></select><label className="sr-only" htmlFor="clip-category">Filter by category</label><select id="clip-category" value={category} onChange={e=>setCategory(e.target.value)} className="rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-xs dark:border-white/10 dark:bg-[#0c0f16]"><option value="all">All categories</option>{(data?.categories??[]).map(value=><option key={value} value={value}>{value}</option>)}</select><button onClick={()=>void refetch()} aria-label="Refresh clips" className="grid h-9 w-9 place-items-center rounded-lg border border-slate-200 text-slate-500 dark:border-white/10"><RefreshCw size={14}/></button>{selected.length>0&&<button disabled={busy} onClick={()=>void downloadSelected()} className="inline-flex items-center gap-2 rounded-lg bg-violet-600 px-3 py-2 text-xs font-semibold text-white disabled:opacity-50"><Download size={14}/>Download {selected.length}</button>}</div>
    </div>
    {error&&<p role="alert" className="m-4 rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">{error}</p>}
    {editing&&<form onSubmit={saveEdit} className="m-4 grid gap-3 rounded-xl border border-violet-200 bg-violet-50/50 p-4 dark:border-violet-500/20 dark:bg-violet-500/[.04] sm:grid-cols-2"><label className="text-xs font-medium">Title<input required maxLength={200} value={title} onChange={e=>setTitle(e.target.value)} className="mt-1.5 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm dark:border-white/10 dark:bg-[#0c0f16]"/></label><label className="text-xs font-medium">Caption style<select value={captionStyle} onChange={e=>setCaptionStyle(e.target.value)} className="mt-1.5 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm dark:border-white/10 dark:bg-[#0c0f16]">{["classic","creator","minimal","dynamic","custom"].map(s=><option key={s} value={s}>{s}</option>)}</select></label><label className="text-xs font-medium">Start time (seconds)<input type="number" min="0" step="0.1" value={start} onChange={e=>setStart(e.target.value)} className="mt-1.5 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm dark:border-white/10 dark:bg-[#0c0f16]"/></label><label className="text-xs font-medium">End time (seconds)<input type="number" min="1" step="0.1" value={end} onChange={e=>setEnd(e.target.value)} className="mt-1.5 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm dark:border-white/10 dark:bg-[#0c0f16]"/></label><fieldset className="rounded-lg border border-slate-200 p-3 dark:border-white/10 sm:col-span-2"><legend className="px-1 text-xs font-semibold">Caption customization</legend><div className="grid gap-3 sm:grid-cols-3"><label className="text-[11px] text-slate-500">Font family<input value={fontFamily} onChange={e=>setFontFamily(e.target.value)} maxLength={64} className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-xs dark:border-white/10 dark:bg-[#0c0f16]"/></label><label className="text-[11px] text-slate-500">Font size<input type="number" min={18} max={120} value={fontSize} onChange={e=>setFontSize(Number(e.target.value))} className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-xs dark:border-white/10 dark:bg-[#0c0f16]"/></label><label className="text-[11px] text-slate-500">Text color<input type="color" value={textColor} onChange={e=>setTextColor(e.target.value)} className="mt-1 h-9 w-full rounded-lg border border-slate-200 bg-white p-1 dark:border-white/10 dark:bg-[#0c0f16]"/></label><label className="text-[11px] text-slate-500">Active word color<input type="color" value={activeColor} onChange={e=>setActiveColor(e.target.value)} className="mt-1 h-9 w-full rounded-lg border border-slate-200 bg-white p-1 dark:border-white/10 dark:bg-[#0c0f16]"/></label><label className="text-[11px] text-slate-500">Background opacity<input type="number" min={0} max={100} value={backgroundOpacity} onChange={e=>setBackgroundOpacity(Number(e.target.value))} className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-xs dark:border-white/10 dark:bg-[#0c0f16]"/></label><label className="text-[11px] text-slate-500">Outline width<input type="number" min={0} max={10} value={outline} onChange={e=>setOutline(Number(e.target.value))} className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-xs dark:border-white/10 dark:bg-[#0c0f16]"/></label><label className="text-[11px] text-slate-500">Caption position<select value={position} onChange={e=>setPosition(e.target.value)} className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-xs dark:border-white/10 dark:bg-[#0c0f16]"><option value="bottom">Bottom</option><option value="middle">Middle</option><option value="top">Top</option></select></label><label className="text-[11px] text-slate-500">Animation<select value={animation} onChange={e=>setAnimation(e.target.value)} className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-xs dark:border-white/10 dark:bg-[#0c0f16]"><option value="none">None</option><option value="fade">Fade</option><option value="pop">Pop</option></select></label><label className="text-[11px] text-slate-500">Words per line<input type="number" min={2} max={12} value={wordsPerLine} onChange={e=>setWordsPerLine(Number(e.target.value))} className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-xs dark:border-white/10 dark:bg-[#0c0f16]"/></label></div></fieldset><div className="flex gap-2 sm:col-span-2"><button disabled={busy} className="rounded-lg bg-violet-600 px-4 py-2 text-xs font-semibold text-white disabled:opacity-50">Save &amp; rerender</button><button type="button" onClick={()=>setEditing(null)} className="rounded-lg px-4 py-2 text-xs text-slate-500">Cancel</button></div></form>}
    {isLoading?<div className="grid gap-4 p-5 sm:grid-cols-2 lg:grid-cols-3">{[1,2,3].map(i=><div key={i} className="h-96 animate-pulse rounded-xl bg-slate-100 dark:bg-white/5"/>)}</div>:!clips.length?<div className="px-6 py-12 text-center"><Sparkles className="mx-auto text-violet-500" size={24}/><p className="mt-3 text-sm font-medium">Clips will appear when processing completes</p><p className="mt-1 text-xs text-slate-500">The worker will extract audio, transcribe, score highlights, and render validated MP4s.</p></div>:<div className="grid gap-4 p-4 sm:grid-cols-2 lg:grid-cols-3">{clips.map(clip=><article key={clip.id} className="overflow-hidden rounded-xl border border-slate-200 dark:border-white/10"><div className="relative aspect-[9/16] max-h-[460px] bg-black">{clip.preview_url?<video controls preload="metadata" poster={clip.thumbnail_url??undefined} src={clip.preview_url} className="h-full w-full object-contain"/>:clip.thumbnail_url?<img src={clip.thumbnail_url} alt="Clip thumbnail" className="h-full w-full object-cover"/>:<div className="grid h-full place-items-center text-white"><LoaderCircle className="animate-spin" size={22}/></div>}<label className="absolute left-3 top-3 flex items-center gap-2 rounded-lg bg-black/60 px-2.5 py-1.5 text-xs text-white"><input type="checkbox" disabled={!clip.video_url} checked={selected.includes(clip.id)} onChange={e=>setSelected(prev=>e.target.checked?[...prev,clip.id]:prev.filter(id=>id!==clip.id))} aria-label={`Select ${clip.title}`}/>Select</label><span className="absolute right-3 top-3 rounded-lg bg-violet-600 px-2.5 py-1.5 text-xs font-bold text-white">{Math.round(clip.viral_score)} / 100</span></div><div className="space-y-3 p-4"><div className="flex items-start justify-between gap-2"><div><h3 className="line-clamp-2 text-sm font-semibold">{clip.title}</h3><p className="mt-1 text-[11px] text-slate-500">{clip.category} · {formatTime(clip.duration_seconds)} · {formatTime(clip.start_time)}–{formatTime(clip.end_time)}</p></div><span className="shrink-0 rounded-full bg-slate-100 px-2 py-1 text-[10px] text-slate-600 dark:bg-white/5 dark:text-slate-300">{clip.status.replaceAll("_"," ")}</span></div>{clip.hook&&<p className="text-xs font-medium text-violet-700 dark:text-violet-300">“{clip.hook}”</p>}<p className="line-clamp-3 text-xs leading-5 text-slate-500">{clip.transcript}</p>{Object.keys(clip.score_components).length>0&&<div className="flex flex-wrap gap-1.5">{Object.entries(clip.score_components).slice(0,3).map(([key,value])=><span key={key} className="rounded bg-slate-100 px-1.5 py-1 text-[9px] text-slate-500 dark:bg-white/5">{key.replaceAll("_"," ")}: {Math.round(value)}</span>)}</div>}<div className="flex flex-wrap gap-2 border-t border-slate-100 pt-3 dark:border-white/5"><button onClick={()=>beginEdit(clip)} className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 px-2.5 py-2 text-xs font-medium dark:border-white/10"><Edit3 size={13}/>Edit</button>{clip.video_url&&<a href={`${clip.video_url}?download=true`} className="inline-flex items-center gap-1.5 rounded-lg bg-violet-600 px-2.5 py-2 text-xs font-semibold text-white"><Download size={13}/>Download</a>}</div></div></article>)}</div>}
    {finished.length>1&&<div className="border-t border-slate-100 px-5 py-3 text-xs text-slate-500 dark:border-white/5">Select generated clips to download them together as a ZIP.</div>}
  </section>;
}
