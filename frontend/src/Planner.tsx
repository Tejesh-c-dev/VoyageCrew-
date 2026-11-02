import { useAuth, useUser, SignIn, UserButton } from '@clerk/react';
import { useCallback, useEffect, useRef, useState } from 'react';
import DOMPurify from 'dompurify';
import { marked } from 'marked';
import html2pdf from 'html2pdf.js';
import { api, apiMessage, installTokenProvider } from './api';
import './style.css';

type Result = { success: boolean; thread_id: string; answer: string; itinerary: string; requires_approval: boolean; approval_request: string; selected_agents: string[]; supervisor_reasoning: string; guardrail_allowed: boolean; guardrail_reason: string; };
const labels: Record<string, string> = { flight_agent: '?? Flight Agent', hotel_agent: '?? Hotel Agent', weather_agent: '??? Weather Agent', budget_agent: '?? Budget Agent', itinerary_agent: '??? Itinerary Agent' };
const prompts = ['Plan a complete 7 days Japan trip from Bangladesh including flights, hotels and sightseeing under 2 lakhs.', 'Plan a 5 days Dubai trip from Dhaka with flights, hotels and sightseeing.', 'Plan a 7 days Thailand trip from Bangladesh with budget hotels and sightseeing.', 'Give me all country flight info.'];
declare global { interface Window { tripmateRenderMarkdown?: (markdown: string) => string } }
export function sanitizeMarkdown(value: string): string {
  return DOMPurify.sanitize(marked.parse(value || '') as string, { ADD_TAGS: [], ADD_ATTR: [], FORBID_TAGS: ['script','style','iframe'], FORBID_ATTR: ['onerror','onload','onclick','style'], ALLOWED_URI_REGEXP: /^(https?|ftp):\/\//i });
}
if (typeof window !== 'undefined') window.tripmateRenderMarkdown = sanitizeMarkdown;
function SafeMarkdown({ value }: { value: string }) {
  const html = sanitizeMarkdown(value);  return <div className="result-box" dangerouslySetInnerHTML={{ __html: html }} />;
}
export function Planner() {
  const { isLoaded, isSignedIn, getToken } = useAuth();
  const { user } = useUser();
  const [input, setInput] = useState(''); const [feedback, setFeedback] = useState('');
  const [result, setResult] = useState<Result | null>(null); const [error, setError] = useState(''); const [busy, setBusy] = useState(false);
  const owner = user?.id || null; const busyRef = useRef(false); const priorOwner = useRef<string | null>(null); const requestVersion = useRef(0);
  const thread = useRef<string | null>(null);
  useEffect(() => {
    installTokenProvider(async () => await getToken());
  }, [getToken]);
  useEffect(() => {
    if (priorOwner.current !== owner) {
      requestVersion.current++; thread.current = null; setResult(null); setError(''); setBusy(false);
      if (owner) { const storedOwner = sessionStorage.getItem('travel_thread_owner'); thread.current = storedOwner === owner ? sessionStorage.getItem('travel_thread_id') : null; }
      sessionStorage.removeItem('travel_thread_id');
      if (owner && thread.current) sessionStorage.setItem('travel_thread_id', thread.current);
      if (owner) sessionStorage.setItem('travel_thread_owner', owner); else sessionStorage.removeItem('travel_thread_owner');
      priorOwner.current = owner;
    }
  }, [owner]);
  const submit = useCallback(async () => {
    setError(''); if (busyRef.current) return;
    if (result?.requires_approval) { setError('Please approve or revise the current draft before starting another plan.'); return; }
    const message = input.trim(); if (!message) { setError('Please enter your travel request first.'); return; }
    const version = ++requestVersion.current; busyRef.current = true; setBusy(true);
    try { const { data } = await api.post<Result>('/api/travel', { message, thread_id: thread.current }); if (version !== requestVersion.current) return;
      thread.current = data.thread_id; sessionStorage.setItem('travel_thread_id', data.thread_id); sessionStorage.setItem('travel_thread_owner', owner || ''); setResult(data);
    } catch(e) { if (version === requestVersion.current) setError(apiMessage(e)); } finally { if (version === requestVersion.current) { busyRef.current = false; setBusy(false); } }
  }, [busy, input, owner, result]);
  const approve = async (approved: boolean) => {
    setError(''); if (!thread.current || !result?.requires_approval) { setError('There is no draft waiting for approval.'); return; }
    if (!approved && !feedback.trim()) { setError('Please enter revision feedback before requesting changes.'); return; }
    const version = ++requestVersion.current; busyRef.current = true; setBusy(true);
    try { const { data } = await api.post<Result>('/api/travel/approve', { thread_id: thread.current, approved, feedback: feedback.trim() }); if (version === requestVersion.current) { setResult(data); setFeedback(''); } }
    catch(e) { if (version === requestVersion.current) setError(apiMessage(e)); } finally { if (version === requestVersion.current) { busyRef.current = false; setBusy(false); } }
  };
  useEffect(() => { const onKey = (event: KeyboardEvent) => { if (event.ctrlKey && event.key === 'Enter') void submit(); }; window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey); }, [submit]);
  const copy = async () => { try { await navigator.clipboard.writeText(document.querySelector('.result-box')?.textContent || ''); } catch { setError('Could not copy result.'); } };
  const pdf = async () => { const el = document.getElementById('pdfContent'); if (!el) return; try { await html2pdf().set({ margin: .5, filename: 'ai-travel-plan.pdf', image: { type: 'jpeg', quality: .98 }, html2canvas: { scale: 2, useCORS: true, backgroundColor: '#fff' }, jsPDF: { unit: 'in', format: 'a4', orientation: 'portrait' }, pagebreak: { mode: ['avoid-all','css','legacy'] } }).from(el).save(); } catch { setError('Could not download PDF.'); } };
  if (!isLoaded) return <main className="auth"><p>Loading secure sign-in…</p></main>;
  if (!isSignedIn) return <main className="auth"><h1>Welcome to TripMate</h1><p>Sign in to plan and save your travel plans.</p><SignIn /></main>;
  return <><header className="topbar"><strong>?? TripMate</strong><UserButton /></header><main className="app-container">
    <section className="hero"><div className="badge">?? VoyageCrew — AI Travel Planner</div><h1>Plan Your Perfect Trip with AI</h1><p>Search flights, discover hotels, check weather, assess your budget, and review the AI-generated draft before the final itinerary is created.</p><div className="features"><article>?? <b>Flight-first planning</b><small>Route ideas and airfare guidance</small></article><article>??? <b>Weather-aware trips</b><small>Seasonal conditions and forecast insights</small></article><article>?? <b>Human review</b><small>Approve or revise before finalizing</small></article></div></section>
    <section className="card"><h2>Where do you want to go?</h2><p>Example: Plan a complete 7 days Japan trip from Bangladesh under 2 lakhs.</p><textarea aria-label="Travel request" value={input} onChange={e => setInput(e.target.value)} placeholder="Describe your trip…"/><button disabled={busy} onClick={() => void submit()}>{busy ? 'Planning…' : 'Generate Draft'}</button><div className="prompts">{prompts.map((p,i)=><button className="quick" key={p} onClick={()=>setInput(p)}>{['Japan Trip','Dubai Trip','Thailand Trip','Global Flights'][i]}</button>)}</div></section>
    {busy && <p role="status" className="loading">Your travel plan is being prepared…</p>}
    {result && <><section className="card"><span className={`guard ${result.guardrail_allowed ? '' : 'blocked'}`}>Guardrail {result.guardrail_allowed ? 'passed' : 'blocked'}</span><h2>Execution Plan</h2><p>{result.supervisor_reasoning || 'Supervisor routing completed.'}</p><div className="prompts">{result.selected_agents.map(a=><span className="agent" key={a}>{labels[a] || a}</span>)}</div></section><section className="card"><div className="result-head"><div><h2>{result.requires_approval ? 'Draft Travel Plan' : 'Your Final AI Travel Plan'}</h2><small>Thread ID: {result.thread_id}</small></div><div><button className="secondary" onClick={()=>void copy()}>Copy</button> <button className="secondary" onClick={()=>void pdf()}>Download PDF</button></div></div><div id="pdfContent"><h1>AI Travel Plan</h1><SafeMarkdown value={result.requires_approval ? result.itinerary || result.answer : result.answer}/></div></section>{result.requires_approval && <section className="card"><span className="eyebrow">Human-in-the-Loop</span><h2>Review the draft itinerary</h2><p>{result.approval_request || 'Approve the draft or provide feedback before the final plan is generated.'}</p><textarea aria-label="Revision feedback" value={feedback} onChange={e=>setFeedback(e.target.value)} placeholder="Add revision feedback…"/><div className="prompts"><button disabled={busy} onClick={()=>void approve(true)}>Approve &amp; Generate Final</button><button disabled={busy} className="secondary" onClick={()=>void approve(false)}>Revise Using Feedback</button></div></section>}</>}
    {error && <p className="error" role="alert">{error}</p>}
  </main><footer>Built with FastAPI, LangGraph, PostgreSQL and MCP</footer></>;
}




