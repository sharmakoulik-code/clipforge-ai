"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, Check, CircleAlert, Clapperboard, CloudUpload, FileVideo2, Link2, LoaderCircle, ShieldCheck, Youtube } from "lucide-react";
import { ChangeEvent, DragEvent, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, apiRequest, uploadProject, type Project } from "@/services/api";

const MAX_SIZE = 2 * 1024 * 1024 * 1024;
const VIDEO_EXTENSIONS = [".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".mpeg", ".mpg"];

export default function UploadPage() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [rights, setRights] = useState(false);
  const [error, setError] = useState("");
  const [progress, setProgress] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [youtubeMode, setYoutubeMode] = useState(false);
  const [youtubeUrl, setYoutubeUrl] = useState("");
  const [youtubeBusy, setYoutubeBusy] = useState(false);
  const [youtubeMessage, setYoutubeMessage] = useState("");
  const [controller, setController] = useState<AbortController | null>(null);
  const { data: history } = useQuery({ queryKey: ["projects"], queryFn: () => apiRequest<Project[]>("/projects") });

  function acceptFile(candidate?: File) {
    setError("");
    if (!candidate) return;
    const suffix = `.${candidate.name.split(".").pop()?.toLowerCase() ?? ""}`;
    if (!VIDEO_EXTENSIONS.includes(suffix)) { setFile(null); setError("Choose a video in MP4, MOV, M4V, WebM, MKV, AVI, or MPEG format."); return; }
    if (candidate.size > MAX_SIZE) { setFile(null); setError("This file is over the 2 GB upload limit."); return; }
    if (!candidate.size) { setFile(null); setError("This file is empty. Choose another video."); return; }
    setFile(candidate); setName(candidate.name.replace(/\.[^.]+$/, "").slice(0, 200));
  }
  function fileChanged(event: ChangeEvent<HTMLInputElement>) { acceptFile(event.target.files?.[0]); }
  function dropped(event: DragEvent<HTMLDivElement>) { event.preventDefault(); acceptFile(event.dataTransfer.files?.[0]); }

  async function startUpload() {
    if (!file || !rights) return;
    const aborter = new AbortController(); setController(aborter); setUploading(true); setError(""); setProgress(0);
    try {
      const result = await uploadProject(file, name.trim() || file.name, setProgress, aborter.signal);
      router.push(`/dashboard/projects/${result.project.id}`);
    } catch (cause) {
      if (cause instanceof DOMException && cause.name === "AbortError") setError("Upload transfer cancelled. If the full file had already reached the server, check your projects before retrying.");
      else setError(cause instanceof ApiError ? cause.message : "Upload failed. Please try again.");
    } finally { setUploading(false); setController(null); }
  }

  async function importYoutube(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setYoutubeBusy(true); setError(""); setYoutubeMessage("");
    try {
      const result = await apiRequest<{ project: Project; message: string }>("/projects/youtube", {
        method: "POST", body: JSON.stringify({ url: youtubeUrl, user_confirms_rights: rights, project_name: undefined }),
      });
      setYoutubeMessage(result.message);
      router.push(`/dashboard/projects/${result.project.id}`);
    } catch (cause) { setError(cause instanceof ApiError ? cause.message : "Could not import this link."); }
    finally { setYoutubeBusy(false); }
  }

  return (
    <div className="mx-auto max-w-4xl space-y-7">
      <div><Link href="/dashboard" className="inline-flex items-center gap-2 text-xs text-slate-500 hover:text-violet-600"><ArrowLeft size={14}/> Back to overview</Link><p className="mt-5 text-xs font-semibold uppercase tracking-[.18em] text-violet-600">New project</p><h1 className="mt-2 text-3xl font-semibold tracking-tight">Bring your next story in.</h1><p className="mt-2 text-sm text-slate-500">Upload a video you own or are authorized to process.</p></div>
      <div className="flex gap-2 rounded-xl border border-slate-200 bg-white p-1.5 dark:border-white/10 dark:bg-[#11141d]"><button type="button" onClick={()=>{setYoutubeMode(false);setError("")}} className={`inline-flex items-center gap-2 rounded-lg px-4 py-2.5 text-sm font-medium ${!youtubeMode?"bg-violet-600 text-white":"text-slate-500 hover:bg-slate-50 dark:hover:bg-white/5"}`}><CloudUpload size={16}/>Upload a file</button><button type="button" onClick={()=>{setYoutubeMode(true);setError("")}} className={`inline-flex items-center gap-2 rounded-lg px-4 py-2.5 text-sm font-medium ${youtubeMode?"bg-violet-600 text-white":"text-slate-500 hover:bg-slate-50 dark:hover:bg-white/5"}`}><Link2 size={16}/>YouTube reference</button></div>
      <section className="rounded-2xl border border-slate-200 bg-white p-5 dark:border-white/10 dark:bg-[#11141d] sm:p-7">
        {!youtubeMode ? <>
          <div onDragOver={e=>e.preventDefault()} onDrop={dropped} onClick={()=>!uploading&&inputRef.current?.click()} onKeyDown={e=>e.key==="Enter"&&inputRef.current?.click()} role="button" tabIndex={0} aria-label="Choose or drop a video file" className={`grid min-h-64 cursor-pointer place-items-center rounded-2xl border-2 border-dashed p-7 text-center transition ${file?"border-violet-400 bg-violet-50/50 dark:bg-violet-500/[.04]":"border-slate-200 bg-slate-50 hover:border-violet-300 dark:border-white/10 dark:bg-white/[.02] dark:hover:border-violet-400/40"}`}>
            <input ref={inputRef} type="file" accept={VIDEO_EXTENSIONS.join(",")} onChange={fileChanged} className="sr-only" aria-label="Select a video file"/>
            <div><span className="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300">{file?<FileVideo2 size={24}/>:<CloudUpload size={25}/>}</span><h2 className="mt-4 text-base font-semibold">{file?file.name:"Drop your video here"}</h2><p className="mt-1 text-sm text-slate-500">or <span className="font-semibold text-violet-600">browse files</span> on your device</p><p className="mt-3 text-xs text-slate-400">MP4, MOV, M4V, WebM, MKV, AVI, MPEG · up to 2 GB</p>{file&&<p className="mt-2 text-xs text-slate-500">{(file.size/1024/1024).toFixed(1)} MB · Ready to upload</p>}</div>
          </div>
          {file&&<div className="mt-5"><label className="block text-sm font-medium">Project name<input value={name} onChange={e=>setName(e.target.value)} maxLength={200} className="mt-2 w-full rounded-xl border border-slate-200 bg-white px-3.5 py-3 text-sm outline-none focus:border-violet-500 dark:border-white/10 dark:bg-[#0c0f16]"/></label></div>}
          {uploading&&<div className="mt-5" aria-live="polite"><div className="flex justify-between text-xs"><span className="text-slate-600 dark:text-slate-300">Uploading securely…</span><span>{progress}%</span></div><div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-100 dark:bg-white/10"><div className="h-full rounded-full bg-violet-600 transition-all" style={{width:`${progress}%`}}/></div></div>}
          <div className="mt-5 flex items-start gap-3 rounded-xl bg-slate-50 p-3.5 dark:bg-white/[.035]"><input id="upload-rights" type="checkbox" checked={rights} onChange={e=>setRights(e.target.checked)} className="mt-0.5 h-4 w-4 accent-violet-600"/><label htmlFor="upload-rights" className="text-xs leading-5 text-slate-600 dark:text-slate-400">I own this video or have permission to upload and process it. I understand the file will be stored in my workspace.</label></div>
          <div className="mt-5 flex flex-wrap items-center justify-between gap-3">{file&&!uploading&&<button type="button" onClick={()=>{setFile(null);setProgress(0)}} className="text-sm text-slate-500 hover:text-slate-900 dark:hover:text-white">Remove file</button>}{uploading?<button onClick={()=>controller?.abort()} className="ml-auto rounded-xl border border-slate-200 px-4 py-2.5 text-sm font-medium dark:border-white/10">Cancel upload</button>:<button disabled={!file||!rights} onClick={()=>void startUpload()} className="ml-auto inline-flex items-center gap-2 rounded-xl bg-violet-600 px-5 py-3 text-sm font-semibold text-white transition hover:bg-violet-500 disabled:cursor-not-allowed disabled:opacity-40">Upload video <ArrowRight size={16}/></button>}</div>
        </> : <>
          <div className="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-red-50 text-red-600 dark:bg-red-500/10 dark:text-red-300"><Youtube size={25}/></div><h2 className="mt-4 text-center text-lg font-semibold">Save a YouTube reference</h2><p className="mx-auto mt-2 max-w-lg text-center text-sm leading-6 text-slate-500">We can look up public metadata when available, but we do not download YouTube video or bypass access controls. Upload a source file you are authorized to use to process it.</p>
          <form onSubmit={importYoutube} className="mx-auto mt-6 max-w-xl space-y-4"><label className="block text-sm font-medium">YouTube video URL<input required type="url" value={youtubeUrl} onChange={e=>setYoutubeUrl(e.target.value)} placeholder="https://www.youtube.com/watch?v=…" className="mt-2 w-full rounded-xl border border-slate-200 bg-white px-3.5 py-3 outline-none focus:border-violet-500 dark:border-white/10 dark:bg-[#0c0f16]"/></label><div className="flex items-start gap-3 rounded-xl bg-slate-50 p-3.5 dark:bg-white/[.035]"><input id="youtube-rights" type="checkbox" checked={rights} onChange={e=>setRights(e.target.checked)} className="mt-0.5 h-4 w-4 accent-violet-600"/><label htmlFor="youtube-rights" className="text-xs leading-5 text-slate-600 dark:text-slate-400">I confirm I have the rights or permission to use this video.</label></div><button disabled={!rights||youtubeBusy} className="flex w-full items-center justify-center gap-2 rounded-xl bg-violet-600 px-4 py-3 text-sm font-semibold text-white disabled:opacity-50">{youtubeBusy?<LoaderCircle className="animate-spin" size={16}/>:<Link2 size={16}/>}Save reference</button></form>{youtubeMessage&&<p role="status" className="mx-auto mt-4 max-w-xl rounded-lg bg-amber-50 p-3 text-xs text-amber-800 dark:bg-amber-500/10 dark:text-amber-200">{youtubeMessage}</p>}
        </>}
        {error&&<p role="alert" className="mt-5 flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300"><CircleAlert size={16} className="mt-0.5 shrink-0"/>{error}</p>}
      </section>
      <section className="flex items-start gap-3 rounded-2xl border border-emerald-200 bg-emerald-50/60 p-4 dark:border-emerald-500/15 dark:bg-emerald-500/[.04]"><ShieldCheck size={18} className="mt-0.5 shrink-0 text-emerald-600"/><div><p className="text-sm font-semibold">Your content, responsibly handled</p><p className="mt-1 text-xs leading-5 text-slate-600 dark:text-slate-400">We only process files you submit. Uploaded files are kept in your private workspace and can be removed with the project.</p></div></section>
      <section><div className="mb-3 flex items-center justify-between"><h2 className="text-sm font-semibold">Upload history</h2><span className="text-xs text-slate-500">{history?.length??0} projects</span></div>{history?.length ? <div className="space-y-2">{history.slice(0,4).map(p=><Link key={p.id} href={`/dashboard/projects/${p.id}`} className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm hover:border-violet-300 dark:border-white/10 dark:bg-[#11141d]"><FileVideo2 size={16} className="text-violet-600"/><span className="flex-1 truncate">{p.name}</span><span className="text-xs text-slate-500">{p.status.replaceAll("_"," ")}</span></Link>)}</div>:<div className="rounded-xl border border-dashed border-slate-200 p-5 text-center text-xs text-slate-500 dark:border-white/10">Your recent uploads and project references will appear here.</div>}</section>
    </div>
  );
}
