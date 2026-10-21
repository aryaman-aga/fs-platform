# fsrender-win — Windows render worker

A small, stateless Windows service that renders statement PDFs with **licensed, pinned
Microsoft Excel** for FS-Platform (04 §3 "Windows render workers", 02 §10A engine 1,
E-ARCH-03, ADR-0004). It is reached over the internal queue only: it *pulls* work from
the API, so the render box needs **no inbound ports**.

```
heartbeat ─► claim ─► download input.xlsx ─► Excel COM export (watchdog) ─► upload PDF + meta
                └── 204 (nothing queued) ─► sleep poll_interval_s
```

* One Excel instance per job; COM is initialised in the export thread.
* A watchdog kills **only** the `EXCEL.EXE` processes started by the current job
  (snapshot diff + creation time + service-account owner), never an interactive Excel.
* Each job runs in its own temp directory, which is always deleted.
* Logs contain job IDs and error types only — no client names, sheet content or figures.
* The PDF verification (PDF-01: every printed number present) runs on the Linux side
  after upload (`fscore.api.reconcile_pdf`); a PDF with misses can never be "final".

The Excel automation itself lives in the engine, `fscore.pdf.render_excel_com.export_pdf`,
so there is exactly one implementation of the export rules (print areas, grouped print
sheets, `ExportAsFixedFormat`, no save).

## Protocol (server side in `fsweb`)

| Call | Purpose |
|---|---|
| `POST /api/v1/internal/render/heartbeat` `{worker_id, excel_build}` | liveness; the API routes jobs to `render-win` only while a heartbeat is < 2 min old |
| `POST /api/v1/internal/render/claim` `{worker_id, excel_build}` | 200 `{job_id, run_id, mode, sheets, options}` or 204 |
| `GET /api/v1/internal/render/{job_id}/input` | the workbook version to render |
| `POST /api/v1/internal/render/{job_id}/complete` | multipart `pdf` + form field `meta` = `{engine, engine_version, excel_build}` |
| `POST /api/v1/internal/render/{job_id}/fail` `{error}` | failure (error type + short message) |

All calls carry `X-Worker-Token`. Transport errors, 429 and 5xx are retried with
exponential backoff; the loop itself backs off up to `max_backoff_s` while the API is down.

## Configuration

Environment variables (`FSRENDER_*`) override `%PROGRAMDATA%\FSRender\fsrender.ini`:

```ini
[fsrender]
api_url = https://fs-internal.example-firm.in
worker_token_file = C:\ProgramData\FSRender\worker.token
worker_id = RENDER-01
job_timeout_s = 300
poll_interval_s = 5
heartbeat_interval_s = 30
work_dir = D:\FSRender\work
log_dir = C:\ProgramData\FSRender\logs
; ca_bundle = C:\ProgramData\FSRender\firm-root-ca.pem
; excel_build = 16.0.xxxxx.yyyyy   (override; normally read from the Click-to-Run registry key)
```

`api_url` must be `https://` (an `http://` URL is accepted only for loopback, or with
`FSRENDER_ALLOW_HTTP=1` on an isolated lab network). The token file must be readable by
the service account only (`icacls worker.token /inheritance:r /grant:r "FIRM\svc-render:R"`).

```
fsrender-win check-config   # validate settings, show the detected Excel build
fsrender-win once           # claim and process at most one job (smoke test)
fsrender-win run            # foreground loop (Ctrl+C to stop)
fsrender-win service install --startup auto
fsrender-win service start | stop | remove
```

## Server preparation (runbook: `ops/runbooks/restore-render-worker.md`)

1. Windows Server 2022/2025, joined to the firm domain, no internet browsing, Defender on.
2. Microsoft 365 Apps with **shared-computer activation** (eligible plan such as Business
   Premium / E3 / E5) or Office LTSC volume licence — see the licensing note in
   `ops/hosting.md`. Pin the build (Click-to-Run channel + `updates enabled = false`,
   updated in the monthly patch window) and record it; every render records `excel_build`.
3. Dedicated service account `svc-render` (no admin rights, "Log on as a service").
   Log on interactively once as that account, open Excel, dismiss first-run dialogs,
   set the default printer to "Microsoft Print to PDF", and create
   `C:\Windows\System32\config\systemprofile\Desktop` and
   `C:\Windows\SysWOW64\config\systemprofile\Desktop` (Excel automation hangs without them
   when running non-interactively).
4. Disable Excel add-ins, protected-view prompts for the work directory, and AutoRecover
   for the service account (Group Policy export in `ops/` when Mode B hardening is done).
5. Install Python 3.11+ (all users), then `pip install fscore fsrender-win[win]` from the
   firm's internal wheelhouse (`--require-hashes`), configure, `service install`, `start`.
6. Verify: `fsrender-win once` with a synthetic fixture job queued from staging; the API's
   render-worker heartbeat metric must be fresh and the PDF-01 verification must show 0 misses.

Server-side automation of Office is not supported by Microsoft for unattended services
(risk register 11 §3): the design therefore assumes Excel may hang. The watchdog, one
Excel per job, queue retry on another worker (pool of 2+, E-ARCH-03), the LibreOffice
fallback and the mandatory PDF verification contain that risk.

## MSI packaging (WiX Toolset v4+)

A signed MSI keeps installation reproducible on the render pool:

1. Build a self-contained folder on a clean build VM:
   `python -m venv build\venv`, `build\venv\Scripts\pip install --no-index --find-links wheelhouse --require-hashes -r requirements.lock fsrender-win[win]`
   then `pyinstaller --onedir --name fsrender-win --hidden-import win32timezone -p src src/fsrender_win/__main__.py`
   (`win32timezone` is needed by pywin32 services when frozen).
2. `installer/FSRender.wxs` (sketch) installs the folder to `C:\Program Files\FSRender`,
   creates `C:\ProgramData\FSRender` (ACL: service account read, Administrators full),
   registers the service with `ServiceInstall` (account `FIRM\svc-render`, start auto,
   recovery: restart after 1 min) and never ships a token — the token is provisioned
   separately by the administrator.
3. `wix build installer\FSRender.wxs -o FSRender-<version>.msi`, then sign with the firm's
   code-signing certificate (`signtool sign /fd SHA256 /tr <timestamp-url> ...`).
4. Record the release in the ITGC change log (E-ITGC-01) with the MSI hash.

## Tests

```
.venv/bin/python -m pytest fsrender-win/tests -q
```

The tests run on macOS/Linux with a fake API (`httpx.MockTransport`) and a fake exporter:
claim/heartbeat/complete/fail, retries and backoff, size limit, invalid output, watchdog
timeout (only our Excel PID is killed), sandbox cleanup, and the run loop surviving an
API outage.
