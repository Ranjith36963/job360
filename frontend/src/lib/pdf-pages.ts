/** Draw every page of a PDF into canvases that fit a width - the in-page CV preview.
 *
 * pdf.js is loaded only when a preview opens (it is large), from its legacy build so older
 * phone browsers can run it. Scripts inside a PDF never run: eval is off and no scripting. */

export interface PdfPages {
  /** How many pages the file has. */
  count: number;
  /** Draws page `n` (1-based) into `canvas`, `cssWidth` CSS pixels wide, sharp on this screen.
   * `cancel` stops it (a cancelled drawing resolves quietly, so the canvas can be drawn again). */
  draw: (n: number, canvas: HTMLCanvasElement, cssWidth: number) => { done: Promise<void>; cancel: () => void };
  /** Frees the document. */
  close: () => void;
}

export async function openPdf(data: ArrayBuffer): Promise<PdfPages> {
  const pdfjs = await import("pdfjs-dist/legacy/build/pdf.mjs");
  if (!pdfjs.GlobalWorkerOptions.workerSrc) {
    pdfjs.GlobalWorkerOptions.workerSrc = new URL(
      "pdfjs-dist/legacy/build/pdf.worker.min.mjs",
      import.meta.url
    ).toString();
  }
  const task = pdfjs.getDocument({ data, isEvalSupported: false, enableXfa: false });
  const doc = await task.promise;
  return {
    count: doc.numPages,
    draw: (n, canvas, cssWidth) => {
      let cancelled = false;
      let render: { cancel: () => void } | null = null;
      const done = (async () => {
        const page = await doc.getPage(n);
        if (cancelled) return;
        const ratio = typeof window === "undefined" ? 1 : window.devicePixelRatio || 1;
        const base = page.getViewport({ scale: 1 });
        const viewport = page.getViewport({ scale: (cssWidth / base.width) * ratio });
        canvas.width = Math.floor(viewport.width);
        canvas.height = Math.floor(viewport.height);
        canvas.style.width = "100%";
        canvas.style.aspectRatio = `${base.width} / ${base.height}`;
        const ctx = canvas.getContext("2d");
        if (!ctx) throw new Error("This browser can't draw the preview");
        const task = page.render({ canvasContext: ctx, viewport });
        render = task;
        try {
          await task.promise;
        } catch (err) {
          if (!cancelled) throw err;
        }
      })();
      return {
        done,
        cancel: () => {
          cancelled = true;
          render?.cancel();
        },
      };
    },
    close: () => void task.destroy(),
  };
}
