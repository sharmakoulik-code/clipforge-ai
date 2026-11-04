"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, LoaderCircle, UserRound } from "lucide-react";
import { FormEvent, useState } from "react";
import { ApiError, apiRequest, type User } from "@/services/api";
import { BillingSettings } from "@/components/billing-settings";
import { AccountSecuritySettings } from "@/components/account-settings";

export default function SettingsPage() {
  const queryClient = useQueryClient();
  const { data: user, isLoading } = useQuery({ queryKey: ["me"], queryFn: () => apiRequest<User>("/auth/me") });
  const [name, setName] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  async function save(event: FormEvent) {
    event.preventDefault(); setSaving(true); setError(""); setMessage("");
    try { await apiRequest<User>("/auth/profile", { method: "PATCH", body: JSON.stringify({ display_name: name }) }); await queryClient.invalidateQueries({ queryKey: ["me"] }); setMessage("Your profile was updated."); }
    catch (cause) { setError(cause instanceof ApiError ? cause.message : "Could not update your profile."); }
    finally { setSaving(false); }
  }
  if (isLoading) return <div className="h-64 animate-pulse rounded-2xl bg-slate-200 dark:bg-white/10"/>;
  return <div className="mx-auto max-w-7xl space-y-7"><div><p className="text-xs font-semibold uppercase tracking-[.18em] text-violet-600">Workspace</p><h1 className="mt-2 text-3xl font-semibold tracking-tight">Account settings</h1><p className="mt-2 text-sm text-slate-500">Manage your profile and sign-in details.</p></div>
    <section className="rounded-2xl border border-slate-200 bg-white p-6 dark:border-white/10 dark:bg-[#11141d]"><div className="flex items-center gap-3"><span className="grid h-10 w-10 place-items-center rounded-xl bg-violet-50 text-violet-600 dark:bg-violet-500/10 dark:text-violet-300"><UserRound size={18}/></span><div><h2 className="text-sm font-semibold">Profile</h2><p className="mt-1 text-xs text-slate-500">How your name appears in the workspace</p></div></div><form onSubmit={save} className="mt-6 space-y-4"><label className="block text-sm font-medium">Display name<input required maxLength={160} value={name||user?.display_name||""} onChange={e=>setName(e.target.value)} className="mt-2 w-full max-w-md rounded-xl border border-slate-200 bg-white px-3.5 py-3 outline-none focus:border-violet-500 dark:border-white/10 dark:bg-[#0c0f16]"/></label><label className="block text-sm font-medium">Email address<input readOnly value={user?.email??""} className="mt-2 w-full max-w-md rounded-xl border border-slate-200 bg-slate-50 px-3.5 py-3 text-slate-500 dark:border-white/10 dark:bg-white/[.03]"/></label>{error&&<p role="alert" className="text-sm text-rose-600">{error}</p>}{message&&<p role="status" className="flex items-center gap-2 text-sm text-emerald-600"><Check size={16}/>{message}</p>}<button disabled={saving} className="inline-flex items-center gap-2 rounded-xl bg-violet-600 px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50">{saving&&<LoaderCircle size={15} className="animate-spin"/>}Save profile</button></form></section>
    <section><div className="mb-3"><h2 className="text-lg font-semibold">Subscription, billing &amp; credits</h2><p className="mt-1 text-sm text-slate-500">Plan limits and prices are delivered from the server catalog.</p></div><BillingSettings/></section>
    <AccountSecuritySettings/>
  </div>;
}
