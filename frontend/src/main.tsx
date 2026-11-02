import React from 'react';
import ReactDOM from 'react-dom/client';
import { ClerkProvider } from '@clerk/react';
import { Planner } from './Planner';
import './style.css';
async function start() { const root = ReactDOM.createRoot(document.getElementById('root')!); try { const response = await fetch('/api/config'); const config = await response.json(); if (!config.clerk_publishable_key) throw new Error('Clerk sign-in is not configured.'); root.render(<React.StrictMode><ClerkProvider publishableKey={config.clerk_publishable_key}><Planner /></ClerkProvider></React.StrictMode>); } catch { root.render(<main className="auth"><h1>TripMate</h1><p role="alert">Secure sign-in could not be loaded. Refresh the page or contact support.</p></main>); } }
void start();
