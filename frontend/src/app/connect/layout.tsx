import type { ReactNode } from "react";
import { PageContainer } from "@/components/layout/PageContainer";

export default function ConnectLayout({ children }: { children: ReactNode }) {
  return <PageContainer>{children}</PageContainer>;
}
