# Client guides

Printable guides for clients, generated as PDFs: a one-page installation guide and an illustrated user guide.
Both share one look (DejaVu Sans Condensed, a petrol-blue accent, numbered discs, "This is normal —" boxes).

## Installation guide (Windows desktop edition)

`install_guide.py` builds **one A4 page** for a non-technical person who installs FS-Platform on a Windows
PC, alone or on a phone or screen-share call: what the PC needs, eight numbered steps (install, then first
start), everyday use, colleagues, a forgotten password, and who to ask for help.

Regenerate it from the repository root for every release:

```sh
.venv/bin/python docs/client-guides/install_guide.py
.venv/bin/python docs/client-guides/install_guide.py --contact "Priya Shah · +91 98200 00000 · priya@example.com"
```

| Option | Default | Effect |
|---|---|---|
| `--version` | `__version__` in `fsoffline/src/fsoffline/__init__.py` | Printed beside the title. The setup file is always called `FS-Platform-Setup.exe`, whatever the version. |
| `--contact` | none | Shown in the *Need help?* box. Without it, the box says "Questions? Contact the person who sent you FS-Platform." |
| `--out` | `dist/client-guides/FS-Platform-Install-Guide.pdf` | Where the PDF is written. |

The script stops with an error if the content would not fit on one page (for example a very long `--contact`)
and warns about a lonely last word in a paragraph. After changing the wording, render the page and look at it:

```sh
.venv/bin/python -c "import pypdfium2; pypdfium2.PdfDocument('dist/client-guides/FS-Platform-Install-Guide.pdf')[0].render(scale=2).to_pil().save('/tmp/guide.png')"
```

Keep the guide in step with the product. It quotes the setup wizard (**Next**, **Next**, **Install**,
**Finish**), the setup page (**Create the firm**), the sign-in page (**Create an account**) and the
*Administration* menu (**Assignments**, **Users → Reset password**).

**Never commit the PDF.** `dist/` is git-ignored, and the confidentiality scan rejects PDF files outside
`fixtures/synthetic`. The fonts come from `fscore/src/fscore/pdf/fonts` (DejaVu Sans Condensed).

## User guide (illustrated, Windows desktop edition)

`user_guide.py` builds an illustrated A4 **user guide** (about 18 pages) for the staff of a firm, who know Ind AS
statements but are not technical. It starts where the installation guide ends ("open FS-Platform"): signing in and
out, a tour of the screen, the first client and engagement, uploading the workbook, the checks, the fix plan and its
second-person approval, draft and final PDFs, the other tabs, colleagues and roles, the administrator's tasks
(users, forgotten passwords, backups, "Confirm it's you") and troubleshooting.

Its pictures are real screenshots. `capture_screens.mjs` drives a throw-away copy of the desktop edition in a
browser (Playwright, loaded from `fsui`) and saves the screens, with the position of every button and field the
guide points at, to `build/client-guides/screens/` (PNG files and `manifest.json`). Regenerate both for every
release, from the repository root:

```sh
# once: the web UI and the browser that Playwright drives
(cd fsui && npm ci && npx playwright install chromium && npm run build)
# 1. the screenshots (about 8 minutes) of a throw-away FS-Platform: give an empty folder, delete it afterwards
node docs/client-guides/capture_screens.mjs --data-dir /tmp/fs-guide-data
# 2. the PDF
.venv/bin/python docs/client-guides/user_guide.py --contact "Priya Shah · +91 98200 00000 · priya@example.com"
```

The capture starts `python -m fsoffline --data-dir <folder> serve --no-browser` on the empty folder (an FS-Platform
already running there that nobody has set up yet is used as it is, and left running), sets up the invented firm
*Corvus Advisors* on the setup page and plays a working day with two invented people: Asha Menon, the administrator
and partner, and Meera Iyer, who creates her own account and becomes a manager. Client, entity and engagement; the
synthetic workbook from `fscore.synthetic` with the usual review errors; checks; Meera proposes the fix plan and
Asha approves it; a draft PDF; the corrected workbook, checked again, and the final PDF (should *Render FINAL*
fail, the capture notes it in the manifest, keeps a picture of the message and goes on); a review note, the
disclosure checklist and the audit timeline; a role, a password-reset link and a backup; a locked account. Then it
stops FS-Platform. It waits about five minutes at one point, so that *Confirm it's you* asks for the password,
and warns when the UI build in `fsui/dist` is older than its sources. If a step fails, it saves what both browser
windows showed as `failed-asha.png` and `failed-meera.png` next to the pictures, and still stops FS-Platform.
Only synthetic data is used and nothing leaves the computer. Passwords are made up for each run (and kept only in
`<data-dir>/capture-accounts.json`); filled password fields, one-time links and the backup locations (which
show the computer's own folders) are covered in the pictures.

| Script | Option | Default | Effect |
|---|---|---|---|
| `capture_screens.mjs` | `--data-dir` | required | The empty, throw-away data folder. Never a real one. |
| | `--out` | `build/client-guides/screens` | Where the PNG files and `manifest.json` go (replaced on every run). |
| | `--python` | `.venv/bin/python` | The Python that runs FS-Platform and the workbook generator. |
| | `--keep-running` | off | Leave FS-Platform running to look around; stop it with `.venv/bin/python -m fsoffline --data-dir <folder> stop`. |
| | `--headed` | off | Show the browser while it works. |
| `user_guide.py` | `--screens` | `build/client-guides/screens` | The captured pictures. |
| | `--version` | `__version__` in `fsoffline/src/fsoffline/__init__.py` | On the cover and in the footer; a warning if the pictures show another version. |
| | `--contact` | none | Shown in the *Need help?* boxes (cover and last page), as in the installation guide. |
| | `--out` | `dist/client-guides/FS-Platform-User-Guide.pdf` | Where the PDF is written. |

The build stops if a picture, or a button the guide frames on it, is missing (capture again after a UI change),
and fails if a word a non-technical reader should never meet (MFA, TOTP, API, loopback, port, SHA, JSON, endpoint)
slips into the text. After changing the wording or the pictures, render the pages and look at every one:

```sh
.venv/bin/python -c "import pypdfium2; [p.render(scale=1.5).to_pil().save(f'/tmp/guide-{n:02d}.png') for n, p in enumerate(pypdfium2.PdfDocument('dist/client-guides/FS-Platform-User-Guide.pdf'), 1)]"
```

Keep the guide in step with the product. It quotes the screens word for word (**Create an account**,
**Give a role**, **Propose fix plan**, **Apply and verify**, **Render FINAL**, **Backups & restore** …) and the
tray menu (**Back up now**, **Open data folder**). When a screen changes, capture again and re-read the steps
that go with it; the regions cut from each picture are in `user_guide.py`.

**Never commit the PDF or the pictures.** `build/` and `dist/` are git-ignored, and the confidentiality scan
rejects PDF files outside `fixtures/synthetic`.
