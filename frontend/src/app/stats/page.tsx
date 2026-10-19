import type { Metadata } from "next";
import { StatsClient } from "./StatsClient";

export const metadata: Metadata = { title: "Stats" };

// Protected by middleware.ts (`/stats` is in PROTECTED_PATHS).
export default function StatsPage() {
  return <StatsClient />;
}
