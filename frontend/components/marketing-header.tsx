"use client";

import Link from "next/link";
import { Clapperboard, Menu, X } from "lucide-react";
import { useState } from "react";
import { ThemeToggle } from "@/components/theme-toggle";

const links = [["Product", "#product"], ["How it works", "#how-it-works"], ["Pricing", "#pricing"], ["FAQ", "#faq"]];

export function MarketingHeader() {
  const [open, setOpen] = useState(false);
  return (
    <header className="sticky top-0 z-30 border-b border-slate-200/70 bg-white/85 backdrop-blur-xl dark:border-white/5 dark:bg-[#080a0f]/85">
      <div className="mx-auto flex h-[72px] max-w-7xl items-center justify-between px-5 sm:px-8">
        <Link href="/" className="flex items-center gap-2.5 font-semibold tracking-tight"><span className="grid h-9 w-9 place-items-center rounded-xl bg-violet-600 text-white"><Clapperboard size={17} /></span><span>Frameforge</span></Link>
        <nav className="hidden items-center gap-8 lg:flex">{links.map(([name, href]) => <a key={name} href={href} className="text-sm text-slate-600 transition hover:text-violet-600 dark:text-slate-400 dark:hover:text-white">{name}</a>)}</nav>
        <div className="hidden items-center gap-3 sm:flex"><ThemeToggle /><Link href="/login" className="px-3 py-2 text-sm font-medium text-slate-700 hover:text-violet-600 dark:text-slate-300">Log in</Link><Link href="/register" className="rounded-xl bg-violet-600 px-4 py-2.5 text-sm font-semibold text-white shadow-lg shadow-violet-600/20 transition hover:bg-violet-500">Get started</Link></div>
        <button type="button" aria-label={open ? "Close menu" : "Open menu"} onClick={() => setOpen(!open)} className="grid h-10 w-10 place-items-center rounded-xl border border-slate-200 lg:hidden dark:border-white/10">{open ? <X size={18} /> : <Menu size={18} />}</button>
      </div>
      {open && <div className="border-t border-slate-200 px-5 py-4 lg:hidden dark:border-white/10"><nav className="flex flex-col gap-1">{links.map(([name, href]) => <a key={name} href={href} onClick={() => setOpen(false)} className="rounded-lg px-3 py-2.5 text-sm text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-white/5">{name}</a>)}<div className="mt-3 flex items-center gap-3"><ThemeToggle /><Link href="/login" className="px-3 py-2 text-sm">Log in</Link><Link href="/register" className="rounded-lg bg-violet-600 px-4 py-2.5 text-sm font-semibold text-white">Get started</Link></div></nav></div>}
    </header>
  );
}
