"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, ArrowRight, Clapperboard, LoaderCircle, LockKeyhole } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { ApiError, apiRequest, type User } from "@/services/api";
import { ThemeToggle } from "@/components/theme-toggle";

type AuthMode = "login" | "register" | "forgot" | "reset";

export function AuthForm({ mode }: { mode: AuthMode }) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [token, setToken] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    setToken(query.get("token") ?? "");
  }, []);

  const titles: Record<AuthMode, string> = { login: "Welcome back", register: "Create your workspace", forgot: "Reset your password", reset: "Choose a new password" };
  const descriptions: Record<AuthMode, string> = {
    login: "Sign in to continue shaping your stories.", register: "Start organizing your video projects in one place.",
    forgot: "Enter your email and we’ll share next steps if an account exists.", reset: "Use a strong password you have not used before.",
  };

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(""); setMessage(""); setBusy(true);
    try {
      if (mode === "login") {
        await apiRequest<User>("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
        router.replace("/dashboard");
      } else if (mode === "register") {
        await apiRequest<User>("/auth/register", { method: "POST", body: JSON.stringify({ email, password, display_name: name }) });
        router.replace("/dashboard");
      } else if (mode === "forgot") {
        const result = await apiRequest<{ message: string; debug_token?: string | null }>("/auth/password-reset", { method: "POST", body: JSON.stringify({ email }) });
        setMessage(result.message);
        if (result.debug_token) {
          setMessage(`${result.message} Development reset token: ${result.debug_token}`);
          setToken(result.debug_token);
        }
      } else {
        await apiRequest<void>("/auth/password-reset/confirm", { method: "POST", body: JSON.stringify({ token, new_password: password }) });
        setMessage("Password updated. You can now sign in with your new password.");
      }
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Something went wrong. Please try again.");
    } finally { setBusy(false); }
  }

  return (
    <main className="relative grid min-h-screen place-items-center overflow-hidden bg-slate-50 px-5 py-12 dark:bg-[#080a0f]">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_50%_15%,rgba(124,58,237,.13),transparent_48%)]"/>
      <div className="absolute right-5 top-5"><ThemeToggle/></div>
      <div className="relative w-full max-w-md">
        <Link href="/" className="mx-auto mb-8 flex w-fit items-center gap-2.5 text-sm font-semibold"><span className="grid h-9 w-9 place-items-center rounded-xl bg-violet-600 text-white"><Clapperboard size={17}/></span>Frameforge</Link>
        <section className="rounded-3xl border border-slate-200 bg-white p-7 shadow-xl shadow-slate-900/5 dark:border-white/10 dark:bg-[#11141d] sm:p-9">
          <div className="grid h-11 w-11 place-items-center rounded-xl bg-violet-50 text-violet-600 dark:bg-violet-500/10 dark:text-violet-300"><LockKeyhole size={20}/></div>
          <h1 className="mt-6 text-2xl font-semibold tracking-tight">{titles[mode]}</h1><p className="mt-2 text-sm leading-6 text-slate-600 dark:text-slate-400">{descriptions[mode]}</p>
          <form onSubmit={submit} className="mt-7 space-y-4">
            {mode === "register" && <label className="block text-sm font-medium">Your name<input required maxLength={160} autoComplete="name" value={name} onChange={e=>setName(e.target.value)} className="mt-2 w-full rounded-xl border border-slate-200 bg-white px-3.5 py-3 outline-none transition focus:border-violet-500 focus:ring-2 focus:ring-violet-500/15 dark:border-white/10 dark:bg-[#0c0f16]"/></label>}
            {mode !== "reset" && <label className="block text-sm font-medium">Email address<input required type="email" autoComplete="email" value={email} onChange={e=>setEmail(e.target.value)} className="mt-2 w-full rounded-xl border border-slate-200 bg-white px-3.5 py-3 outline-none transition focus:border-violet-500 focus:ring-2 focus:ring-violet-500/15 dark:border-white/10 dark:bg-[#0c0f16]"/></label>}
            {mode === "reset" && <label className="block text-sm font-medium">Reset token<input required minLength={24} value={token} onChange={e=>setToken(e.target.value)} autoComplete="one-time-code" className="mt-2 w-full rounded-xl border border-slate-200 bg-white px-3.5 py-3 font-mono text-xs outline-none focus:border-violet-500 focus:ring-2 focus:ring-violet-500/15 dark:border-white/10 dark:bg-[#0c0f16]"/></label>}
            {(mode === "login" || mode === "register" || mode === "reset") && <label className="block text-sm font-medium">{mode === "reset" ? "New password" : "Password"}<input required type="password" minLength={mode === "login" ? 1 : 12} maxLength={128} autoComplete={mode === "login" ? "current-password" : "new-password"} value={password} onChange={e=>setPassword(e.target.value)} className="mt-2 w-full rounded-xl border border-slate-200 bg-white px-3.5 py-3 outline-none transition focus:border-violet-500 focus:ring-2 focus:ring-violet-500/15 dark:border-white/10 dark:bg-[#0c0f16]"/>{mode !== "login" && <span className="mt-1 block text-xs font-normal text-slate-500">At least 12 characters.</span>}</label>}
            {error && <p role="alert" className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2.5 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">{error}</p>}
            {message && <p role="status" className="break-words rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2.5 text-sm text-emerald-800 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-300">{message}</p>}
            {mode === "forgot" && token && <Link href={`/reset-password?token=${encodeURIComponent(token)}`} className="block text-sm font-semibold text-violet-600">Continue to set a new password</Link>}
            <button disabled={busy} className="flex w-full items-center justify-center gap-2 rounded-xl bg-violet-600 px-4 py-3.5 text-sm font-semibold text-white transition hover:bg-violet-500 disabled:cursor-wait disabled:opacity-60">{busy&&<LoaderCircle size={16} className="animate-spin"/>}{mode === "login" ? "Sign in" : mode === "register" ? "Create account" : mode === "forgot" ? "Send reset instructions" : "Update password"}<ArrowRight size={16}/></button>
          </form>
          <div className="mt-6 text-center text-sm text-slate-500">{mode === "login" ? <>New here? <Link href="/register" className="font-semibold text-violet-600 hover:text-violet-500">Create an account</Link></> : mode === "register" ? <>Already have an account? <Link href="/login" className="font-semibold text-violet-600">Sign in</Link></> : <Link href="/login" className="inline-flex items-center gap-1 font-semibold text-violet-600"><ArrowLeft size={14}/> Back to sign in</Link>}</div>
          {mode === "login" && <Link href="/forgot-password" className="mt-4 block text-center text-xs text-slate-500 hover:text-violet-600">Forgot password?</Link>}
        </section>
        <p className="mt-5 text-center text-xs text-slate-500">By continuing, you agree to use only video content you own or are authorized to process.</p>
      </div>
    </main>
  );
}
