"use client";

import { useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import { FormEvent, useEffect, useState } from "react";
import { ApiError, apiRequest } from "@/services/api";

const input = "mt-1.5 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm dark:border-white/10 dark:bg-[#0c0f16]";

export function AccountSecuritySettings() {
  const router = useRouter();
  const { theme, setTheme } = useTheme();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [deletePassword, setDeletePassword] = useState("");
  const [prefs, setPrefs] = useState({ theme: "system", email_notifications: true, processing_notifications: true });
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    apiRequest<typeof prefs>("/auth/preferences").then(data => { if (active) { setPrefs(data); setTheme(data.theme); } }).catch(() => undefined);
    return () => { active = false; };
  }, [setTheme]);

  async function updatePassword(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(""); setMessage("");
    try { await apiRequest<void>("/auth/password/change", { method: "POST", body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }) }); setCurrentPassword(""); setNewPassword(""); setMessage("Password changed. Other sessions were revoked."); }
    catch (cause) { setError(cause instanceof ApiError ? cause.message : "Could not change password."); }
    finally { setBusy(false); }
  }

  async function savePrefs() {
    setBusy(true); setError(""); setMessage("");
    try { await apiRequest("/auth/preferences", { method: "PUT", body: JSON.stringify(prefs) }); setTheme(prefs.theme); setMessage("Preferences saved."); }
    catch (cause) { setError(cause instanceof ApiError ? cause.message : "Could not save preferences."); }
    finally { setBusy(false); }
  }

  async function deleteAccount(event: FormEvent) {
    event.preventDefault();
    if (!window.confirm("Permanently delete your account, projects, source media, clips and credit history? This cannot be undone.")) return;
    setBusy(true); setError("");
    try { await apiRequest<void>("/auth/account", { method: "DELETE", body: JSON.stringify({ password: deletePassword, confirmation: "DELETE" }) }); router.replace("/register"); router.refresh(); }
    catch (cause) { setError(cause instanceof ApiError ? cause.message : "Account deletion failed."); }
    finally { setBusy(false); }
  }

  return <div className="space-y-5">
    <section className="rounded-2xl border border-slate-200 bg-white p-6 dark:border-white/10 dark:bg-[#11141d]"><h2 className="text-sm font-semibold">Preferences &amp; notifications</h2><div className="mt-4 grid gap-3 sm:grid-cols-2"><label className="text-xs">Theme<select value={prefs.theme} onChange={event=>setPrefs({...prefs,theme:event.target.value})} className={input}><option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option></select></label><label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={prefs.email_notifications} onChange={event=>setPrefs({...prefs,email_notifications:event.target.checked})}/> Email notifications</label><label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={prefs.processing_notifications} onChange={event=>setPrefs({...prefs,processing_notifications:event.target.checked})}/> Processing notifications</label></div><button disabled={busy} onClick={()=>void savePrefs()} className="mt-4 rounded-lg bg-violet-600 px-3 py-2 text-xs font-semibold text-white disabled:opacity-50">Save preferences</button></section>
    <section className="rounded-2xl border border-slate-200 bg-white p-6 dark:border-white/10 dark:bg-[#11141d]"><h2 className="text-sm font-semibold">Change password</h2><form onSubmit={updatePassword} className="mt-4 grid gap-3 sm:grid-cols-2"><label className="text-xs">Current password<input type="password" autoComplete="current-password" required value={currentPassword} onChange={event=>setCurrentPassword(event.target.value)} className={input}/></label><label className="text-xs">New password<input type="password" autoComplete="new-password" minLength={12} maxLength={128} required value={newPassword} onChange={event=>setNewPassword(event.target.value)} className={input}/></label><button disabled={busy} className="w-fit rounded-lg bg-violet-600 px-3 py-2 text-xs font-semibold text-white disabled:opacity-50">Update password</button></form></section>
    <section className="rounded-2xl border border-rose-200 bg-white p-6 dark:border-rose-500/20 dark:bg-[#11141d]"><h2 className="text-sm font-semibold text-rose-700">Delete account and data</h2><p className="mt-1 text-xs text-slate-500">Projects, uploaded sources and exports will be removed. Credit and billing data is deleted from this application subject to provider/legal retention duties.</p><form onSubmit={deleteAccount} className="mt-4 flex flex-col gap-3 sm:flex-row"><label className="flex-1 text-xs">Confirm with your password<input type="password" required autoComplete="current-password" value={deletePassword} onChange={event=>setDeletePassword(event.target.value)} className={input}/></label><button disabled={busy} className="self-end rounded-lg border border-rose-300 px-3 py-2 text-xs font-semibold text-rose-700 disabled:opacity-50">Delete my account</button></form></section>
    {error&&<p role="alert" className="text-sm text-rose-600">{error}</p>}{message&&<p role="status" className="text-sm text-emerald-600">{message}</p>}
  </div>;
}
