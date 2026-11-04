"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Clapperboard, FileVideo2, LayoutDashboard, LogOut, Menu, Settings2, Shield, Upload, X } from "lucide-react";
import { ReactNode, useEffect, useState } from "react";
import { ApiError, apiRequest, type User } from "@/services/api";
import { ThemeToggle } from "@/components/theme-toggle";

const navigation = [
  { href: "/dashboard", label: "Overview", icon: LayoutDashboard },
  { href: "/dashboard/upload", label: "Upload video", icon: Upload },
  { href: "/dashboard/settings", label: "Account settings", icon: Settings2 },
];
const adminNavigation = { href: "/dashboard/admin", label: "Administration", icon: Shield };

export function DashboardShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(true);
  const [menuOpen, setMenuOpen] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const visibleNavigation = user?.is_admin ? [...navigation, adminNavigation] : navigation;

  useEffect(() => {
    let active = true;
    const expire = () => router.replace("/login");
    window.addEventListener("auth:expired", expire);
    apiRequest<User>("/auth/me").then(value => { if (active) setUser(value); }).catch(() => { if (active) router.replace("/login"); }).finally(() => { if (active) setChecking(false); });
    return () => { active = false; window.removeEventListener("auth:expired", expire); };
  }, [router]);

  async function logout() {
    setSigningOut(true);
    try { await apiRequest<void>("/auth/logout", { method: "POST" }); }
    catch { /* locally discard the workspace view even if the server is unreachable */ }
    finally { router.replace("/login"); router.refresh(); }
  }

  if (checking || !user) return <main className="grid min-h-screen place-items-center bg-slate-50 dark:bg-[#080a0f]"><div className="flex items-center gap-3 text-sm text-slate-500"><span className="h-5 w-5 animate-spin rounded-full border-2 border-violet-600 border-t-transparent"/>Checking your secure session…</div></main>;

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900 dark:bg-[#080a0f] dark:text-slate-100">
      <aside className="fixed inset-y-0 left-0 z-40 hidden w-[248px] flex-col border-r border-slate-200 bg-white dark:border-white/10 dark:bg-[#0c0f16] lg:flex">
        <Link href="/dashboard" className="flex h-[76px] items-center gap-3 border-b border-slate-100 px-6 dark:border-white/5"><span className="grid h-9 w-9 place-items-center rounded-xl bg-violet-600 text-white"><Clapperboard size={17}/></span><span className="font-semibold tracking-tight">Frameforge</span></Link>
        <p className="px-6 pb-2 pt-7 text-[10px] font-semibold uppercase tracking-[.18em] text-slate-400">Workspace</p>
        <nav className="space-y-1 px-3">{visibleNavigation.map(({href,label,icon:Icon})=><Link key={href} href={href} className={`flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition ${pathname===href || (href!=="/dashboard"&&pathname.startsWith(href)) ? "bg-violet-50 font-semibold text-violet-700 dark:bg-violet-500/10 dark:text-violet-300" : "text-slate-600 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-white/5"}`}><Icon size={17}/>{label}</Link>)}</nav>
        <div className="mt-auto border-t border-slate-100 p-4 dark:border-white/5"><div className="flex items-center gap-3 rounded-xl px-2 py-2"><span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-violet-100 text-xs font-bold text-violet-700 dark:bg-violet-500/15 dark:text-violet-200">{(user.display_name||user.email).slice(0,1).toUpperCase()}</span><div className="min-w-0 flex-1"><p className="truncate text-sm font-medium">{user.display_name||"Creator"}</p><p className="truncate text-xs text-slate-500">{user.email}</p></div></div><button onClick={logout} disabled={signingOut} className="mt-2 flex w-full items-center gap-2 rounded-lg px-3 py-2 text-xs text-slate-500 hover:bg-slate-100 hover:text-slate-900 dark:hover:bg-white/5 dark:hover:text-white"><LogOut size={15}/>{signingOut?"Signing out…":"Sign out"}</button></div>
      </aside>
      {menuOpen && <button aria-label="Close navigation" onClick={()=>setMenuOpen(false)} className="fixed inset-0 z-40 bg-black/40 lg:hidden"/>}
      <aside className={`fixed inset-y-0 left-0 z-50 flex w-[270px] flex-col border-r border-slate-200 bg-white transition-transform dark:border-white/10 dark:bg-[#0c0f16] lg:hidden ${menuOpen?"translate-x-0":"-translate-x-full"}`}>
        <div className="flex h-[72px] items-center justify-between border-b border-slate-100 px-5 dark:border-white/5"><Link href="/dashboard" onClick={()=>setMenuOpen(false)} className="flex items-center gap-2.5 font-semibold"><span className="grid h-9 w-9 place-items-center rounded-xl bg-violet-600 text-white"><Clapperboard size={16}/></span>Frameforge</Link><button aria-label="Close navigation" onClick={()=>setMenuOpen(false)} className="grid h-9 w-9 place-items-center rounded-lg hover:bg-slate-100 dark:hover:bg-white/5"><X size={18}/></button></div>
        <nav className="space-y-1 p-3">{visibleNavigation.map(({href,label,icon:Icon})=><Link key={href} href={href} onClick={()=>setMenuOpen(false)} className={`flex items-center gap-3 rounded-xl px-3 py-3 text-sm ${pathname===href?"bg-violet-50 font-semibold text-violet-700 dark:bg-violet-500/10 dark:text-violet-300":"text-slate-600 dark:text-slate-400"}`}><Icon size={17}/>{label}</Link>)}</nav>
        <div className="mt-auto border-t border-slate-100 p-4 dark:border-white/5"><p className="truncate text-sm font-medium">{user.display_name||"Creator"}</p><p className="truncate text-xs text-slate-500">{user.email}</p><button onClick={logout} className="mt-4 flex items-center gap-2 text-sm text-slate-500"><LogOut size={15}/>Sign out</button></div>
      </aside>
      <div className="lg:pl-[248px]">
        <header className="sticky top-0 z-30 flex h-[72px] items-center justify-between border-b border-slate-200 bg-white/90 px-4 backdrop-blur-xl dark:border-white/5 dark:bg-[#080a0f]/85 sm:px-7">
          <div className="flex items-center gap-3"><button aria-label="Open navigation" onClick={()=>setMenuOpen(true)} className="grid h-10 w-10 place-items-center rounded-xl border border-slate-200 lg:hidden dark:border-white/10"><Menu size={18}/></button><div className="hidden items-center gap-2 text-xs text-slate-400 sm:flex"><FileVideo2 size={15}/><span>Creator workspace</span></div></div>
          <div className="flex items-center gap-3"><span className="hidden text-xs text-slate-500 sm:block">{user.display_name||user.email}</span><ThemeToggle/><Link href="/dashboard/upload" className="inline-flex items-center gap-2 rounded-xl bg-violet-600 px-3.5 py-2.5 text-xs font-semibold text-white transition hover:bg-violet-500 sm:text-sm"><Upload size={15}/>New upload</Link></div>
        </header>
        <main className="mx-auto max-w-7xl px-4 py-7 sm:px-7 sm:py-9">{children}</main>
      </div>
    </div>
  );
}
