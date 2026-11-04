import { ArrowUpRight, Clapperboard, Film, Sparkles } from "lucide-react";

export default function HomePage() {
  return (
    <main className="min-h-screen overflow-hidden bg-[#080a0f]">
      <nav className="mx-auto flex max-w-7xl items-center justify-between px-6 py-7 lg:px-10">
        <a href="/" className="flex items-center gap-3 text-sm font-semibold tracking-wide"><span className="grid h-9 w-9 place-items-center rounded-xl bg-violet-500 text-white"><Clapperboard size={18} /></span>FRAMEFORGE</a>
        <span className="rounded-full border border-white/10 px-3 py-1.5 text-xs text-slate-400">Stage 1 · Foundation</span>
      </nav>
      <section className="mx-auto grid max-w-7xl items-center gap-14 px-6 pb-24 pt-20 lg:grid-cols-[1.15fr_.85fr] lg:px-10 lg:pt-28">
        <div>
          <div className="mb-7 inline-flex items-center gap-2 rounded-full border border-violet-400/20 bg-violet-400/5 px-3 py-1.5 text-xs text-violet-200"><Sparkles size={14} /> A new way to shape your story</div>
          <h1 className="max-w-3xl text-5xl font-semibold leading-[1.08] tracking-tight sm:text-6xl lg:text-7xl">One video.<br /><span className="text-violet-400">More moments.</span></h1>
          <p className="mt-7 max-w-xl text-lg leading-8 text-slate-400">An AI-powered foundation for turning long-form video into short-form clips made to travel.</p>
          <div className="mt-10 flex flex-wrap items-center gap-4"><a href="mailto:hello@example.com" className="inline-flex items-center gap-2 rounded-xl bg-violet-500 px-5 py-3 text-sm font-semibold text-white transition hover:bg-violet-400">Get early access <ArrowUpRight size={17} /></a><span className="text-sm text-slate-500">The platform is taking shape.</span></div>
        </div>
        <div className="relative mx-auto w-full max-w-lg">
          <div className="absolute -inset-10 rounded-full bg-violet-500/10 blur-3xl" />
          <div className="relative rounded-3xl border border-white/10 bg-[#11141d] p-5 shadow-2xl shadow-black/40">
            <div className="flex items-center justify-between border-b border-white/5 pb-4"><div><p className="text-xs text-slate-500">WORKSPACE / OVERVIEW</p><p className="mt-1 text-sm font-medium">Your next great clip</p></div><span className="h-2 w-2 rounded-full bg-emerald-400" /></div>
            <div className="mt-5 grid grid-cols-[1fr_92px] gap-4">
              <div className="flex aspect-video items-center justify-center rounded-2xl border border-white/5 bg-gradient-to-br from-slate-800 via-[#252033] to-violet-950"><div className="grid h-12 w-12 place-items-center rounded-full bg-white/10 text-white"><Film size={20} /></div></div>
              <div className="flex aspect-[9/16] items-center justify-center rounded-2xl border border-violet-400/20 bg-gradient-to-b from-violet-950 via-[#211d37] to-slate-900"><div className="px-2 text-center"><span className="mb-2 block text-[9px] uppercase tracking-[.2em] text-violet-300">Highlight</span><span className="text-[10px] font-semibold leading-4">A moment<br />worth sharing</span></div></div>
            </div>
            <div className="mt-5 flex items-center gap-3"><span className="h-1.5 flex-1 rounded-full bg-white/10"><span className="block h-1.5 w-2/5 rounded-full bg-violet-400" /></span><span className="text-[10px] text-slate-500">Preview concept</span></div>
          </div>
          <p className="relative mt-4 text-center text-xs text-slate-600">A glimpse of what we’re building</p>
        </div>
      </section>
    </main>
  );
}
