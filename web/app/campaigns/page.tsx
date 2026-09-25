"use client";

import { useAuth } from "@clerk/nextjs";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ApiError, useApi } from "../../lib/api";

type Campaign = {
  id: number;
  name: string;
  status: string;
  role_title: string;
  people_count: number;
};

type Role = { id: number; title: string };

export default function CampaignsPage() {
  const { isLoaded, isSignedIn, orgId } = useAuth();
  const api = useApi();
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showNew, setShowNew] = useState(false);

  const reload = useCallback(() => {
    Promise.all([api("/campaigns"), api("/roles")])
      .then(([c, r]) => {
        setCampaigns(c.campaigns);
        setRoles(r.roles);
      })
      .catch((err: unknown) =>
        setError(err instanceof ApiError ? err.message : String(err)),
      );
  }, [api]);

  useEffect(() => {
    if (isLoaded && isSignedIn && orgId) reload();
  }, [isLoaded, isSignedIn, orgId, reload]);

  if (!isLoaded) return null;
  if (!isSignedIn || !orgId) {
    return (
      <div className="notice">
        <h2>Sign in and select an organization</h2>
        <p>Campaigns belong to an organization — use the title block above.</p>
      </div>
    );
  }

  return (
    <>
      <div className="sectionh" style={{ borderTop: 0 }}>
        <span className="lbl">Campaigns</span>
        <button
          className="stampbtn"
          style={{ marginLeft: "auto" }}
          onClick={() => setShowNew((v) => !v)}
        >
          {showNew ? "Close" : "New campaign"}
        </button>
      </div>
      {error && <div className="errband mono">{error}</div>}
      {showNew && (
        <NewCampaignForm
          roles={roles}
          onCreated={() => {
            setShowNew(false);
            reload();
          }}
          onError={setError}
        />
      )}
      {campaigns.map((c) => (
        <Link key={c.id} href={`/campaigns/${c.id}`} className="rowlink">
          <b>{c.name}</b>
          <span style={{ color: "var(--ink-3)", marginLeft: 10, fontSize: 12 }}>
            {c.role_title} · {c.people_count}{" "}
            {c.people_count === 1 ? "person" : "people"} · {c.status}
          </span>
        </Link>
      ))}
      {campaigns.length === 0 && !showNew && (
        <p style={{ padding: 24, color: "var(--ink-3)" }}>
          No campaigns yet. Create one to start sourcing candidates for a role.
        </p>
      )}
    </>
  );
}

function NewCampaignForm({
  roles,
  onCreated,
  onError,
}: {
  roles: Role[];
  onCreated: () => void;
  onError: (message: string) => void;
}) {
  const api = useApi();
  const [name, setName] = useState("");
  const [roleId, setRoleId] = useState<string>("new");
  const [roleTitle, setRoleTitle] = useState("");
  const [hard, setHard] = useState("");
  const [green, setGreen] = useState("");
  const [red, setRed] = useState("");
  const [busy, setBusy] = useState(false);

  const lines = (value: string) =>
    value.split("\n").map((l) => l.trim()).filter(Boolean);

  const submit = async () => {
    setBusy(true);
    try {
      let role = roleId === "new" ? null : Number(roleId);
      if (role === null) {
        const created = await api("/roles", {
          method: "POST",
          body: {
            title: roleTitle,
            rubric: {
              hard_requirements: lines(hard),
              green_flags: lines(green),
              red_flags: lines(red),
            },
          },
        });
        role = created.id;
      }
      await api("/campaigns", {
        method: "POST",
        body: { name, role_wanted_id: role },
      });
      onCreated();
    } catch (err) {
      onError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const needsRole = roleId === "new";
  const valid = name.trim() && (!needsRole || roleTitle.trim());

  return (
    <div className="formgrid" style={{ borderBottom: "1px solid var(--rule-2)" }}>
      <label>
        <span className="lbl">Campaign name</span>
        <input value={name} onChange={(e) => setName(e.target.value)}
               placeholder="Fall 2026 · Mechanical PT" />
      </label>
      <label>
        <span className="lbl">Role</span>
        <select value={roleId} onChange={(e) => setRoleId(e.target.value)}>
          <option value="new">New role…</option>
          {roles.map((r) => (
            <option key={r.id} value={r.id}>{r.title}</option>
          ))}
        </select>
      </label>
      {needsRole && (
        <>
          <label className="wide">
            <span className="lbl">Role title</span>
            <input value={roleTitle} onChange={(e) => setRoleTitle(e.target.value)}
                   placeholder="Mechanical engineer (part-time)" />
          </label>
          <label>
            <span className="lbl">Hard requirements · one per line</span>
            <textarea value={hard} onChange={(e) => setHard(e.target.value)} />
          </label>
          <label>
            <span className="lbl">Green flags · one per line</span>
            <textarea value={green} onChange={(e) => setGreen(e.target.value)} />
          </label>
          <label className="wide">
            <span className="lbl">Red flags · one per line</span>
            <textarea value={red} onChange={(e) => setRed(e.target.value)} />
          </label>
        </>
      )}
      <div className="wide">
        <button className="stampbtn" disabled={!valid || busy} onClick={submit}>
          {busy ? "Creating…" : "Create campaign"}
        </button>
      </div>
    </div>
  );
}
