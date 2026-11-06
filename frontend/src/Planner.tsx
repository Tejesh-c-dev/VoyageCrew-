import { useCallback, useEffect, useRef, useState } from 'react';
import DOMPurify from 'dompurify';
import { marked } from 'marked';
import { api, apiMessage } from './api';
import './style.css';

type Result = { success: boolean; thread_id: string; answer: string; itinerary: string; requires_approval: boolean; approval_request: string; selected_agents: string[]; supervisor_reasoning: string; guardrail_allowed: boolean; guardrail_reason: string; };
const labels: Record<string, string> = { flight_agent: '✈ Flight Agent', hotel_agent: '⌂ Hotel Agent', weather_agent: '☼ Weather Agent', budget_agent: '◈ Budget Agent', itinerary_agent: '▤ Itinerary Agent' };
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
  const [authReady, setAuthReady] = useState(false); const [owner, setOwner] = useState<string | null>(null); const [authMode, setAuthMode] = useState<'login'|'signup'>('login'); const [email, setEmail] = useState(''); const [password, setPassword] = useState('');
  const [input, setInput] = useState(''); const [feedback, setFeedback] = useState('');
  const [result, setResult] = useState<Result | null>(null); const [error, setError] = useState(''); const [busy, setBusy] = useState(false);
  const busyRef = useRef(false); const priorOwner = useRef<string | null>(null); const requestVersion = useRef(0);
  const thread = useRef<string | null>(null);
  useEffect(() => { api.get('/api/auth/session').then(({data}) => setOwner(data.user_id)).catch(() => setOwner(null)).finally(() => setAuthReady(true)); }, []);
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
  const pdf = async () => { const el = document.getElementById('pdfContent'); if (!el) return; try { const { default: html2pdf } = await import('html2pdf.js'); await html2pdf().set({ margin: .5, filename: 'ai-travel-plan.pdf', image: { type: 'jpeg', quality: .98 }, html2canvas: { scale: 2, useCORS: true, backgroundColor: '#fff' }, jsPDF: { unit: 'in', format: 'a4', orientation: 'portrait' }, pagebreak: { mode: ['avoid-all','css','legacy'] } }).from(el).save(); } catch { setError('Could not download PDF.'); } };
  if (!authReady) return <main className="auth"><p>Loading your account…</p></main>;
  if (!owner) return <main className="auth"><div className="brand"><span className="brand-mark">✳</span> TripMate</div><h1>Your next great trip starts here.</h1><p>Sign in to plan, refine, and keep your travel ideas together.</p><form onSubmit={async e => { e.preventDefault(); setError(''); try { const {data}=await api.post(`/api/auth/${authMode}`, {email,password}); setOwner(data.user_id); } catch(err) { setError(apiMessage(err)); } }}><label>Email <input type="email" required value={email} onChange={e=>setEmail(e.target.value)}/></label><label>Password <input type="password" required minLength={authMode==='signup'?10:1} value={password} onChange={e=>setPassword(e.target.value)}/></label><button>{authMode==='signup'?'Create account':'Sign in'}</button></form><button className="secondary" onClick={()=>setAuthMode(authMode==='login'?'signup':'login')}>{authMode==='login'?'Create an account':'Already have an account? Sign in'}</button>{error && <p role="alert" className="error">{error}</p>}</main>;
  return <><header className="topbar"><a className="brand" href="#top"><span className="brand-mark">✳</span> TripMate</a><nav aria-label="Main navigation"><a href="#planner">Planner</a><a href="#how-it-works">How it works</a></nav><div className="profile"><span>Travel workspace</span><button className="secondary" onClick={async()=>{await api.post('/api/auth/logout'); requestVersion.current++; thread.current=null; sessionStorage.removeItem('travel_thread_id'); sessionStorage.removeItem('travel_thread_owner'); setResult(null); setOwner(null);}}>Sign out</button></div></header><main id="top" className="app-container">
    <section className="hero"><div className="hero-copy"><div className="badge"><span>✦</span> YOUR PERSONAL AI TRAVEL PLANNER</div><h1>Go somewhere<br/><em>that stays with you.</em></h1><p>Thoughtful itineraries, built around your pace and budget. Tell us what you’re dreaming of and we’ll take it from there.</p><a className="hero-link" href="#planner">Start planning <span>↓</span></a></div><div className="hero-photo" role="img" aria-label="Sunrise over Mount Fuji and a quiet lakeside town"><span className="photo-caption">HAKONE, JAPAN <i>35°14′N 139°06′E</i></span><div className="photo-note"><span>✦</span><div><b>Made for your kind of travel</b><small>One good trip at a time</small></div></div></div></section>
    <section id="planner" className="planner-layout"><div className="planner-intro"><span className="eyebrow">THE JOURNEY BEGINS</span><h2>Where would you<br/>like to go?</h2><p>Share the details you already know. We’ll help with the rest.</p><div className="trust-note"><span>✓</span><div><b>You’re in control</b><small>Review every itinerary before it’s finalized.</small></div></div></div><div className="card planner-card"><label htmlFor="trip-request">Tell us about your trip</label><p className="field-hint">Destination, dates, who’s going, budget — anything helps.</p><textarea id="trip-request" aria-label="Travel request" value={input} onChange={e => setInput(e.target.value)} placeholder="I’m dreaming of a week in Japan this spring…"/><div className="planner-actions"><span>⌘ Enter to plan</span><button disabled={busy} onClick={() => void submit()}>{busy ? 'Planning your trip…' : 'Build my itinerary'} <span aria-hidden="true">→</span></button></div><div className="suggestions"><span>NEED A STARTING POINT?</span><div className="prompts">{prompts.slice(0,3).map((p,i)=><button className="quick" key={p} onClick={()=>setInput(p)}><span className={`destination-thumb thumb-${['japan','dubai','thailand'][i]}`} aria-hidden="true"/>{['Japan','Dubai','Thailand'][i]} <span>↗</span></button>)}</div></div></div></section>
    <section id="how-it-works" className="how-it-works" aria-label="How TripMate plans your trip"><article><span>01</span><div><b>We listen first</b><p>Your pace, budget, and wish list shape every recommendation.</p></div></article><article><span>02</span><div><b>A team plans together</b><p>Flights, stays, weather, and costs come into view.</p></div></article><article><span>03</span><div><b>You make the call</b><p>Review your draft and request changes before it’s final.</p></div></article></section>
    {busy && <p role="status" className="loading">Your travel plan is being prepared…</p>}
    {result && <><section className="card"><span className={`guard ${result.guardrail_allowed ? '' : 'blocked'}`}>Guardrail {result.guardrail_allowed ? 'passed' : 'blocked'}</span><h2>Execution Plan</h2><p>{result.supervisor_reasoning || 'Supervisor routing completed.'}</p><div className="prompts">{result.selected_agents.map(a=><span className="agent" key={a}>{labels[a] || a}</span>)}</div></section><section className="card"><div className="result-head"><div><h2>{result.requires_approval ? 'Draft Travel Plan' : 'Your Final AI Travel Plan'}</h2><small>Thread ID: {result.thread_id}</small></div><div><button className="secondary" onClick={()=>void copy()}>Copy</button> <button className="secondary" onClick={()=>void pdf()}>Download PDF</button></div></div><div id="pdfContent"><h1>AI Travel Plan</h1><SafeMarkdown value={result.requires_approval ? result.itinerary || result.answer : result.answer}/></div></section>{result.requires_approval && <section className="card"><span className="eyebrow">Human-in-the-Loop</span><h2>Review the draft itinerary</h2><p>{result.approval_request || 'Approve the draft or provide feedback before the final plan is generated.'}</p><textarea aria-label="Revision feedback" value={feedback} onChange={e=>setFeedback(e.target.value)} placeholder="Add revision feedback…"/><div className="prompts"><button disabled={busy} onClick={()=>void approve(true)}>Approve &amp; Generate Final</button><button disabled={busy} className="secondary" onClick={()=>void approve(false)}>Revise Using Feedback</button></div></section>}</>}
    {error && <p className="error" role="alert">{error}</p>}
  </main><footer><span className="brand"><span className="brand-mark">✳</span> TripMate</span><span>Thoughtful trips, made together.</span></footer></>;
}




