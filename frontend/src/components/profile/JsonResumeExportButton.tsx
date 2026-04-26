"use client";

import { useState } from "react";
import { Download } from "lucide-react";
import { Button } from "@/components/ui/button";
import { getJsonResume } from "@/lib/api";
import { toast } from "@/lib/toast";

export function JsonResumeExportButton() {
  const [loading, setLoading] = useState(false);

  async function handleExport() {
    setLoading(true);
    try {
      const data = await getJsonResume();
      const json = JSON.stringify(data.resume, null, 2);
      const blob = new Blob([json], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "resume.json";
      a.click();
      URL.revokeObjectURL(url);
      toast.success("resume.json downloaded");
    } catch (err: unknown) {
      toast.apiError(err, "Failed to export resume");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Button
      variant="outline"
      size="sm"
      className="gap-1.5"
      disabled={loading}
      onClick={handleExport}
    >
      {loading ? (
        <span className="animate-spin inline-block h-3.5 w-3.5 border-2 border-current border-t-transparent rounded-full" />
      ) : (
        <Download className="h-3.5 w-3.5" />
      )}
      {loading ? "Exporting…" : "Export JSON Resume"}
    </Button>
  );
}
