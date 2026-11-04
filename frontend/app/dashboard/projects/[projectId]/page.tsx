"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Check, CircleAlert, Clock3, FileVideo2, Film, LoaderCircle, Pencil, RefreshCw, Trash2, Video } from "lucide-react";
import { FormEvent, useState } from "react";
import { ApiError, apiRequest, type ProcessingJob, type Project } from "@/services/api";

type ProjectData = Project & { jobs: ProcessingJob[]; clip_count: number };
const displayDate = (value: string) => new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
const titleCase = (value: string) => value.replaceAll("_", " ").replace(/\b\w/g, x => x.toUpperCase());

export default function ProjectPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [renameOpen, setRenameOpen] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const { data, isLoading, refetch } = useQuery({
    queryKey: ["project", projectId], queryFn: () => apiRequest<ProjectData>(`/projects/${projectId}`),
    refetchInterval: query => query.state.data?.jobs.some(job => ["queued","uploading","extracting_audio","transcribing","analyzing","detecting_speakers","cropping","captioning","rendering"].includes(job.status)) ? 5000 : false,
  });

  async function rename(event: FormEvent) {
    event.preventDefault(); if (!name.trim()) return;
    setBusy(true); setError("");
    try { await apiRequest<Project>(`/projects/${projectId}`, { method: "PATCH", body: JSON.stringify({ name }) }); await queryClient.invalidateQueries({ queryKey: ["project", projectId] }); await queryClient.invalidateQueries({ queryKey: ["dashboard"] }); setRenameOpen(false); }
    catch (cause) { setError(cause instanceof ApiError ? cause.message : "Could not rename this project."); }
    finally { setBusy(false); }
  }

  async function deleteProject() {
    if (!window.confirm("Delete this project and its uploaded source file? This cannot be undone.")) return;
    setBusy(true); setError("");
    try { await apiRequest<void>(`/projects/${projectId}`, { method: "DELETE" }); await queryClient.invalidateQueries({ queryKey: ["dashboard"] }); router.replace("/dashboard"); }
    catch (cause) { setError(cause instanceof ApiError ? cause.message : "Could not delete this project."); setBusy(false); }
  }

  async function retry(job: ProcessingJob) {
    setBusy(true); setError("");
    try { await apiRequest<ProcessingJob>(`/jobs/${job.id}/retry`, { method: "POST" }); await refetch(); }
    catch (cause) { setError(cause instanceof ApiError ? cause.message : "Could not retry this job."); }
    finally { setBusy(false); }
  }

  if (isLoading) return <div className="h-80 animate-pulse rounded-2xl bg-slate-200 dark:bg-white/10"/>;
  if (!data) return <div role="alert" className="rounded-2xl border border-slate-200 bg-white p-8 dark:border-white/10 dark:bg-[#11141d]"><h1 className="font-semibold">Project not found</h1><p className="mt-2 text-sm text-slate-500">It may have been removed or you may not have access.</p><Link href="/dashboard" className="mt-4 inline-flex text-sm font-semibold text-violet-600">Back to workspace</Link></div>;

  const metadata = data.source_metadata ?? {};
  const latestJob = data.jobs[0];
  const state = latestJob?.status ?? data.status;
  const details = [
    ["Duration", typeof metadata.duration_seconds === "number" ? `${(metadata.duration_seconds/60).toFixed(1)} min` : "—"],
    ["Resolution", metadata.width && metadata.height ? `${metadata.width} × ${metadata.height}` : "—"],
    ["Frame rate", typeof metadata.frame_rate === "number" ? `${metadata.frame_rate.toFixed(2)} fps` : "—"],
    ["Video codec", String(metadata.codec ?? "—")],
    ["Container", String(metadata.container ?? (data.source_url?"YouTube link":"—"))],
    ["Audio streams", metadata.audio_streams === undefined ? "—" : String(metadata.audio_streams)],
    ["File size", typeof metadata.file_size_bytes === "number" ? `${(metadata.file_size_bytes/1024/1024).toFixed(1)} MB` : "—"],
    ["Aspect ratio", String(metadata.aspect_ratio ?? "—")],
  ];

  return (
    <div className="mx-auto max-w-5xl space-y-7">
      <Link href="/dashboard" className="inline-flex items-center gap-2 text-xs text-slate-500 hover:text-violet-600"><ArrowLeft size={14}/> All projects</Link>
      <section className="flex flex-col justify-between gap-5 rounded-2xl border border-slate-200 bg-white p-5 dark:border-white/10 dark:bg-[#11141d] sm:flex-row sm:items-start sm:p-7">
        <div className="flex min-w-0 items-start gap-4"><span className="grid h-12 w-12 shrink-0 place-items-center rounded-2xl bg-violet-50 text-violet-600 dark:bg-violet-500/10 dark:text-violet-300"><Video size={21}/></span><div className="min-w-0"><div className="flex flex-wrap items-center gap-3"><h1 className="break-words text-2xl font-semibold tracking-tight">{data.name}</h1><span className="rounded-full bg-slate-100 px-2.5 py-1 text-[10px] font-medium text-slate-600 dark:bg-white/5 dark:text-slate-300">{titleCase(state)}</span></div><p className="mt-2 break-all text-xs text-slate-500">{data.source_filename||data.source_url||"No source file"}</p><p className="mt-1 text-xs text-slate-400">Created {displayDate(data.created_at)}</p></div></div>
        <div className="flex shrink-0 gap-2"><button onClick={()=>{setName(data.name);setRenameOpen(true)}} className="inline-flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-xs font-medium hover:bg-slate-50 dark:border-white/10 dark:hover:bg-white/5"><Pencil size={14}/> Rename</button><button disabled={busy} onClick={()=>void deleteProject()} className="inline-flex items-center gap-2 rounded-lg border border-rose-200 px-3 py-2 text-xs font-medium text-rose-600 hover:bg-rose-50 disabled:opacity-50 dark:border-rose-500/20 dark:hover:bg-rose-500/10"><Trash2 size={14}/> Delete</button></div>
      </section>
      {renameOpen&&<form onSubmit={rename} className="flex flex-col gap-3 rounded-xl border border-violet-200 bg-white p-4 dark:border-violet-500/20 dark:bg-[#11141d] sm:flex-row"><label className="sr-only" htmlFor="project-name">Project name</label><input id="project-name" autoFocus maxLength={200} value={name} onChange={e=>setName(e.target.value)} className="flex-1 rounded-lg border border-slate-200 bg-transparent px-3 py-2 text-sm outline-none focus:border-violet-500 dark:border-white/10"/><button disabled={busy} className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">Save name</button><button type="button" onClick={()=>setRenameOpen(false)} className="rounded-lg px-4 py-2 text-sm text-slate-500">Cancel</button></form>}
      {error&&<p role="alert" className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">{error}</p>}
      {latestJob&&<section className="rounded-2xl border border-violet-200 bg-violet-50/60 p-5 dark:border-violet-500/20 dark:bg-violet-500/[.04] sm:p-6"><div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center"><div className="flex items-start gap-3"><span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-violet-600 text-white">{state==="completed"?<Check size={18}/>:state==="failed"?<CircleAlert size={18}/>:<LoaderCircle size={18} className="animate-spin"/>}</span><div><h2 className="text-sm font-semibold">Processing job</h2><p className="mt-1 text-xs text-slate-600 dark:text-slate-400">{latestJob.current_stage?titleCase(latestJob.current_stage):titleCase(latestJob.status)} · attempt {latestJob.attempt+1}</p>{latestJob.error_message&&<p className="mt-2 text-xs text-rose-600">{latestJob.error_message}</p>}</div></div>{state==="failed"&&<button onClick={()=>void retry(latestJob)} disabled={busy} className="inline-flex items-center gap-2 rounded-lg bg-violet-600 px-3 py-2 text-xs font-semibold text-white disabled:opacity-50"><RefreshCw size={14}/> Retry job</button>}</div><div className="mt-5 flex items-center justify-between text-[10px] text-slate-500"><span>Job progress</span><span>{latestJob.progress}%</span></div><div className="mt-2 h-2 overflow-hidden rounded-full bg-violet-100 dark:bg-white/10"><div className="h-full rounded-full bg-violet-600 transition-all" style={{width:`${latestJob.progress}%`}}/></div><p className="mt-3 text-[11px] text-slate-500">Stage 2 validates and queues uploads. AI processing stages become available in Stage 3.</p></section>}
      <section className="rounded-2xl border border-slate-200 bg-white dark:border-white/10 dark:bg-[#11141d]"><div className="flex items-center gap-2 border-b border-slate-100 px-5 py-4 dark:border-white/5"><FileVideo2 size={16} className="text-violet-600"/><div><h2 className="text-sm font-semibold">Original video metadata</h2><p className="mt-1 text-xs text-slate-500">Extracted from the uploaded source with FFprobe</p></div></div><div className="grid gap-px bg-slate-100 sm:grid-cols-2 lg:grid-cols-4 dark:bg-white/5">{details.map(([term,value])=><div key={term} className="bg-white px-5 py-4 dark:bg-[#11141d]"><p className="text-[10px] font-medium uppercase tracking-wide text-slate-400">{term}</p><p className="mt-1.5 break-all text-sm font-medium">{value}</p></div>)}</div></section>
      <section className="rounded-2xl border border-slate-200 bg-white dark:border-white/10 dark:bg-[#11141d]"><div className="flex items-center justify-between border-b border-slate-100 px-5 py-4 dark:border-white/5"><div className="flex items-center gap-2"><Clock3 size={16} className="text-violet-600"/><div><h2 className="text-sm font-semibold">Processing history</h2><p className="mt-1 text-xs text-slate-500">Stages tracked for this project</p></div></div><button aria-label="Refresh status" onClick={()=>void refetch()} className="grid h-9 w-9 place-items-center rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-50 dark:border-white/10 dark:hover:bg-white/5"><RefreshCw size={14}/></button></div>{data.jobs.length?<div className="divide-y divide-slate-100 dark:divide-white/5">{data.jobs.map(job=><div key={job.id} className="flex flex-wrap items-center gap-3 px-5 py-4"><span className={`grid h-8 w-8 place-items-center rounded-lg ${job.status==="failed"?"bg-rose-50 text-rose-600":"bg-violet-50 text-violet-600 dark:bg-violet-500/10 dark:text-violet-300"}`}>{job.status==="completed"?<Check size={15}/>:<Film size={15}/>}</span><div className="min-w-0 flex-1"><p className="text-xs font-medium">{titleCase(job.current_stage||job.status)}</p><p className="mt-1 text-[10px] text-slate-500">Started {displayDate(job.created_at)} · {job.id.slice(0,8)}</p></div><span className="text-xs text-slate-500">{titleCase(job.status)}</span></div>)}</div>:<p className="px-5 py-8 text-center text-sm text-slate-500">A processing job will appear once an authorized source video is uploaded.</p>}</section>
      <section className="rounded-2xl border border-slate-200 bg-white dark:border-white/10 dark:bg-[#11141d]"><div className="flex items-center gap-2 border-b border-slate-100 px-5 py-4 dark:border-white/5"><Film size={16} className="text-violet-600"/><h2 className="text-sm font-semibold">Generated clips <span className="ml-1 text-xs font-normal text-slate-500">({data.clip_count})</span></h2></div><p className="px-5 py-8 text-center text-sm text-slate-500">Clips will be available after the AI editing pipeline is added in Stage 3.</p></section>
    </div>
  );
}
