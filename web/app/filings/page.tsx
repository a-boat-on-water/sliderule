"use client";

import { Show, SignInButton, useAuth } from "@clerk/nextjs";
import { useCallback, useEffect, useState } from "react";
import { ApiError, useApi } from "../../lib/api";

const WORK_TYPES = [
  { key: "mechanical_systems", label: "Mechanical", color: "#2a78d6" },
  { key: "plumbing", label: "Plumbing", color: "#1baf7a" },
  { key: "sprinkler", label: "Sprinkler", color: "#eda100" },
] as const;

const DAY_CHOICES = [30, 90, 365] as const;
const RADIUS_CHOICES = [5, 10, 25] as const;
const MIDTOWN = { lat: 40.758, lng: -73.9855 };

type Firm = {
  id: number;
  name: string;
  office_address: string | null;
  filing_count: number;
  last_filed_at: string | null;
  filings_by_work_type: Record<string, number> | null;
};

export default function FilingsPage() {
  const { isLoaded, isSignedIn, orgId } = useAuth();
  const api = useApi();

  const [workTypes, setWorkTypes] = useState<Set<string>>(
    new Set(WORK_TYPES.map((w) => w.key)),
  );
  const [days, setDays] = useState<number | null>(90);
  const [radiusKm, setRadiusKm] = useState<number | null>(null);
  const [firms, setFirms] = useState<Firm[]>([]);
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
    params.set("limit", "100");
    return params;
  }, [workTypes, days, radiusKm]);

  useEffect(() => {
    if (!isLoaded || !isSignedIn || !orgId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    api("/firms/search", buildParams())
      .then((res) => {
        if (!cancelled) setFirms(res.firms);
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
        <p>
          Filing search, campaigns and outreach are scoped to your
          organization — sign in to continue.
        </p>
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
        <p>
          Everything in sliderule belongs to an organization. Create or select
          one with the switcher in the title block above — for the first
          tenant, that&rsquo;s <b>Concord Consulting Engineering</b>.
        </p>
      </div>
    );
  }

  const totalFilings = firms.reduce((sum, f) => sum + f.filing_count, 0);
  const lastFiled = firms.reduce<string | null>(
    (latest, f) =>
      f.last_filed_at && (!latest || f.last_filed_at > latest)
        ? f.last_filed_at
        : latest,
    null,
  );

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
        {loading && <span className="lbl">syncing…</span>}
      </div>

      {error && <div className="errband mono">{error}</div>}

      <div className="cells">
        <div className="cell">
          <span className="lbl">Firms matching</span>
          <b>{firms.length}</b>
          <small>{firms.length === 100 ? "top 100 shown" : "ranked by filings"}</small>
        </div>
        <div className="cell">
          <span className="lbl">Filings</span>
          <b>{totalFilings.toLocaleString()}</b>
          <small>across firms shown</small>
        </div>
        <div className="cell">
          <span className="lbl">Most recent</span>
          <b>{lastFiled ?? "—"}</b>
          <small>DOB NOW · dataset w9ak-ipjd</small>
        </div>
      </div>

      <div style={{ overflowY: "auto", flex: 1 }}>
        <table className="firms">
          <thead>
            <tr>
              <th>Firm</th>
              <th>Office</th>
              <th>Filings</th>
              <th>By work type</th>
              <th>Last filed</th>
            </tr>
          </thead>
          <tbody>
            {firms.map((firm) => (
              <tr key={firm.id}>
                <td className="firm-name">{firm.name}</td>
                <td className="firm-addr">{firm.office_address ?? "—"}</td>
                <td className="num">{firm.filing_count}</td>
                <td>
                  <div className="mini" aria-label={miniLabel(firm)}>
                    {WORK_TYPES.map((wt) => {
                      const n = firm.filings_by_work_type?.[wt.key] ?? 0;
                      return n > 0 ? (
                        <i key={wt.key} style={{ flex: n, background: wt.color }} />
                      ) : null;
                    })}
                  </div>
                </td>
                <td className="num">{firm.last_filed_at ?? "—"}</td>
              </tr>
            ))}
            {!loading && firms.length === 0 && !error && (
              <tr>
                <td colSpan={5} style={{ color: "var(--ink-3)", padding: 24 }}>
                  No filings match. Widen the date range, or run a
                  sync_filings job if the database is empty.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}

function miniLabel(firm: Firm): string {
  const parts = WORK_TYPES.map((wt) => {
    const n = firm.filings_by_work_type?.[wt.key] ?? 0;
    return n > 0 ? `${wt.label} ${n}` : null;
  }).filter(Boolean);
  return parts.join(", ");
}
