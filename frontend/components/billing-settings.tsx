"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, apiRequest } from "@/services/api";

type Plan = { key: string; name: string; price_cents: number; currency: string; monthly_credits: number; max_video_seconds: number; max_clips: number; max_concurrent_jobs: number; export_width: number; export_height: number; watermark: boolean; priority: string; advanced_captions: boolean };
type Transaction = { id: string; transaction_type: string; amount: number; balance_after: number; description: string | null; created_at: string };
const date = (value: string) => new Date(value).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });

export function BillingSettings() {
  const queryClient = useQueryClient();
  const [working, setWorking] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const plans = useQuery({ queryKey: ["billing-plans"], queryFn: () => apiRequest<Plan[]>("/billing/plans") });
  const balance = useQuery({ queryKey: ["credits"], queryFn: () => apiRequest<{ available_credits: number; reserved_credits: number }>("/credits/balance") });
  const subscription = useQuery({ queryKey: ["subscription"], queryFn: () => apiRequest<{ plan_key: string; status: string; current_period_end: string | null; cancel_at_period_end: boolean }>("/billing/subscription") });
  const transactions = useQuery({ queryKey: ["credit-transactions"], queryFn: () => apiRequest<{ transactions: Transaction[]; has_more: boolean }>("/credits/transactions?limit=20") });
  const payments = useQuery({ queryKey: ["billing-history"], queryFn: () => apiRequest<{ payments: Array<{ id: string; amount: string; currency: string; status: string; invoice_url?: string; invoice_pdf?: string; created_at: string }> }>("/billing/history") });

  async function checkout(planKey: string) {
    if (current !== "free" && !window.confirm("Change your subscription now? Stripe may prorate the current billing period.")) return;
    setWorking(planKey); setError(""); setNotice("");
    try {
      if (current !== "free") {
        await apiRequest("/billing/change-plan", { method: "POST", body: JSON.stringify({ plan_key: planKey }) });
        await queryClient.invalidateQueries({ queryKey: ["subscription"] });
        setNotice("Plan change was submitted to Stripe. Subscription and credits update from verified webhooks.");
        return;
      }
      const result = await apiRequest<{ checkout_url: string }>("/billing/checkout", { method: "POST", body: JSON.stringify({ plan_key: planKey }) });
      const url = new URL(result.checkout_url);
      if (url.protocol !== "https:" || url.hostname !== "checkout.stripe.com") throw new Error("Checkout returned an invalid secure payment URL.");
      window.location.assign(url.toString());
    } catch (cause) { setError(cause instanceof ApiError ? cause.message : cause instanceof Error ? cause.message : "Could not start checkout."); }
    finally { setWorking(""); }
  }

  async function portal() {
    setWorking("portal"); setError("");
    try {
      const result = await apiRequest<{ portal_url: string }>("/billing/portal", { method: "POST" });
      const url = new URL(result.portal_url);
      if (url.protocol !== "https:" || url.hostname !== "billing.stripe.com") throw new Error("Billing portal returned an invalid secure URL.");
      window.location.assign(url.toString());
    } catch (cause) { setError(cause instanceof ApiError ? cause.message : cause instanceof Error ? cause.message : "Could not open the billing portal."); }
    finally { setWorking(""); }
  }

  async function cancel() {
    if (!window.confirm("Schedule this subscription to end at the current billing period?")) return;
    setWorking("cancel"); setError(""); setNotice("");
    try {
      await apiRequest("/billing/cancel", { method: "POST" });
      await queryClient.invalidateQueries({ queryKey: ["subscription"] });
      setNotice("Cancellation is scheduled for the end of the billing period.");
    } catch (cause) { setError(cause instanceof ApiError ? cause.message : "Could not cancel the subscription."); }
    finally { setWorking(""); }
  }

  const current = subscription.data?.plan_key ?? "free";
  return <div className="space-y-5">
    <section className="grid gap-3 sm:grid-cols-2"><article className="rounded-2xl border border-violet-200 bg-violet-50 p-5 dark:border-violet-500/20 dark:bg-violet-500/[.05]"><p className="text-xs text-slate-500">Available credits</p><p className="mt-2 text-3xl font-semibold">{balance.data?.available_credits ?? "—"}</p><p className="mt-1 text-xs text-slate-500">{balance.data?.reserved_credits ?? 0} reserved for active processing</p></article><article className="rounded-2xl border border-slate-200 bg-white p-5 dark:border-white/10 dark:bg-[#11141d]"><p className="text-xs text-slate-500">Current subscription</p><p className="mt-2 text-xl font-semibold">{subscription.data?.plan_key ?? "free"}</p><p className="mt-1 text-xs text-slate-500">Status: {subscription.data?.status ?? "loading"}{subscription.data?.current_period_end ? ` · Renews ${date(subscription.data.current_period_end)}` : ""}</p>{subscription.data?.cancel_at_period_end&&<p className="mt-2 text-xs text-amber-600">Scheduled to cancel at period end</p>}</article></section>
    {error&&<p role="alert" className="rounded-lg bg-rose-50 p-3 text-sm text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">{error}</p>}{notice&&<p role="status" className="rounded-lg bg-emerald-50 p-3 text-sm text-emerald-700">{notice}</p>}
    <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">{plans.data?.map(plan=><article key={plan.key} className={`flex flex-col rounded-2xl border bg-white p-5 dark:bg-[#11141d] ${plan.key===current?"border-violet-400 ring-1 ring-violet-400":"border-slate-200 dark:border-white/10"}`}><h3 className="text-lg font-semibold">{plan.name}</h3><p className="mt-1 text-2xl font-bold">{plan.price_cents?new Intl.NumberFormat(undefined,{style:"currency",currency:plan.currency.toUpperCase()}).format(plan.price_cents/100):"Free"}<span className="text-xs font-normal text-slate-500">{plan.price_cents?" / month":""}</span></p><ul className="mt-4 flex-1 space-y-2 text-xs text-slate-500"><li>{plan.monthly_credits.toLocaleString()} monthly credits</li><li>Videos up to {Math.floor(plan.max_video_seconds/60)} min · {plan.max_clips} clips/job</li><li>{plan.export_width}×{plan.export_height} exports{plan.watermark?" · watermark":" · no watermark"}</li><li>{plan.max_concurrent_jobs} concurrent jobs · {plan.priority} queue</li><li>{plan.advanced_captions?"Advanced captions":"Standard captions"}</li></ul>{plan.key!==current&&plan.key!=="free"&&<button disabled={!!working} onClick={()=>void checkout(plan.key)} className="mt-5 rounded-lg bg-violet-600 px-3 py-2.5 text-xs font-semibold text-white disabled:opacity-50">{working===plan.key?"Opening checkout…":current==="free"?"Choose plan":"Change plan"}</button>}{plan.key===current&&<span className="mt-5 rounded-lg bg-slate-100 px-3 py-2.5 text-center text-xs font-semibold text-slate-500 dark:bg-white/5">Current plan</span>}</article>)}</section>
    {current!=="free"&&<div className="flex flex-wrap gap-2"><button disabled={!!working} onClick={()=>void portal()} className="rounded-lg border border-slate-200 px-3 py-2 text-xs font-medium dark:border-white/10">{working==="portal"?"Opening…":"Manage billing & invoices"}</button><button disabled={!!working||Boolean(subscription.data?.cancel_at_period_end)} onClick={()=>void cancel()} className="rounded-lg border border-rose-200 px-3 py-2 text-xs text-rose-600 disabled:opacity-50 dark:border-rose-500/20">{working==="cancel"?"Scheduling…":"Cancel plan"}</button></div>}
    <section className="rounded-2xl border border-slate-200 bg-white p-5 dark:border-white/10 dark:bg-[#11141d]"><h3 className="text-sm font-semibold">Credit history</h3>{transactions.data?.transactions.length?<div className="mt-3 divide-y divide-slate-100 dark:divide-white/5">{transactions.data.transactions.map(item=><div key={item.id} className="flex flex-wrap items-center gap-2 py-3 text-xs"><span className="min-w-28 capitalize text-slate-500">{item.transaction_type.replaceAll("_"," ")}</span><span className={item.amount<0?"font-semibold text-rose-600":"font-semibold text-emerald-600"}>{item.amount>0?"+":""}{item.amount}</span><span className="min-w-0 flex-1 text-slate-500">{item.description}</span><time className="text-slate-400">{date(item.created_at)}</time></div>)}</div>:<p className="mt-3 text-xs text-slate-500">No credit transactions yet.</p>}</section>
    <section className="rounded-2xl border border-slate-200 bg-white p-5 dark:border-white/10 dark:bg-[#11141d]"><h3 className="text-sm font-semibold">Billing history</h3>{payments.data?.payments.length?<ul className="mt-3 space-y-2">{payments.data.payments.map(item=><li key={item.id} className="flex flex-wrap items-center gap-3 text-xs"><span>{new Intl.NumberFormat(undefined,{style:"currency",currency:item.currency}).format(Number(item.amount))}</span><span className="text-slate-500">{item.status} · {date(item.created_at)}</span>{item.invoice_pdf&&<a className="ml-auto text-violet-600" href={item.invoice_pdf} target="_blank" rel="noreferrer">Invoice PDF</a>}{item.invoice_url&&<a className="text-violet-600" href={item.invoice_url} target="_blank" rel="noreferrer">View invoice</a>}</li>)}</ul>:<p className="mt-3 text-xs text-slate-500">No invoices yet.</p>}</section>
  </div>;
}
