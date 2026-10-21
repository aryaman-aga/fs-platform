/**
 * In-app PDF viewer (pdf.js). The PDF bytes come from the watermarked download
 * endpoint (E-SEC-10); a banner repeats who is viewing and when. The worker is
 * served from this origin so the API's CSP (worker-src 'self') is satisfied.
 */
import * as pdfjs from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { useEffect, useRef, useState } from "react";

import { formatDateTime } from "@/lib/format";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

export function PdfViewer({ data, viewer, label, maxPages = 60 }: { data: ArrayBuffer; viewer: string; label: string; maxPages?: number }) {
  const holder = useRef<HTMLDivElement>(null);
  const [pages, setPages] = useState(0);
  const [rendered, setRendered] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [scale, setScale] = useState(1.15);

  useEffect(() => {
    let cancelled = false;
    const node = holder.current;
    if (!node) return;
    node.querySelectorAll("canvas").forEach((c) => c.remove());
    setError(null);
    setRendered(0);
    const task = pdfjs.getDocument({ data: new Uint8Array(data.slice(0)) });
    task.promise
      .then(async (doc) => {
        if (cancelled) return;
        setPages(doc.numPages);
        for (let n = 1; n <= Math.min(doc.numPages, maxPages); n += 1) {
          const page = await doc.getPage(n);
          if (cancelled) return;
          const vp = page.getViewport({ scale });
          const canvas = document.createElement("canvas");
          const ratio = window.devicePixelRatio || 1;
          canvas.width = Math.floor(vp.width * ratio);
          canvas.height = Math.floor(vp.height * ratio);
          canvas.style.width = `${Math.floor(vp.width)}px`;
          canvas.setAttribute("role", "img");
          canvas.setAttribute("aria-label", `${label} — page ${n} of ${doc.numPages}`);
          const ctx = canvas.getContext("2d");
          if (!ctx) continue;
          ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
          node.appendChild(canvas);
          await page.render({ canvasContext: ctx, viewport: vp, canvas }).promise;
          if (!cancelled) setRendered(n);
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) setError((e as Error).message || "cannot display this PDF");
      });
    return () => {
      cancelled = true;
      void task.destroy();
    };
  }, [data, scale, label, maxPages]);

  return (
    <div className="stack">
      <div className="row between">
        <span className="small muted" data-testid="pdf-pages">
          {pages ? `${rendered} of ${pages} page(s) shown` : "Rendering…"}
          {pages > maxPages ? ` — download for the full document` : ""}
        </span>
        <div className="row">
          <button type="button" className="btn sm" onClick={() => setScale((s) => Math.max(0.6, s - 0.15))} aria-label="Zoom out">
            −
          </button>
          <span className="small">{Math.round(scale * 100)}%</span>
          <button type="button" className="btn sm" onClick={() => setScale((s) => Math.min(2.5, s + 0.15))} aria-label="Zoom in">
            +
          </button>
        </div>
      </div>
      <div className="pdf-viewer" data-testid="pdf-viewer">
        <div className="watermark-banner">
          Confidential — viewed by {viewer} · {formatDateTime(new Date())} · watermarked copy
        </div>
        {error ? <div className="notice error">{error}</div> : null}
        {/* pdf.js canvases live in their own node, apart from the React-managed children */}
        <div className="pdf-canvases" ref={holder} />
      </div>
    </div>
  );
}
