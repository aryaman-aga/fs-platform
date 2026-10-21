# Rebuild a Windows render worker (07 §5 `restore-render-worker`, E-ARCH-03)

The render worker is stateless: rebuilding it never loses data. Renders continue on the other pool
member or on the LibreOffice fallback meanwhile (finals that require Excel wait).

1. **Host.** Windows Server 2022/2025 from the firm's hardened image (or restore the weekly image backup).
   Join the domain, enable Defender, disable internet browsing, sync time to NIC/NPL:
   `w32tm /config /manualpeerlist:"samay1.nic.in samay2.nic.in" /syncfromflags:manual /update`.
2. **Excel.** Install Microsoft 365 Apps with shared-computer activation (or Office LTSC) at the
   **recorded build** from the last successful render's `excel_build` (render records / operations
   report). Disable automatic updates; updates happen in the monthly patch window with a regression
   render of the synthetic fixtures.
3. **Service account.** `FIRM\svc-render`, non-admin, "Log on as a service". Log on once interactively,
   open Excel, dismiss first-run prompts, and create the `systemprofile\Desktop` folders
   (see `fsrender-win/README.md`).
4. **Worker.** Install the signed MSI (or `pip install fsrender-win[win]` from the internal wheelhouse with
   `--require-hashes`), write `C:\ProgramData\FSRender\fsrender.ini`, place the token file (ACL: service
   account read only), then `fsrender-win check-config`.
5. **Register.** `fsrender-win service install --startup auto` and `start`. Within 2 minutes the API shows a
   fresh heartbeat; the `RenderWorkerDown` alert clears.
6. **Verify.** Queue a draft render of a synthetic fixture from staging; it must complete with PDF-01 = 0
   misses and record the expected `excel_build`.
7. **Record** the rebuild (ticket, MSI hash, Excel build) in the change log (E-ITGC-01).
