"use client";

import { useAuth } from "@clerk/nextjs";
import { useCallback } from "react";

const API_BASE =
  process.env.NEXT_PUBLIC_SLIDERULE_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

/** Authenticated fetch against the sliderule API. The Clerk session token is
 * attached as a Bearer header; the API reads org id and role from it. */
export function useApi() {
  const { getToken } = useAuth();
  return useCallback(
    async (path: string, params?: URLSearchParams) => {
      const token = await getToken();
      if (!token) throw new ApiError(401, "signed out");
      const qs = params && params.size > 0 ? `?${params}` : "";
      const res = await fetch(`${API_BASE}${path}${qs}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) throw new ApiError(res.status, await res.text());
      return res.json();
    },
    [getToken],
  );
}
