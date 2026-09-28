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
  const [loadError, setLoadError] = useState<string | null>(null);
  // role created by a previous attempt whose campaign POST failed: reuse it
  // on retry (same text) instead of inserting a duplicate roles_wanted row
  const [createdRole, setCreatedRole] = useState<{ id: number; text: string } | null>(null);

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
      .catch((err) =>
        setLoadError(err instanceof ApiError ? err.message : String(err)),
      );
  }, [isLoaded, isSignedIn, orgId, api, router]);

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      const description = text.trim();
      const title = description.split(/\r?\n/)[0].trim().slice(0, 60);
      let roleId = createdRole?.text === description ? createdRole.id : null;
      if (roleId === null) {
        const role = await api("/roles", {
          method: "POST",
          body: { title, rubric: { description } },
        });
        roleId = role.id as number;
        setCreatedRole({ id: roleId, text: description });
      }
      const month = new Date().toLocaleString("en-US", {
        month: "short",
        year: "numeric",
      });
      const campaign = await api("/campaigns", {
        method: "POST",
        body: { name: `${title} — ${month}`, role_wanted_id: roleId },
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
  if (loadError) {
    return (
      <div className="notice">
        <h2>Couldn&apos;t reach sliderule</h2>
        <p style={{ color: "var(--stamp)", fontSize: 12 }}>{loadError}</p>
        <p>Reload to try again.</p>
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
