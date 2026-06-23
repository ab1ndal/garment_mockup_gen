import { useEffect, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import { supabase } from "./supabaseClient";
import { getMe, type Me } from "./api";
import ProductsTab from "./components/ProductsTab";
import PromptsTab from "./components/PromptsTab";

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
      .then(setMe)
      .catch((e: Error) => setError(e.message));
  }, [session]);

  const signIn = () =>
    supabase.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: window.location.origin },
    });

  const signOut = () => supabase.auth.signOut();

  if (loading) return <Centered>Loading…</Centered>;

  if (!session) {
    return (
      <Centered>
        <h1>Bindal's Creation</h1>
        <p>Mockup Generator — team access only</p>
        <button onClick={signIn}>Sign in with Google</button>
      </Centered>
    );
  }

  if (error) {
    return (
      <Centered>
        <h1>Access denied</h1>
        <p style={{ color: "#b00" }}>{error}</p>
        <p>Signed in as {session.user.email}</p>
        <button onClick={signOut}>Sign out</button>
      </Centered>
    );
  }

  if (!me) return <Centered>Verifying access…</Centered>;

  return <Shell me={me} onSignOut={signOut} />;
}

function Shell({ me, onSignOut }: { me: Me; onSignOut: () => void }) {
  const [tab, setTab] = useState<"products" | "prompts">("products");
  return (
    <div style={{ padding: 32, fontFamily: "system-ui, sans-serif" }}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h1>Mockup Generator</h1>
        <div>
          <span style={{ marginRight: 12 }}>{me.email} · <strong>{me.role}</strong></span>
          <button onClick={onSignOut}>Sign out</button>
        </div>
      </header>
      <nav style={{ display: "flex", gap: 8, margin: "16px 0", borderBottom: "1px solid #ddd" }}>
        {(["products", "prompts"] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)}
            style={{ fontWeight: tab === t ? 700 : 400, border: "none", background: "none",
                     borderBottom: tab === t ? "2px solid #333" : "2px solid transparent", padding: "8px 12px", cursor: "pointer" }}>
            {t === "products" ? "Products" : "Prompts"}
          </button>
        ))}
      </nav>
      {tab === "products" ? <ProductsTab /> : <PromptsTab />}
    </div>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        flexDirection: "column",
        gap: 12,
        alignItems: "center",
        justifyContent: "center",
        fontFamily: "system-ui, sans-serif",
      }}
    >
      {children}
    </div>
  );
}
