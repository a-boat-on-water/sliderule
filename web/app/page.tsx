"use client";

import { useAuth } from "@clerk/nextjs";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, useApi } from "../lib/api";

/** Home: straight into your campaign if one exists; otherwise one question. */
export default function Home() {
  const { isLoaded, isSignedIn, orgId } = useAuth();
  const api = useApi();
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isLoaded || !isSignedIn || !orgId) return;
    api("/campaigns")
      .then((res) => {
        if (res.campaigns.length > 0) {
          router.replace(`/campaigns/${res.campaigns[0].id}`);
        } else {
          setReady(true);
        }
      })
      .catch(() => setReady(true));
  }, [isLoaded, isSignedIn, orgId, api, router]);

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      const title = text.trim().split("\n")[0].slice(0, 60);
      const role = await api("/roles", {
        method: "POST",
        body: { title, rubric: { description: text.trim() } },
      });
      const month = new Date().toLocaleString("en-US", {
        month: "short",
        year: "numeric",
      });
      const campaign = await api("/campaigns", {
        method: "POST",
        body: { name: `${title} — ${month}`, role_wanted_id: role.id },
      });
      router.push(`/campaigns/${campaign.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
      setBusy(false);
    }
  };

  if (!isLoaded) return null;
  if (!isSignedIn || !orgId) {
    return (
      <div className="notice">
        <h2>Sign in to sliderule</h2>
        <p>
          Sign in and select your organization with the controls in the title
          block above to start finding engineers.
        </p>
      </div>
    );
  }
  if (!ready) return null;

  return (
    <div className="notice" style={{ maxWidth: 560 }}>
      <h2>Who are you hiring?</h2>
      <p>
        One sentence is enough — role, where, what matters, any dealbreakers.
        This becomes the yardstick every candidate is screened against.
      </p>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={
          "Part-time mechanical engineer in NYC. Must have hands-on DOB " +
          "filing / production drawing experience. No licensed principals " +
          "looking for partner roles."
        }
        style={{
          width: "100%", minHeight: 110, border: "1px solid var(--rule-2)",
          padding: "8px 10px", fontSize: 13, fontFamily: "inherit",
          borderRadius: 2, marginBottom: 12,
        }}
      />
      {error && <p style={{ color: "var(--stamp)", fontSize: 12 }}>{error}</p>}
      <button className="stampbtn" disabled={!text.trim() || busy} onClick={start}>
        {busy ? "Setting up…" : "Start finding engineers"}
      </button>
    </div>
  );
}
