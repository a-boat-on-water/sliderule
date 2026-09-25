"use client";

import { useAuth } from "@clerk/nextjs";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ApiError, useApi } from "../../../lib/api";

type Stages = { active: string[]; terminal: string[] };

type Card = {
  id: number;
  stage: string;
  person_name: string;
  firm_name: string | null;
  bucket: string | null;
};

type ReviewCard = {
  id: number;
  person_name: string;
  firm_name: string | null;
  location: string | null;
  bucket: string | null;
  ai_reasoning: string | null;
  raw_text: string | null;
};

export default function CampaignPage() {
  const { id } = useParams<{ id: string }>();
  const { isLoaded, isSignedIn, orgId } = useAuth();
  const api = useApi();
  const [board, setBoard] = useState<Record<string, Card[]>>({});
  const [review, setReview] = useState<ReviewCard[]>([]);
  const [stages, setStages] = useState<Stages | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showAdd, setShowAdd] = useState(false);

  const reload = useCallback(() => {
    Promise.all([
      api(`/campaigns/${id}/board`),
      api(`/campaigns/${id}/review`),
    ])
      .then(([b, r]) => {
        setBoard(b.board);
        setReview(r.review);
        setError(null);
      })
      .catch((err: unknown) =>
        setError(err instanceof ApiError ? err.message : String(err)),
      );
  }, [api, id]);

  useEffect(() => {
    if (!isLoaded || !isSignedIn || !orgId) return;
    // board columns come from the same source that enforces the graph
    api("/stages").then((s) =>
      setStages({
        active: s.order.filter((st: string) => !s.terminal.includes(st)),
        terminal: s.terminal,
      }),
    );
    reload();
    const timer = setInterval(reload, 10_000); // pick up worker evaluations
    return () => clearInterval(timer);
  }, [isLoaded, isSignedIn, orgId, api, reload]);

  const decide = async (cpId: number, verdict: "approve" | "reject") => {
    let reason: string | null = null;
    if (verdict === "reject") {
      reason = window.prompt("Rejection reason (required):");
      if (!reason) return;
    }
    try {
      await api(`/campaign-people/${cpId}/decision`, {
        method: "POST",
        body: { verdict, reason },
      });
      reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  };

  const approveAllStrong = async () => {
    try {
      await api(`/campaigns/${id}/approve-all-strong`, { method: "POST" });
      reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  };

  if (!isLoaded) return null;
  if (!isSignedIn || !orgId) {
    return (
      <div className="notice">
        <h2>Sign in and select an organization</h2>
        <p>Use the title block above.</p>
      </div>
    );
  }

  const strongCount = review.filter((c) => c.bucket === "strong").length;
  const terminal = Object.entries(board)
    .filter(([stage]) => stages?.terminal.includes(stage))
    .map(([stage, cards]) => `${stage} ${cards.length}`)
    .join(" · ");

  return (
    <>
      {error && <div className="errband mono">{error}</div>}

      <div className="cols">
        {(stages?.active ?? []).map((stage) => (
          <div key={stage} className={`col ${stage === "screened" ? "gate" : ""}`}>
            <div className="colh">
              <span className="lbl">
                {stage === "screened" ? "screened ⌾" : stage.replace("_", " ")}
              </span>
              <span className="n">{board[stage]?.length ?? 0}</span>
            </div>
            {(board[stage] ?? []).map((card) => (
              <div key={card.id} className="card">
                <div className="nm">{card.person_name}</div>
                {card.firm_name && <div className="fm">{card.firm_name}</div>}
                {card.bucket && (
                  <span className={`bkt bkt-${card.bucket}`}>{card.bucket}</span>
                )}
              </div>
            ))}
          </div>
        ))}
      </div>
      {terminal && (
        <div style={{ padding: "6px 16px", color: "var(--ink-3)", fontSize: 11 }}
             className="mono">
          parked / terminal: {terminal}
        </div>
      )}

      <div className="sectionh">
        <span className="lbl">Review queue · {review.length}</span>
        {strongCount > 0 && (
          <button className="stampbtn" style={{ marginLeft: "auto" }}
                  onClick={approveAllStrong}>
            ⌾ Approve all Strong ({strongCount})
          </button>
        )}
        <button className="ghostbtn"
                style={{ marginLeft: strongCount > 0 ? 0 : "auto" }}
                onClick={() => setShowAdd((v) => !v)}>
          {showAdd ? "Close" : "Add candidate"}
        </button>
      </div>

      {showAdd && (
        <AddCandidateForm campaignId={id} onAdded={() => { setShowAdd(false); reload(); }}
                          onError={setError} />
      )}

      {review.map((card) => (
        <div key={card.id} className="review-card">
          <h3>
            {card.person_name}
            {card.bucket && (
              <span className={`bkt bkt-${card.bucket}`} style={{ marginLeft: 10 }}>
                {card.bucket}
              </span>
            )}
          </h3>
          <div style={{ color: "var(--ink-3)", fontSize: 12 }}>
            {[card.firm_name, card.location].filter(Boolean).join(" · ")}
          </div>
          {card.ai_reasoning && (
            <p className="review-reasoning">{card.ai_reasoning}</p>
          )}
          {card.raw_text && <div className="review-raw">{card.raw_text}</div>}
          <div className="btnrow">
            <button className="stampbtn" onClick={() => decide(card.id, "approve")}>
              Approve
            </button>
            <button className="ghostbtn" onClick={() => decide(card.id, "reject")}>
              Reject…
            </button>
          </div>
        </div>
      ))}
      {review.length === 0 && (
        <p style={{ padding: "18px 16px", color: "var(--ink-3)" }}>
          Nothing waiting for review. Add candidates and the evaluation runs
          in the background — screened people appear here.
        </p>
      )}
    </>
  );
}

function AddCandidateForm({
  campaignId,
  onAdded,
  onError,
}: {
  campaignId: string;
  onAdded: () => void;
  onError: (message: string) => void;
}) {
  const api = useApi();
  const [name, setName] = useState("");
  const [linkedin, setLinkedin] = useState("");
  const [firm, setFirm] = useState("");
  const [location, setLocation] = useState("");
  const [profile, setProfile] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    try {
      await api(`/campaigns/${campaignId}/people`, {
        method: "POST",
        body: {
          name,
          linkedin_url: linkedin.trim() || null,
          firm_name: firm.trim() || null,
          location: location.trim() || null,
          raw_profile: profile,
        },
      });
      onAdded();
    } catch (err) {
      onError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="formgrid" style={{ borderBottom: "1px solid var(--rule-2)" }}>
      <label>
        <span className="lbl">Name</span>
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label>
        <span className="lbl">LinkedIn URL · optional</span>
        <input value={linkedin} onChange={(e) => setLinkedin(e.target.value)}
               placeholder="linkedin.com/in/…" />
      </label>
      <label>
        <span className="lbl">Firm · optional</span>
        <input value={firm} onChange={(e) => setFirm(e.target.value)}
               placeholder="MG Engineering D.P.C." />
      </label>
      <label>
        <span className="lbl">Location · optional</span>
        <input value={location} onChange={(e) => setLocation(e.target.value)} />
      </label>
      <label className="wide">
        <span className="lbl">Profile text · pasted from anywhere</span>
        <textarea value={profile} onChange={(e) => setProfile(e.target.value)}
                  placeholder="Paste the resume, LinkedIn about section, bio —
whatever evidence exists. The evaluation reads exactly this." />
      </label>
      <div className="wide">
        <button className="stampbtn" disabled={!name.trim() || !profile.trim() || busy}
                onClick={submit}>
          {busy ? "Adding…" : "Add candidate"}
        </button>
      </div>
    </div>
  );
}
