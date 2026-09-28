"use client";

import { SignInButton, useAuth } from "@clerk/nextjs";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ApiError, useApi } from "../../lib/api";

const WORK_TYPES = [
  { key: "mechanical_systems", label: "Mechanical", color: "#2a78d6" },
  { key: "plumbing", label: "Plumbing", color: "#1baf7a" },
  { key: "sprinkler", label: "Sprinkler", color: "#eda100" },
] as const;

const DAY_CHOICES = [90, 365, 730] as const;
const RADIUS_CHOICES = [5, 10, 25] as const;
const MIDTOWN = { lat: 40.758, lng: -73.9855 };

type Engineer = {
  name: string;
  title: string | null;
  license: string | null;
  firm_id: number;
  firm_name: string;
  firm_address: string | null;
  filing_count: number;
  last_filed_at: string | null;
  filings_by_work_type: Record<string, number> | null;
  recent_projects: string[] | null;
};

type Campaign = { id: number; name: string };

/** Engineers: the licensed professionals who sign the filings, ranked by how
 * many matching filings they signed. One click adds one to your campaign. */
export default function EngineersPage() {
  const { isLoaded, isSignedIn, orgId } = useAuth();
  const api = useApi();

  const [workTypes, setWorkTypes] = useState<Set<string>>(
    new Set(["mechanical_systems"]),
  );
  const [days, setDays] = useState<number | null>(365);
  const [radiusKm, setRadiusKm] = useState<number | null>(null);
  const [engineers, setEngineers] = useState<Engineer[]>([]);
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [added, setAdded] = useState<Record<string, number | "busy" | "dup">>({});
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const buildParams = useCallback(() => {
    const params = new URLSearchParams();
    for (const wt of workTypes) params.append("work_type", wt);
    if (days != null) {
      const from = new Date(Date.now() - days * 86400_000);
      params.set("filed_from", from.toISOString().slice(0, 10));
    }
    if (radiusKm != null) {
      params.set("lat", String(MIDTOWN.lat));
      params.set("lng", String(MIDTOWN.lng));
      params.set("radius_km", String(radiusKm));
    }
    params.set("limit", "200");
    return params;
  }, [workTypes, days, radiusKm]);

  useEffect(() => {
    if (!isLoaded || !isSignedIn || !orgId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    api("/engineers/search", { params: buildParams() })
      .then((res) => {
        if (!cancelled) setEngineers(res.engineers);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(
          err instanceof ApiError
            ? `API error ${err.status}: ${err.message}`
            : String(err),
        );
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [isLoaded, isSignedIn, orgId, api, buildParams]);

  // the campaign to add into: the org's first (the simple UI has one)
  useEffect(() => {
    if (!isLoaded || !isSignedIn || !orgId) return;
    api("/campaigns")
      .then((res) => setCampaign(res.campaigns[0] ?? null))
      .catch(() => setCampaign(null));
  }, [isLoaded, isSignedIn, orgId, api]);

  const keyOf = (e: Engineer) => `${e.firm_id}:${e.license ?? e.name.toLowerCase()}`;

  const add = async (e: Engineer) => {
    if (!campaign) return;
    const key = keyOf(e);
    setAdded((prev) => ({ ...prev, [key]: "busy" }));
    try {
      const res = await api(`/campaigns/${campaign.id}/people/from-filings`, {
        method: "POST",
        body: { name: e.name, firm_id: e.firm_id, license: e.license },
      });
      setAdded((prev) => ({ ...prev, [key]: res.campaign_person_id as number }));
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setAdded((prev) => ({ ...prev, [key]: "dup" }));
      } else {
        setError(err instanceof ApiError ? err.message : String(err));
        setAdded((prev) => {
          const next = { ...prev };
          delete next[key];
          return next;
        });
      }
    }
  };

  const toggleWorkType = (key: string) =>
    setWorkTypes((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        if (next.size > 1) next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });

  if (!isLoaded) return null;

  if (!isSignedIn) {
    return (
      <div className="notice">
        <h2>Sign in to sliderule</h2>
        <p>The engineers list is scoped to your organization — sign in to continue.</p>
        <SignInButton>
          <button className="stampbtn">Sign in</button>
        </SignInButton>
      </div>
    );
  }
  if (!orgId) {
    return (
      <div className="notice">
        <h2>No organization selected</h2>
        <p>Select or create one with the switcher in the title block above.</p>
      </div>
    );
  }

  const licensed = engineers.filter((e) => e.license).length;

  return (
    <>
      <div className="filters">
        {WORK_TYPES.map((wt) => (
          <button
            key={wt.key}
            className={`fchip ${workTypes.has(wt.key) ? "on" : ""}`}
            onClick={() => toggleWorkType(wt.key)}
            aria-pressed={workTypes.has(wt.key)}
          >
            <span className="sw" style={{ background: wt.color }} />
            {wt.label}
          </button>
        ))}
        <span className="fsep">|</span>
        {DAY_CHOICES.map((d) => (
          <button
            key={d}
            className={`fchip ${days === d ? "on" : ""}`}
            onClick={() => setDays(days === d ? null : d)}
            aria-pressed={days === d}
          >
            {d}d
          </button>
        ))}
        <span className="fsep">|</span>
        {RADIUS_CHOICES.map((r) => (
          <button
            key={r}
            className={`fchip ${radiusKm === r ? "on" : ""}`}
            onClick={() => setRadiusKm(radiusKm === r ? null : r)}
            aria-pressed={radiusKm === r}
            title="Radius from Midtown Manhattan"
          >
            {r} km
          </button>
        ))}
        {loading && <span className="lbl">searching…</span>}
        <span style={{ marginLeft: "auto" }} className="lbl">
          {campaign ? (
            <>adding to <Link href={`/campaigns/${campaign.id}`}>{campaign.name}</Link></>
          ) : (
            <>no campaign yet — <Link href="/">start one</Link></>
          )}
        </span>
      </div>

      {error && <div className="errband mono">{error}</div>}

      <div className="cells">
        <div className="cell">
          <span className="lbl">Engineers</span>
          <b>{engineers.length}</b>
          <small>{engineers.length === 200 ? "top 200 shown" : "applicants of record"}</small>
        </div>
        <div className="cell">
          <span className="lbl">Licensed</span>
          <b>{licensed}</b>
          <small>PE / RA license on file</small>
        </div>
        <div className="cell">
          <span className="lbl">Firms</span>
          <b>{new Set(engineers.map((e) => e.firm_id)).size}</b>
          <small>DOB NOW · dataset w9ak-ipjd</small>
        </div>
      </div>

      <div style={{ overflowY: "auto", flex: 1 }}>
        <table className="firms">
          <thead>
            <tr>
              <th>Engineer</th>
              <th>Firm</th>
              <th>Filings</th>
              <th>By work type</th>
              <th>Last filed</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {engineers.map((e) => {
              const key = keyOf(e);
              const state = added[key];
              return (
                <tr key={key}>
                  <td className="firm-name" title={e.recent_projects?.join("\n")}>
                    {e.name}
                    {e.title && (
                      <span className="bkt bkt-possible" style={{ marginLeft: 8, marginTop: 0 }}>
                        {e.title}{e.license ? ` ${e.license}` : ""}
                      </span>
                    )}
                  </td>
                  <td className="firm-addr">
                    {e.firm_name}
                    {e.firm_address && (
                      <div style={{ color: "var(--ink-3)", fontSize: 11 }}>{e.firm_address}</div>
                    )}
                  </td>
                  <td className="num">{e.filing_count}</td>
                  <td>
                    <div className="mini">
                      {WORK_TYPES.map((wt) => {
                        const n = e.filings_by_work_type?.[wt.key] ?? 0;
                        return n > 0 ? (
                          <i key={wt.key} style={{ flex: n, background: wt.color }} />
                        ) : null;
                      })}
                    </div>
                  </td>
                  <td className="num">{e.last_filed_at ?? "—"}</td>
                  <td className="num">
                    {typeof state === "number" ? (
                      <Link href={`/campaigns/${campaign?.id}`} className="lbl">added ✓</Link>
                    ) : state === "dup" ? (
                      <span className="lbl">already in</span>
                    ) : (
                      <button
                        className="ghostbtn"
                        disabled={!campaign || state === "busy"}
                        onClick={() => add(e)}
                      >
                        {state === "busy" ? "adding…" : "Add"}
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
            {!loading && engineers.length === 0 && !error && (
              <tr>
                <td colSpan={6} style={{ color: "var(--ink-3)", padding: 24 }}>
                  No engineers match. Widen the date range or work types, or
                  run a sync_filings job if the database is empty.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}
