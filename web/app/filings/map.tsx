"use client";

import { useEffect, useRef, useState } from "react";

export type Pin = {
  id: number;
  firm_name: string;
  work_type: string;
  project_address: string | null;
  latitude: number | null;
  longitude: number | null;
};

const COLORS: Record<string, string> = {
  mechanical_systems: "#2a78d6",
  plumbing: "#1baf7a",
  sprinkler: "#eda100",
};

// NYC-ish default bounds when there are no pins yet
const DEFAULT = { minLat: 40.55, maxLat: 40.92, minLng: -74.26, maxLng: -73.68 };

type Center = { lat: number; lng: number; radiusKm: number } | null;

/** Schematic filing map: a drafting grid with real coordinates projected
 * linearly. Real tiles (Leaflet) can replace this without changing the API. */
export function FilingMap({ pins, center }: { pins: Pin[]; center: Center }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [hover, setHover] = useState<Pin | null>(null);
  const located = pins.filter((p) => p.latitude != null && p.longitude != null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const parent = canvas.parentElement!;
    const dpr = window.devicePixelRatio || 1;
    const W = parent.clientWidth;
    const H = parent.clientHeight;
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    canvas.style.width = `${W}px`;
    canvas.style.height = `${H}px`;
    const ctx = canvas.getContext("2d")!;
    ctx.scale(dpr, dpr);

    const b = bounds(located);
    const px = (lng: number) =>
      ((lng - b.minLng) / (b.maxLng - b.minLng)) * (W - 40) + 20;
    const py = (lat: number) =>
      ((b.maxLat - lat) / (b.maxLat - b.minLat)) * (H - 40) + 20;

    ctx.fillStyle = "#f8f7f2";
    ctx.fillRect(0, 0, W, H);
    ctx.strokeStyle = "#e7e4d8";
    ctx.lineWidth = 1;
    for (let x = 14; x < W; x += 28) {
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke();
    }
    for (let y = 14; y < H; y += 28) {
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
    }

    for (const p of located) {
      ctx.beginPath();
      ctx.arc(px(p.longitude!), py(p.latitude!), 3, 0, Math.PI * 2);
      ctx.fillStyle = COLORS[p.work_type] ?? "#9094a1";
      ctx.globalAlpha = 0.88;
      ctx.fill();
      ctx.globalAlpha = 1;
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = "#f8f7f2";
      ctx.stroke();
    }

    if (center) {
      // km -> degrees at this latitude, then to pixels via the x projection
      const degPerKm = 1 / (111.32 * Math.cos((center.lat * Math.PI) / 180));
      const rPx = Math.abs(
        px(center.lng + center.radiusKm * degPerKm) - px(center.lng),
      );
      ctx.setLineDash([6, 5]);
      ctx.lineWidth = 1.6;
      ctx.strokeStyle = "#c2392f";
      ctx.beginPath();
      ctx.arc(px(center.lng), py(center.lat), rPx, 0, Math.PI * 2);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.arc(px(center.lng), py(center.lat), 4.5, 0, Math.PI * 2);
      ctx.fillStyle = "#c2392f";
      ctx.fill();
    }

    const onMove = (e: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      let best: Pin | null = null;
      let bestD = 100; // 10px radius
      for (const p of located) {
        const d = (px(p.longitude!) - mx) ** 2 + (py(p.latitude!) - my) ** 2;
        if (d < bestD) { bestD = d; best = p; }
      }
      setHover(best);
    };
    canvas.addEventListener("mousemove", onMove);
    return () => canvas.removeEventListener("mousemove", onMove);
  }, [pins, center]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <canvas
        ref={canvasRef}
        role="img"
        aria-label={`Map of ${located.length} filings, colored by work type`}
      />
      <span className="maplbl mono">
        {located.length.toLocaleString()} FILINGS PLOTTED
      </span>
      {hover && (
        <div className="maphover">
          <b>{hover.firm_name}</b>
          {hover.project_address ? ` — ${hover.project_address}` : ""}
          <span className="mono" style={{ marginLeft: 8, color: "var(--ink-3)" }}>
            {hover.work_type}
          </span>
        </div>
      )}
    </>
  );
}

function bounds(pins: Pin[]) {
  if (pins.length < 2) return DEFAULT;
  let minLat = Infinity, maxLat = -Infinity, minLng = Infinity, maxLng = -Infinity;
  for (const p of pins) {
    minLat = Math.min(minLat, p.latitude!);
    maxLat = Math.max(maxLat, p.latitude!);
    minLng = Math.min(minLng, p.longitude!);
    maxLng = Math.max(maxLng, p.longitude!);
  }
  const padLat = (maxLat - minLat) * 0.08 || 0.01;
  const padLng = (maxLng - minLng) * 0.08 || 0.01;
  return {
    minLat: minLat - padLat, maxLat: maxLat + padLat,
    minLng: minLng - padLng, maxLng: maxLng + padLng,
  };
}
