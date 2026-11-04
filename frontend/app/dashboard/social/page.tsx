"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { apiRequest, ApiError, type Project } from "@/services/api";

type Provider = { slug: string; name: string; oauth_supported: boolean; publishing_supported: boolean; connect_available: boolean; approval_required: boolean; availability_note: string };
type Account = { id: string; provider: string; provider_name: string; external_account_id: string; display_name: string; username: string | null; status: string; connected_at: string; metadata: Record<string, unknown> };
type Clip = { id: string; project_id: string; title: string; video_url: string | null; status: string; duration_seconds: number; transcript: string; description: string; category: string; hook: string; reason: string };
type Metadata = { title: string; caption: string; description: string; hashtags: string[]; keywords?: string[]; call_to_action?: string; thumbnail_text_suggestions?: string[] };
type Target = { id: string; account_id: string | null; account_name: string; provider: string; provider_name: string; status: string; metadata: Metadata; platform_options: { privacy_status?: string; made_for_kids?: boolean | null }; scheduled_at: string | null; schedule_timezone: string | null; external_post_url: string | null; last_error_code: string | null; last_error_message: string | null; published_at: string | null };
type Post = { id: string; clip_id: string | null; title: string; status: string; timezone: string; created_at: string; targets: Target[] };
type CalendarItem = { target_id: string; post_id: string; clip_id: string | null; title: string; provider: string; provider_name: string; account_name: string; status: string; schedule_status: string; scheduled_at_utc: string; timezone: string; metadata: Metadata };
type View = "accounts" | "compose" | "bulk" | "calendar" | "queue";
type BulkDraft = { scheduledAt: string; metadata: Record<string, Metadata>; options: Record<string, { privacy_status: string; made_for_kids: boolean | null }> };

const section = "rounded-2xl border border-slate-200 bg-white p-5 dark:border-white/10 dark:bg-[#11141d]";
const input = "mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-violet-500 dark:border-white/10 dark:bg-[#0c0f16]";
const button = "rounded-lg bg-violet-600 px-3.5 py-2.5 text-xs font-semibold text-white transition hover:bg-violet-500 disabled:cursor-not-allowed disabled:opacity-45";
const secondary = "rounded-lg border border-slate-200 px-3 py-2 text-xs font-medium hover:bg-slate-50 disabled:opacity-45 dark:border-white/10 dark:hover:bg-white/5";

function localDateTime(date: Date) {
  const adjusted = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return adjusted.toISOString().slice(0, 16);
}
function defaultSchedule() { return localDateTime(new Date(Date.now() + 60 * 60 * 1000)); }
function errorText(error: unknown) { return error instanceof Error ? error.message : "Something went wrong. Try again."; }
function splitTags(value: string) { return value.split(/[\s,]+/).map(tag => tag.trim()).filter(Boolean).map(tag => tag.startsWith("#") ? tag : `#${tag}`); }
function blankMetadata(title: string): Metadata { return { title, caption: "", description: "", hashtags: [], keywords: [] }; }

export default function SocialPublisherPage() {
  const [view, setView] = useState<View>("accounts");
  const [providers, setProviders] = useState<Provider[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [posts, setPosts] = useState<Post[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [clips, setClips] = useState<Clip[]>([]);
  const [calendarItems, setCalendarItems] = useState<CalendarItem[]>([]);
  const [calendarDate, setCalendarDate] = useState(new Date());
  const [calendarMode, setCalendarMode] = useState<"month" | "week" | "list">("month");
  const [selectedClip, setSelectedClip] = useState("");
  const [selectedAccounts, setSelectedAccounts] = useState<string[]>([]);
  const [post, setPost] = useState<Post | null>(null);
  const [metadata, setMetadata] = useState<Record<string, Metadata>>({});
  const [options, setOptions] = useState<Record<string, { privacy_status?: string; made_for_kids?: boolean | null }>>({});
  const [audience, setAudience] = useState("");
  const [language, setLanguage] = useState("English");
  const [tone, setTone] = useState("clear and engaging");
  const [cta, setCta] = useState("");
  const [scheduleAt, setScheduleAt] = useState(defaultSchedule());
  const [bulkClipIds, setBulkClipIds] = useState<string[]>([]);
  const [bulkAccounts, setBulkAccounts] = useState<string[]>([]);
  const [bulkDrafts, setBulkDrafts] = useState<Record<string, BulkDraft>>({});
  const [bulkStep, setBulkStep] = useState<0 | 1>(0);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const timezone = useMemo(() => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC", []);

  const refresh = useCallback(async () => {
    const [providerResult, accountResult, postResult, projectResult] = await Promise.all([
      apiRequest<{ providers: Provider[] }>("/social/providers"),
      apiRequest<{ accounts: Account[] }>("/social/accounts"),
      apiRequest<{ posts: Post[] }>("/social/posts"),
      apiRequest<Project[]>("/projects"),
    ]);
    setProviders(providerResult.providers);
    setAccounts(accountResult.accounts);
    setPosts(postResult.posts);
    setProjects(projectResult);
    const result = await Promise.all(projectResult.map(project =>
      apiRequest<{ clips: Clip[] }>(`/projects/${project.id}/clips?sort=newest`).catch(() => ({ clips: [] }))
    ));
    setClips(result.flatMap(item => item.clips).filter(clip => clip.status === "completed" && !!clip.video_url));
  }, []);

  useEffect(() => {
    let active = true;
    void refresh().catch(cause => { if (active) setError(errorText(cause)); });
    const connection = new URLSearchParams(window.location.search).get("connection");
    if (connection) {
      const messages: Record<string, string> = {
        youtube_connected: "YouTube account connected. Publishing is enabled only for this official adapter.",
        cancelled: "Google authorization was cancelled.", invalid_state: "The OAuth state was invalid or expired. Start the connection again.",
        account_limit: "Your plan's connected-account limit was reached.", no_accounts: "Google returned no YouTube channels for this account.",
        oauth_failed: "Google connection failed. Check OAuth configuration and account eligibility.", server_not_configured: "Google OAuth server settings are incomplete.",
      };
      setNotice(messages[connection] || "Connection status updated.");
      window.history.replaceState({}, "", "/dashboard/social");
    }
    return () => { active = false; };
  }, [refresh]);

  useEffect(() => {
    if (view !== "calendar") return;
    let start: Date;
    let end: Date;
    if (calendarMode === "week") {
      start = new Date(calendarDate); start.setHours(0, 0, 0, 0);
      const weekday = (start.getDay() + 6) % 7; start.setDate(start.getDate() - weekday);
      end = new Date(start); end.setDate(end.getDate() + 7);
    } else if (calendarMode === "list") {
      start = new Date(); start.setHours(0, 0, 0, 0);
      end = new Date(start); end.setDate(end.getDate() + 30);
    } else {
      start = new Date(calendarDate.getFullYear(), calendarDate.getMonth(), 1);
      start.setDate(start.getDate() - ((start.getDay() + 6) % 7));
      end = new Date(calendarDate.getFullYear(), calendarDate.getMonth() + 1, 1);
      end.setDate(end.getDate() + (7 - ((end.getDay() + 6) % 7)) % 7);
    }
    const params = new URLSearchParams({ start: start.toISOString(), end: end.toISOString(), timezone });
    apiRequest<{ items: CalendarItem[] }>(`/social/calendar?${params}`).then(result => setCalendarItems(result.items)).catch(cause => setError(errorText(cause)));
  }, [view, calendarDate, calendarMode, timezone]);

  const completedAccounts = accounts.filter(account => account.status === "connected");
  const availableClips = clips;
  const chosenClip = clips.find(clip => clip.id === selectedClip);
  const youtube = providers.find(provider => provider.slug === "youtube");
  const tabs: Array<[View, string]> = [["accounts", "Connected accounts"], ["compose", "Composer"], ["bulk", "Bulk schedule"], ["calendar", "Calendar"], ["queue", "Queue & history"]];

  function toggleSelection(selected: string[], setter: (value: string[]) => void, id: string) {
    setter(selected.includes(id) ? selected.filter(value => value !== id) : [...selected, id]);
  }

  async function connectYoutube() {
    setError(""); setBusy("connect");
    try {
      const result = await apiRequest<{ authorization_url: string }>("/social/accounts/youtube/oauth/start", { method: "POST", body: "{}" });
      window.location.assign(result.authorization_url);
    } catch (cause) { setError(errorText(cause)); setBusy(""); }
  }

  async function disconnect(account: Account) {
    if (!window.confirm(`Disconnect ${account.display_name}? This removes stored credentials from ClipForge.`)) return;
    setBusy(account.id); setError("");
    try { await apiRequest(`/social/accounts/${account.id}`, { method: "DELETE" }); setNotice("Account disconnected."); await refresh(); }
    catch (cause) { setError(errorText(cause)); }
    finally { setBusy(""); }
  }

  async function createDraft() {
    if (!selectedClip || !selectedAccounts.length) { setError("Choose a rendered clip and at least one connected account."); return; }
    setBusy("draft"); setError("");
    try {
      const created = await apiRequest<Post>("/social/drafts", { method: "POST", body: JSON.stringify({ clip_id: selectedClip, account_ids: selectedAccounts, idempotency_key: crypto.randomUUID(), timezone }) });
      setPost(created);
      const values: Record<string, Metadata> = {};
      const optionsValue: Record<string, { privacy_status?: string; made_for_kids?: boolean | null }> = {};
      created.targets.forEach(target => { values[target.id] = target.metadata || blankMetadata(created.title); optionsValue[target.id] = target.platform_options || { privacy_status: "private", made_for_kids: null }; });
      setMetadata(values); setOptions(optionsValue); setNotice("Draft created. Generate platform-specific copy or edit it directly."); await refresh();
    } catch (cause) { setError(errorText(cause)); }
    finally { setBusy(""); }
  }

  async function generateMetadata() {
    if (!post) return;
    setBusy("generate"); setError("");
    try {
      const result = await apiRequest<{ metadata_by_target: Record<string, Metadata>; errors: Array<{ target_id: string; message: string }>; charged_credits: number }>(
        `/social/posts/${post.id}/metadata/generate`, { method: "POST", body: JSON.stringify({ idempotency_key: crypto.randomUUID(), audience, language, tone, call_to_action: cta }) });
      setMetadata(previous => ({ ...previous, ...result.metadata_by_target }));
      setNotice(`Generated platform-specific drafts (${result.charged_credits} credits). Review every field before publishing.`);
      if (result.errors.length) setError(result.errors.map(item => item.message).join(" · "));
      await refresh();
    } catch (cause) { setError(errorText(cause)); }
    finally { setBusy(""); }
  }

  async function saveTarget(target: Target) {
    const current = metadata[target.id] || blankMetadata(post?.title || "");
    const platformOptions = options[target.id] || { privacy_status: "private", made_for_kids: null };
    const saved = await apiRequest<{ metadata: Metadata; platform_options: Target["platform_options"] }>(`/social/targets/${target.id}/metadata`, {
      method: "PATCH", body: JSON.stringify({ metadata: current, platform_options: platformOptions }),
    });
    setMetadata(previous => ({ ...previous, [target.id]: saved.metadata }));
    setOptions(previous => ({ ...previous, [target.id]: saved.platform_options }));
  }

  async function saveAllTargets() {
    if (!post) return;
    for (const target of post.targets) await saveTarget(target);
  }

  async function publishNow() {
    if (!post) return;
    setBusy("publish"); setError("");
    try { await saveAllTargets(); const updated = await apiRequest<Post>(`/social/posts/${post.id}/publish`, { method: "POST", body: "{}" }); setPost(updated); setNotice("Publishing queued. Each account is tracked independently."); await refresh(); }
    catch (cause) { setError(errorText(cause)); }
    finally { setBusy(""); }
  }

  async function schedulePost() {
    if (!post || !scheduleAt) return;
    setBusy("schedule"); setError("");
    try {
      await saveAllTargets();
      const updated = await apiRequest<Post>(`/social/posts/${post.id}/schedule`, { method: "POST", body: JSON.stringify({ scheduled_at: scheduleAt, timezone }) });
      setPost(updated); setNotice("Scheduled successfully. The worker publishes while you are offline."); await refresh();
    } catch (cause) { setError(errorText(cause)); }
    finally { setBusy(""); }
  }

  function toggleBulkClip(clip: Clip) {
    const already = bulkClipIds.includes(clip.id);
    setBulkClipIds(current => already ? current.filter(id => id !== clip.id) : [...current, clip.id]);
    if (!already) setBulkDrafts(current => ({ ...current, [clip.id]: {
      scheduledAt: defaultSchedule(),
      metadata: Object.fromEntries(bulkAccounts.map(id => [id, blankMetadata(clip.title)])),
      options: Object.fromEntries(bulkAccounts.map(id => [id, { privacy_status: "private", made_for_kids: null } as const])),
    } }));
  }

  function toggleBulkAccount(id: string) {
    const adding = !bulkAccounts.includes(id);
    setBulkAccounts(current => adding ? [...current, id] : current.filter(value => value !== id));
    if (adding) setBulkDrafts(current => Object.fromEntries(Object.entries(current).map(([clipId, draft]) => {
      const clip = clips.find(item => item.id === clipId);
      return [clipId, { ...draft, metadata: { ...draft.metadata, [id]: blankMetadata(clip?.title || "") }, options: { ...draft.options, [id]: { privacy_status: "private", made_for_kids: null } } }];
    })));
    else setBulkDrafts(current => Object.fromEntries(Object.entries(current).map(([clipId, draft]) => {
      const metadataCopy = { ...draft.metadata }, optionsCopy = { ...draft.options }; delete metadataCopy[id]; delete optionsCopy[id];
      return [clipId, { ...draft, metadata: metadataCopy, options: optionsCopy }];
    })));
  }

  async function confirmBulkSchedule() {
    if (!bulkClipIds.length || !bulkAccounts.length) { setError("Choose multiple clips and one or more connected accounts."); return; }
    setBusy("bulk"); setError("");
    try {
      const items = bulkClipIds.map(clipId => {
        const draft = bulkDrafts[clipId];
        return { clip_id: clipId, account_ids: bulkAccounts, idempotency_key: crypto.randomUUID(), scheduled_at: draft.scheduledAt, timezone,
          metadata_by_account: Object.fromEntries(bulkAccounts.map(id => [id, { ...draft.metadata[id], hashtags: draft.metadata[id]?.hashtags || [] }])),
          options_by_account: draft.options };
      });
      const result = await apiRequest<{ scheduled_targets: number }>("/social/posts/bulk-schedule", { method: "POST", body: JSON.stringify({ items }) });
      setNotice(`${items.length} posts / ${result.scheduled_targets} account targets scheduled.`);
      setBulkStep(0); setBulkClipIds([]); setBulkDrafts({}); await refresh(); setView("calendar");
    } catch (cause) { setError(errorText(cause)); }
    finally { setBusy(""); }
  }

  async function targetAction(target: Target, action: "cancel" | "pause" | "resume" | "retry") {
    setBusy(target.id); setError("");
    try { await apiRequest(`/social/targets/${target.id}/${action}`, { method: "POST", body: "{}" }); await refresh(); }
    catch (cause) { setError(errorText(cause)); }
    finally { setBusy(""); }
  }

  const accountLabel = (id: string) => accounts.find(account => account.id === id)?.display_name || "Account";
  const navChange = (next: View) => { setView(next); setError(""); setNotice(""); };

  return <div className="space-y-6">
    <header className="flex flex-wrap items-end justify-between gap-3"><div><p className="text-xs font-semibold uppercase tracking-[.18em] text-violet-600">Create · publish · schedule</p><h1 className="mt-2 text-3xl font-semibold">Social Publisher</h1><p className="mt-2 max-w-2xl text-sm text-slate-500">Connect authorized accounts, create platform-specific metadata, and manage each publishing target independently.</p></div><button className={secondary} onClick={() => void refresh()}>Refresh</button></header>
    {error && <p role="alert" className="break-words rounded-xl bg-rose-50 p-3 text-sm text-rose-700 dark:bg-rose-500/10 dark:text-rose-200">{error}</p>}
    {notice && <p role="status" className="rounded-xl bg-emerald-50 p-3 text-sm text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-200">{notice}</p>}
    <nav className="flex gap-1 overflow-x-auto rounded-xl border border-slate-200 bg-white p-1 dark:border-white/10 dark:bg-[#11141d]">{tabs.map(([key, label]) => <button key={key} onClick={() => navChange(key)} className={`shrink-0 rounded-lg px-3 py-2 text-xs font-medium ${view === key ? "bg-violet-600 text-white" : "text-slate-500 hover:bg-slate-100 dark:hover:bg-white/5"}`}>{label}</button>)}</nav>

    {view === "accounts" && <section className={section}>
      <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-lg font-semibold">Connected accounts</h2><p className="mt-1 text-xs text-slate-500">Tokens stay server-side encrypted; only connection status and public account details appear here.</p></div>{youtube?.connect_available && <button className={button} disabled={!!busy} onClick={() => void connectYoutube()}>{busy === "connect" ? "Opening Google…" : "Connect YouTube"}</button>}</div>
      {!youtube?.connect_available && <p className="mt-4 rounded-lg bg-amber-50 p-3 text-xs text-amber-800 dark:bg-amber-500/10 dark:text-amber-200">YouTube OAuth is not configured on this server. Set Google OAuth client values and the Fernet token-encryption key.</p>}
      <div className="mt-5 grid gap-3 md:grid-cols-2">{providers.map(provider => {
        const connected = accounts.filter(account => account.provider === provider.slug && account.status !== "disconnected");
        return <article key={provider.slug} className="rounded-xl border border-slate-200 p-4 dark:border-white/10"><div className="flex items-start justify-between gap-3"><div><p className="font-semibold">{provider.name}</p><p className="mt-1 text-xs text-slate-500">{provider.publishing_supported ? "Official publishing adapter implemented" : "Not implemented — no publishing simulation"}</p></div><span className={`rounded-full px-2 py-1 text-[10px] ${provider.publishing_supported ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"}`}>{provider.connect_available ? "Ready to connect" : provider.publishing_supported ? "Needs setup" : "Unavailable"}</span></div><p className="mt-3 text-xs leading-5 text-slate-500">{provider.availability_note}</p>{connected.length > 0 && <div className="mt-3 space-y-2">{connected.map(account => <div key={account.id} className="flex items-center justify-between gap-2 rounded-lg bg-slate-50 p-3 dark:bg-white/[.03]"><div className="min-w-0"><p className="truncate text-xs font-medium">{account.display_name}</p><p className="truncate text-[10px] text-slate-500">{account.username || account.external_account_id} · {account.status}</p></div><button className={secondary} disabled={!!busy} onClick={() => void disconnect(account)}>Disconnect</button></div>)}</div>}{provider.slug === "youtube" && provider.connect_available && <button className={`${secondary} mt-3`} disabled={!!busy} onClick={() => void connectYoutube()}>Connect / reconnect another channel</button>}</article>;
      })}</div>
      <p className="mt-4 text-[11px] text-slate-500">Disconnect removes ClipForge's local credentials. App-wide Google consent is revoked only when no other connected channel shares the OAuth grant.</p>
    </section>}

    {view === "compose" && <div className="grid gap-5 xl:grid-cols-[.9fr_1.1fr]">
      <section className={`${section} space-y-4`}><div><h2 className="text-lg font-semibold">Content composer</h2><p className="mt-1 text-xs text-slate-500">Select a rendered clip and connected accounts. Each account gets its own content and status.</p></div>
        <label className="block text-xs font-medium">Rendered clip<select value={selectedClip} onChange={event => { setSelectedClip(event.target.value); setPost(null); setMetadata({}); }} className={input}><option value="">Choose a clip</option>{availableClips.map(clip => <option key={clip.id} value={clip.id}>{clip.title} · {Math.round(clip.duration_seconds)}s</option>)}</select></label>
        {chosenClip?.video_url && <video controls preload="metadata" src={chosenClip.video_url} className="max-h-[440px] w-full rounded-xl bg-black object-contain"/>}
        <fieldset><legend className="text-xs font-medium">Connected publishing accounts</legend><div className="mt-2 space-y-2">{completedAccounts.filter(account => providers.find(provider => provider.slug === account.provider)?.publishing_supported).map(account => <label key={account.id} className="flex items-center gap-2 rounded-lg border border-slate-200 p-3 text-xs dark:border-white/10"><input type="checkbox" checked={selectedAccounts.includes(account.id)} onChange={() => toggleSelection(selectedAccounts, setSelectedAccounts, account.id)}/><span className="font-medium">{account.display_name}</span><span className="ml-auto text-slate-500">{account.provider_name}</span></label>)}</div>{completedAccounts.length === 0 && <p className="mt-2 text-xs text-slate-500">Connect a supported account first.</p>}</fieldset>
        <button className={button} disabled={!!busy || !selectedClip || !selectedAccounts.length} onClick={() => void createDraft()}>{busy === "draft" ? "Preparing…" : post ? "Create a new draft" : "Create draft"}</button>
        {post && <div className="border-t border-slate-100 pt-4 dark:border-white/5"><h3 className="text-sm font-semibold">AI content preferences</h3><div className="mt-3 grid gap-3 sm:grid-cols-2"><label className="text-xs">Target audience<input value={audience} onChange={event => setAudience(event.target.value)} placeholder="e.g. new video creators" className={input}/></label><label className="text-xs">Language<input value={language} onChange={event => setLanguage(event.target.value)} className={input}/></label><label className="text-xs">Tone<select value={tone} onChange={event => setTone(event.target.value)} className={input}>{["clear and engaging", "educational", "friendly", "witty", "professional", "inspirational"].map(item => <option key={item}>{item}</option>)}</select></label><label className="text-xs">Optional CTA<input value={cta} onChange={event => setCta(event.target.value)} placeholder="e.g. follow for more" className={input}/></label></div><button className={`${secondary} mt-3`} disabled={!!busy} onClick={() => void generateMetadata()}>{busy === "generate" ? "Generating platform-specific drafts…" : "Generate AI metadata · charged per platform"}</button><p className="mt-2 text-[10px] text-slate-500">Drafts use transcript and available clip analysis only. They never certify unseen visuals; review and approve all generated copy.</p></div>}
      </section>
      <section className={section}><div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-lg font-semibold">Review per platform</h2><p className="mt-1 text-xs text-slate-500">Edit content and privacy independently for every account.</p></div>{post && <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs dark:bg-white/5">Draft · {post.targets.length} targets</span>}</div>
        {!post ? <p className="mt-8 text-sm text-slate-500">Create a draft to generate, edit, preview, or schedule platform-specific content.</p> : <div className="mt-4 space-y-4">{post.targets.map(target => {
          const value = metadata[target.id] || target.metadata || blankMetadata(post.title);
          const targetOptions = options[target.id] || target.platform_options || { privacy_status: "private", made_for_kids: null };
          return <article key={target.id} className="rounded-xl border border-slate-200 p-4 dark:border-white/10"><div className="flex flex-wrap items-center justify-between gap-2"><div><h3 className="text-sm font-semibold">{target.provider_name} · {target.account_name}</h3><p className="text-[10px] text-slate-500">Target status: {target.status.replaceAll("_", " ")}</p></div><button className={secondary} disabled={!!busy} onClick={() => void saveTarget(target).then(() => setNotice(`${target.provider_name} content saved.`)).catch(cause => setError(errorText(cause)))}>Save this platform</button></div>
            <div className="mt-3 grid gap-3"><label className="text-xs">Title<input value={value.title || ""} maxLength={100} onChange={event => setMetadata(current => ({ ...current, [target.id]: { ...value, title: event.target.value } }))} className={input}/></label><label className="text-xs">Caption<textarea rows={3} maxLength={5000} value={value.caption || ""} onChange={event => setMetadata(current => ({ ...current, [target.id]: { ...value, caption: event.target.value } }))} className={input}/></label><label className="text-xs">Description<textarea rows={4} maxLength={5000} value={value.description || ""} onChange={event => setMetadata(current => ({ ...current, [target.id]: { ...value, description: event.target.value } }))} className={input}/></label><label className="text-xs">Hashtags<textarea rows={2} value={(value.hashtags || []).join(" ")} onChange={event => setMetadata(current => ({ ...current, [target.id]: { ...value, hashtags: splitTags(event.target.value) } }))} placeholder="#creator #video" className={input}/></label><label className="text-xs">Visibility<select value={targetOptions.privacy_status || "private"} onChange={event => setOptions(current => ({ ...current, [target.id]: { ...targetOptions, privacy_status: event.target.value } }))} className={input}><option value="private">Private</option><option value="unlisted">Unlisted</option><option value="public">Public</option></select></label><label className="text-xs">Audience declaration<select value={targetOptions.made_for_kids === true ? "true" : targetOptions.made_for_kids === false ? "false" : ""} onChange={event => setOptions(current => ({ ...current, [target.id]: { ...targetOptions, made_for_kids: event.target.value === "" ? null : event.target.value === "true" } }))} className={input}><option value="">Choose made-for-kids status</option><option value="false">Not made for kids</option><option value="true">Made for kids</option></select></label><p className="text-[10px] text-amber-700">Select the accurate audience declaration. This is sent to YouTube and must follow its rules.</p><p className="rounded-lg bg-slate-50 p-3 text-xs dark:bg-white/[.03]"><span className="font-medium">Preview:</span> {value.title || "Untitled"}<br/>{value.caption || value.description || "No caption yet."}<br/><span className="text-violet-600">{(value.hashtags || []).join(" ")}</span></p></div>
          </article>;
        })}
        <div className="border-t border-slate-100 pt-4 dark:border-white/5"><label className="block max-w-sm text-xs">Schedule time ({timezone})<input type="datetime-local" value={scheduleAt} onChange={event => setScheduleAt(event.target.value)} className={input}/></label><div className="mt-3 flex flex-wrap gap-2"><button className={button} disabled={!!busy} onClick={() => void publishNow()}>{busy === "publish" ? "Queuing…" : "Publish now"}</button><button className={secondary} disabled={!!busy || !scheduleAt} onClick={() => void schedulePost()}>{busy === "schedule" ? "Scheduling…" : "Schedule"}</button></div><p className="mt-2 text-[10px] text-slate-500">Uploads are sent through official APIs only. If YouTube does not confirm an upload, the target is held for reconciliation rather than blindly duplicated.</p></div>
      </div>}
      </section>
    </div>}

    {view === "bulk" && <section className={`${section} space-y-5`}><div><h2 className="text-lg font-semibold">Bulk scheduling review</h2><p className="mt-1 text-xs text-slate-500">Choose each clip's time and write distinct metadata per selected account. Review all entries before confirming.</p></div>
      <div><h3 className="text-xs font-semibold">1. Select connected accounts</h3><div className="mt-2 flex flex-wrap gap-2">{completedAccounts.filter(account => providers.find(provider => provider.slug === account.provider)?.publishing_supported).map(account => <label key={account.id} className="flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-xs dark:border-white/10"><input type="checkbox" checked={bulkAccounts.includes(account.id)} onChange={() => toggleBulkAccount(account.id)}/>{account.display_name}</label>)}</div></div>
      <div><h3 className="text-xs font-semibold">2. Select clips to schedule</h3>{availableClips.length === 0 ? <p className="mt-2 text-xs text-slate-500">No completed rendered clips are available yet.</p> : <div className="mt-2 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">{availableClips.map(clip => <label key={clip.id} className="flex items-center gap-3 rounded-lg border border-slate-200 p-3 text-xs dark:border-white/10"><input type="checkbox" checked={bulkClipIds.includes(clip.id)} onChange={() => toggleBulkClip(clip)}/><span className="line-clamp-2">{clip.title}</span></label>)}</div>}</div>
      {bulkClipIds.length > 0 && <div className="space-y-3"><h3 className="text-xs font-semibold">3. Per-clip time and per-account copy</h3>{bulkClipIds.map(clipId => {
        const clip = clips.find(item => item.id === clipId)!;
        const draft = bulkDrafts[clipId] || { scheduledAt: defaultSchedule(), metadata: {}, options: {} };
        return <article key={clipId} className="rounded-xl border border-slate-200 p-4 dark:border-white/10"><div className="flex flex-wrap items-center justify-between gap-2"><h4 className="text-sm font-semibold">{clip.title}</h4><label className="text-xs">Publish at ({timezone})<input type="datetime-local" value={draft.scheduledAt} onChange={event => setBulkDrafts(value => ({ ...value, [clipId]: { ...draft, scheduledAt: event.target.value } }))} className={input}/></label></div><div className="mt-3 grid gap-3 lg:grid-cols-2">{bulkAccounts.map(accountId => {
          const value = draft.metadata[accountId] || blankMetadata(clip.title);
          return <div key={accountId} className="rounded-lg bg-slate-50 p-3 dark:bg-white/[.03]"><p className="mb-2 text-xs font-semibold">{accountLabel(accountId)} · metadata</p><label className="text-[10px]">Title<input value={value.title} onChange={event => setBulkDrafts(current => ({ ...current, [clipId]: { ...draft, metadata: { ...draft.metadata, [accountId]: { ...value, title: event.target.value } } } }))} className={input}/></label><label className="mt-2 block text-[10px]">Caption / description<textarea rows={3} value={value.description || value.caption} onChange={event => setBulkDrafts(current => ({ ...current, [clipId]: { ...draft, metadata: { ...draft.metadata, [accountId]: { ...value, caption: event.target.value, description: event.target.value } } } }))} className={input}/></label><label className="mt-2 block text-[10px]">Hashtags<input value={(value.hashtags || []).join(" ")} onChange={event => setBulkDrafts(current => ({ ...current, [clipId]: { ...draft, metadata: { ...draft.metadata, [accountId]: { ...value, hashtags: splitTags(event.target.value) } } } }))} className={input}/></label><label className="mt-2 block text-[10px]">Visibility<select value={draft.options[accountId]?.privacy_status || "private"} onChange={event => setBulkDrafts(current => ({ ...current, [clipId]: { ...draft, options: { ...draft.options, [accountId]: { ...draft.options[accountId], privacy_status: event.target.value } } } }))} className={input}><option value="private">Private</option><option value="unlisted">Unlisted</option><option value="public">Public</option></select></label><label className="mt-2 block text-[10px]">Audience declaration<select value={draft.options[accountId]?.made_for_kids === true ? "true" : draft.options[accountId]?.made_for_kids === false ? "false" : ""} onChange={event => setBulkDrafts(current => ({ ...current, [clipId]: { ...draft, options: { ...draft.options, [accountId]: { ...draft.options[accountId], privacy_status: draft.options[accountId]?.privacy_status || "private", made_for_kids: event.target.value === "" ? null : event.target.value === "true" } } } }))} className={input}><option value="">Choose made-for-kids status</option><option value="false">Not made for kids</option><option value="true">Made for kids</option></select></label></div>;
        })}</div></article>;
      })}</div>}
      <div className="flex flex-wrap gap-2">{bulkStep === 0 ? <button className={button} disabled={!bulkClipIds.length || !bulkAccounts.length} onClick={() => setBulkStep(1)}>Review batch · {bulkClipIds.length} posts</button> : <><button className={secondary} onClick={() => setBulkStep(0)}>Back to edits</button><button className={button} disabled={!!busy} onClick={() => void confirmBulkSchedule()}>{busy === "bulk" ? "Scheduling batch…" : `Confirm ${bulkClipIds.length} scheduled posts`}</button></>}</div>
      {bulkStep === 1 && <div className="rounded-xl bg-violet-50 p-4 text-xs text-violet-900 dark:bg-violet-500/10 dark:text-violet-100"><strong>Review:</strong> {bulkClipIds.length} clips, {bulkAccounts.length} account(s) per clip, {bulkClipIds.length * bulkAccounts.length} independent targets. Dates are stored as UTC; display timezone is {timezone}. No API calls occur until you confirm.</div>}
    </section>}

    {view === "calendar" && <section className={section}><div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-lg font-semibold">Publishing calendar</h2><p className="mt-1 text-xs text-slate-500">Times are displayed in {timezone}; storage and dispatch are UTC.</p></div><div className="flex items-center gap-2"><select value={calendarMode} onChange={event => setCalendarMode(event.target.value as typeof calendarMode)} className="rounded-lg border border-slate-200 bg-white px-2 py-2 text-xs dark:border-white/10 dark:bg-[#0c0f16]"><option value="month">Monthly</option><option value="week">Weekly</option><option value="list">List</option></select><button className={secondary} onClick={() => setCalendarDate(new Date(calendarDate.getFullYear(), calendarDate.getMonth() - 1, 1))}>Previous</button><button className={secondary} onClick={() => setCalendarDate(new Date())}>Today</button><button className={secondary} onClick={() => setCalendarDate(new Date(calendarDate.getFullYear(), calendarDate.getMonth() + 1, 1))}>Next</button></div></div>
      <p className="mt-3 text-sm font-semibold">{calendarMode === "list" ? "Next 30 days" : calendarDate.toLocaleDateString(undefined, { month: "long", year: "numeric" })}</p>
      {calendarMode === "list" ? <div className="mt-3 space-y-2">{calendarItems.map(item => <CalendarCard key={item.target_id} item={item} timezone={timezone}/>)}</div> : <CalendarGrid items={calendarItems} date={calendarDate} mode={calendarMode} timezone={timezone}/>}
      {calendarItems.length === 0 && <p className="mt-5 text-xs text-slate-500">No scheduled targets in this range.</p>}
    </section>}

    {view === "queue" && <section className={section}><div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-lg font-semibold">Publishing queue & history</h2><p className="mt-1 text-xs text-slate-500">Each destination has its own state. Uncertain uploads are held for manual reconciliation to avoid duplicate posts.</p></div><button className={secondary} onClick={() => void refresh()}>Refresh statuses</button></div>
      {posts.length === 0 ? <p className="mt-6 text-sm text-slate-500">No publishing drafts or history yet.</p> : <div className="mt-4 space-y-3">{posts.map(item => <article key={item.id} className="rounded-xl border border-slate-200 p-4 dark:border-white/10"><div className="flex flex-wrap items-center justify-between gap-2"><div><h3 className="text-sm font-semibold">{item.title}</h3><p className="text-[10px] text-slate-500">Post {item.status.replaceAll("_", " ")} · created {new Date(item.created_at).toLocaleString()}</p></div><span className="rounded-full bg-slate-100 px-2 py-1 text-[10px] dark:bg-white/5">{item.targets.length} target(s)</span></div><div className="mt-3 space-y-2">{item.targets.map(target => <div key={target.id} className="flex flex-wrap items-center gap-2 rounded-lg bg-slate-50 p-3 dark:bg-white/[.03]"><div className="min-w-0 flex-1"><p className="truncate text-xs font-medium">{target.provider_name} · {target.account_name} · {target.status.replaceAll("_", " ")}</p>{target.scheduled_at && <p className="text-[10px] text-slate-500">{new Date(target.scheduled_at).toLocaleString(undefined, { timeZone: target.schedule_timezone || timezone })} ({target.schedule_timezone || timezone})</p>}{target.last_error_message && <p className="mt-1 break-words text-[10px] text-rose-600">{target.last_error_code}: {target.last_error_message}</p>}{target.status === "reconciliation_required" && <p className="mt-1 text-[10px] font-medium text-amber-700">Check the destination account before manually creating a new attempt.</p>}</div>{target.external_post_url && <a href={target.external_post_url} target="_blank" rel="noreferrer" className="text-xs font-medium text-violet-600">View post</a>}{target.status === "scheduled" && <button className={secondary} disabled={!!busy} onClick={() => void targetAction(target, "pause")}>Pause</button>}{target.status === "paused" && <button className={secondary} disabled={!!busy} onClick={() => void targetAction(target, "resume")}>Resume</button>}{["scheduled", "paused", "queued", "retry_waiting"].includes(target.status) && <button className={secondary} disabled={!!busy} onClick={() => void targetAction(target, "cancel")}>Cancel</button>}{target.status === "failed" && <button className={secondary} disabled={!!busy} onClick={() => void targetAction(target, "retry")}>Retry</button>}</div>)}</div></article>)}</div>}
    </section>}
  </div>;
}

function CalendarCard({ item, timezone }: { item: CalendarItem; timezone: string }) {
  return <article className="rounded-lg border border-slate-200 p-3 dark:border-white/10"><p className="text-xs font-semibold">{new Date(item.scheduled_at_utc).toLocaleString(undefined, { timeZone: timezone })} · {item.provider_name}</p><p className="mt-1 text-xs">{item.metadata?.title || item.title} · {item.account_name}</p><p className="text-[10px] text-slate-500">{item.status.replaceAll("_", " ")}</p></article>;
}

function CalendarGrid({ items, date, mode, timezone }: { items: CalendarItem[]; date: Date; mode: "month" | "week"; timezone: string }) {
  const first = mode === "week" ? new Date(date) : new Date(date.getFullYear(), date.getMonth(), 1);
  if (mode === "week") { const offset = (first.getDay() + 6) % 7; first.setDate(first.getDate() - offset); }
  else first.setDate(first.getDate() - ((first.getDay() + 6) % 7));
  const days = mode === "week" ? 7 : 42;
  return <div className="mt-4 grid grid-cols-7 gap-1.5"><div className="col-span-7 grid grid-cols-7 gap-1.5 text-center text-[10px] font-semibold text-slate-500">{["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map(day => <span key={day} className="py-1">{day}</span>)}</div>{Array.from({ length: days }, (_, index) => { const day = new Date(first); day.setDate(first.getDate() + index); const onDay = items.filter(item => new Date(item.scheduled_at_utc).toLocaleDateString("en-CA", { timeZone: timezone }) === day.toLocaleDateString("en-CA", { timeZone: timezone })); const inMonth = day.getMonth() === date.getMonth(); return <div key={day.toISOString()} className={`min-h-24 rounded-lg border p-1.5 ${mode === "month" && !inMonth ? "border-slate-100 bg-slate-50/60 text-slate-300 dark:border-white/5 dark:bg-white/[.01]" : "border-slate-200 dark:border-white/10"}`}><p className="text-[10px] font-medium">{day.getDate()}</p><div className="mt-1 space-y-1">{onDay.slice(0, 2).map(item => <p key={item.target_id} title={`${item.provider_name} · ${item.account_name}`} className="truncate rounded bg-violet-100 px-1 py-0.5 text-[9px] text-violet-800 dark:bg-violet-500/15 dark:text-violet-200">{new Date(item.scheduled_at_utc).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", timeZone: timezone })} {item.metadata?.title || item.title}</p>)}{onDay.length > 2 && <p className="text-[9px] text-slate-500">+{onDay.length - 2} more</p>}</div></div>; })}</div>;
}
