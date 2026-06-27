import { useEffect, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import { supabase } from "./supabaseClient";
import { getMe, getCategories, type Me } from "./api";
import ProductsTab from "./components/ProductsTab";
import PromptsTab from "./components/PromptsTab";
import BackfillTab from "./components/BackfillTab";
import QuickGenerateTab from "./components/QuickGenerateTab";

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Track the Supabase auth session.
  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setLoading(false);
    });
    const { data: sub } = supabase.auth.onAuthStateChange((_e, s) => setSession(s));
    return () => sub.subscription.unsubscribe();
  }, []);

  // Once logged in, verify against the backend (profiles gate).
  useEffect(() => {
    if (!session) {
      setMe(null);
      return;
    }
    setError(null);
    getMe()
      .then((m) => {
        setMe(m);
        void getCategories().catch(() => {}); // warm the cache; tabs reuse it
      })
      .catch((e: Error) => setError(e.message));
  }, [session]);

  const signIn = () =>
    supabase.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: window.location.origin },
    });

  const signOut = () => supabase.auth.signOut();

  if (loading)
    return (
      <Centered>
        <span className="spinner" style={{ color: "var(--accent)" }} aria-hidden />
        <p className="muted">Loading…</p>
      </Centered>
    );

  if (!session) {
    return (
      <Centered>
        <div className="card stack" role="group" aria-label="Sign in">
          <div>
            <h1 className="font-display" style={{ fontSize: "var(--fs-2xl)" }}>Bindal's Creation</h1>
            <p className="muted" style={{ margin: "8px 0 0" }}>
              Mockup Generator — team access only
            </p>
          </div>
          <button className="btn-primary" onClick={signIn} style={{ width: "100%" }}>
            <GoogleMark />
            Sign in with Google
          </button>
        </div>
      </Centered>
    );
  }

  if (error) {
    return (
      <Centered>
        <div className="card stack" role="alert">
          <h1 style={{ fontSize: "var(--fs-xl)" }}>Access denied</h1>
          <p className="alert alert-error">{error}</p>
          <p className="subtle">Signed in as {session.user.email}</p>
          <button onClick={signOut}>Sign out</button>
        </div>
      </Centered>
    );
  }

  if (!me)
    return (
      <Centered>
        <span className="spinner" style={{ color: "var(--accent)" }} aria-hidden />
        <p className="muted">Verifying access…</p>
      </Centered>
    );

  return <Shell me={me} onSignOut={signOut} />;
}

const TABS = [
  { id: "products", label: "Products" },
  { id: "quickgen", label: "Quick Generate" },
  { id: "prompts", label: "Prompts" },
  { id: "backfill", label: "Backfill" },
] as const;
type TabId = (typeof TABS)[number]["id"];

function Shell({ me, onSignOut }: { me: Me; onSignOut: () => void }) {
  const [tab, setTab] = useState<TabId>("products");
  return (
    <div className="page">
      <header className="app-header border-b border-line pb-4">
        <h1 className="font-display tracking-tight">Mockup Generator</h1>
        <div className="user-meta">
          <span className="text-subtle">{me.email}</span>
          {me.role && (
            <span className="rounded-full border border-line bg-surface-2 px-2.5 py-0.5 text-xs font-medium text-muted">
              {me.role}
            </span>
          )}
          <button onClick={onSignOut}>Sign out</button>
        </div>
      </header>

      <nav className="tabs" role="tablist" aria-label="Sections">
        {TABS.map((t) => (
          <button
            key={t.id}
            className="tab"
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <div role="tabpanel">
        {tab === "products" ? (
          <ProductsTab />
        ) : tab === "quickgen" ? (
          <QuickGenerateTab />
        ) : tab === "prompts" ? (
          <PromptsTab />
        ) : (
          <BackfillTab />
        )}
      </div>
    </div>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return <div className="centered">{children}</div>;
}

function GoogleMark() {
  return (
    <svg width="16" height="16" viewBox="0 0 48 48" aria-hidden focusable="false">
      <path
        fill="#FFC107"
        d="M43.6 20.5H42V20H24v8h11.3c-1.6 4.7-6.1 8-11.3 8a12 12 0 1 1 0-24c3 0 5.8 1.1 7.9 3l5.7-5.7A20 20 0 1 0 24 44a20 20 0 0 0 19.6-23.5z"
      />
      <path
        fill="#FF3D00"
        d="M6.3 14.7l6.6 4.8A12 12 0 0 1 24 12c3 0 5.8 1.1 7.9 3l5.7-5.7A20 20 0 0 0 6.3 14.7z"
      />
      <path
        fill="#4CAF50"
        d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2A12 12 0 0 1 12.7 28l-6.5 5A20 20 0 0 0 24 44z"
      />
      <path
        fill="#1976D2"
        d="M43.6 20.5H42V20H24v8h11.3a12 12 0 0 1-4.1 5.6l6.2 5.2C40.9 35.7 44 30.4 44 24c0-1.2-.1-2.4-.4-3.5z"
      />
    </svg>
  );
}
