# FS-Platform on a Windows PC — installation and everyday administration

FS-Platform is delivered as one file, `FS-Platform-Setup-<version>.exe`. It installs everything the application
needs — its own Python runtime and libraries, the web interface, the Microsoft Visual C++ runtime and LibreOffice for
PDFs. Nothing else has to be installed and no internet connection is needed. FS-Platform runs on this PC only and
opens in your web browser at `http://127.0.0.1:8746`; it is not reachable from the network.

## What the PC needs

- Windows 10 (22H2) or Windows 11, 64-bit; 8 GB of memory recommended; 3 GB of free disk space plus room for your
  workbooks, PDFs and backups.
- Administrator rights to install (only for installing, upgrading and the Recovery tool).
- Microsoft Edge, Google Chrome or Firefox.
- No authenticator app: everyone signs in with their e-mail and password. An administrator can switch on a code from
  an authenticator app (Microsoft Authenticator, Google Authenticator or similar) for everyone — recommended when many
  people use the PC (see *Sign-in settings* below).
- Optional: Microsoft Excel (desktop). When Excel is installed, PDFs are printed through it, exactly as Excel prints
  them; otherwise the bundled LibreOffice is used.
- Recommended: BitLocker (or another full-disk encryption) on the PC — the data stays on its disk.

## Install

1. Double-click `FS-Platform-Setup-<version>.exe`. The installer is not code-signed yet, so Windows may show
   *"Windows protected your PC"*: click **More info**, then **Run anyway**. Approve the administrator prompt.
2. Keep the suggested components: the application (required), a desktop shortcut, and *Start FS-Platform when you
   sign in to Windows* (it then waits in the background with a tray icon).
3. Installation takes a few minutes (LibreOffice is unpacked). Click **Finish**.

## First start: create your firm and administrator

1. Start **FS-Platform** from the desktop or Start menu. Your browser opens the **setup page** (only on the very first
   start; nobody else can open it).
2. Enter your firm's name, your name and e-mail, and a password (at least 12 characters, not a common password).
   Tick *I also work on engagements* if you will prepare or review statements yourself, not only administer.
3. Sign in with your e-mail and password. (Only if the authenticator has been switched on: scan the QR code with your
   authenticator app, type the code it shows, and **store the recovery codes** somewhere safe.)

You can now create clients and engagements, upload workbooks and run the checks.

## Colleagues

- **A colleague creates an account:** on the sign-in page, **Create an account** — name, e-mail and a password. The
  account is signed in at once but sees **no client** until an administrator gives it a role.
- **Give the account a role:** the administrator finds the new account on *Admin → Users*, then assigns its role for
  each client on *Admin → Assignments* (with a reason; FS-Platform asks for the administrator's password again if
  they signed in more than five minutes ago). Approvals need a second person: a fix plan is approved by someone
  other than the person who proposed it.
- **Or invite a colleague:** *Admin → Users → Invite* shows a one-time link (valid for 72 hours) — your colleague
  opens it **on this PC** and sets a password. Inviting is the only way in once *Create an account* is switched off.
- **Colleagues with their own Windows account on this PC:** an administrator of the PC adds the account once with
  **FS-Platform Recovery → Allow another Windows account**; it works after that person signs out of Windows and back
  in. (The account that installed FS-Platform is already allowed.)
- **Forgotten password:** an administrator uses *Admin → Users → Reset password* (shows a one-time link). With the
  authenticator switched on, *Reset MFA* helps someone who lost their phone (they enrol a new one at the next
  sign-in).
- **Someone left, or an account nobody recognises:** *Admin → Users* — disable it (its sessions end at once).

## Everyday use

- The **tray icon** (next to the clock) has *Open FS-Platform*, *Back up now*, *Open data folder* and *Quit*.
  Double-click it to open FS-Platform. FS-Platform keeps running when you close the browser tab.
- Everyone signs in with their own account — never share one; sessions end after an hour without activity.
- Administrative actions (roles, resets, settings) ask for your password again if you signed in more than five minutes
  ago, and for a reason; both are recorded. Five wrong passwords in a row lock the account for 15 minutes (wrong
  passwords at such a confirmation also sign it out everywhere).

## Sign-in settings: the authenticator and *Create an account*

An administrator of the PC can change two switches in `C:\ProgramData\FSPlatform\fsplatform.ini`:

| Switch | Default | Effect |
|---|---|---|
| `mfa_required = true` | `false` | everyone also enters a code from an authenticator app at sign-in (enrolled with a QR code at the next sign-in; open sessions are asked for it at once), and confirms administrative actions with a code instead of the password |
| `signup_enabled = false` | `true` | the sign-in page no longer offers *Create an account*; colleagues are invited by an administrator |

Switch the authenticator on for a PC that many people use, and *Create an account* off once everyone who needs an
account has one.

1. Start **Notepad** as administrator (right-click → *Run as administrator*) and open
   `C:\ProgramData\FSPlatform\fsplatform.ini` (choose *All files*; create the file if it does not exist).
2. Add, or change, the `[app]` section, for example:

   ```ini
   [app]
   mfa_required = true
   signup_enabled = false
   ```

3. Save, then **Quit** FS-Platform from the tray icon and start it again from the Start menu.

Use `true` or `false`. If FS-Platform cannot read a value, it does not start and names the setting in its message.

## Backups

- A backup is taken **every day** while FS-Platform runs, and before every upgrade. The last 14 are kept in
  `C:\ProgramData\FSPlatform\backups` (the database, the keys that decrypt it, and a copy of every stored file).
- Backups on the same disk do not protect against a failed disk: set a **second backup folder** — a USB drive or a
  network share — on *Admin → Backups*. Every backup is then copied there too; a failed copy is reported in the tray
  and on that screen.
- *Back up now* in the tray takes one immediately.
- **Restore:** FS-Platform Recovery → *Restore a backup*. The current data is kept aside in
  `C:\ProgramData\FSPlatform\before-restore-<date>` first.

## FS-Platform Recovery (Start menu)

For the PC's administrator (it asks for administrator rights):

1. Reset an administrator's password (shows a one-time link) — when nobody who can sign in is an administrator.
2. Reset an administrator's authenticator — only matters when the authenticator is switched on (the menu says so).
3. Allow another Windows account to use FS-Platform.
4. Restore a backup.
5. Save diagnostics for support (logs and versions only — no workbooks, figures or keys).
6. Stop FS-Platform.

Every account change made here is recorded in the audit trail.

## Upgrade

Run the newer `FS-Platform-Setup-<version>.exe`. It stops FS-Platform, replaces the program files and keeps all
data. At the next start the database is updated — after an automatic backup.

## Uninstall

*Settings → Apps → FS-Platform → Uninstall*. Your data stays in `C:\ProgramData\FSPlatform` unless you tick
*Also delete all FS-Platform data on this PC*.

## Where things are

| What | Where |
|---|---|
| Program files (incl. LibreOffice) | `C:\Program Files\FS-Platform` |
| Database, stored files, keys | `C:\ProgramData\FSPlatform` (only SYSTEM, Administrators and the *FS-Platform Users* group can open it) |
| Backups | `C:\ProgramData\FSPlatform\backups` (+ your second folder) |
| Logs | `C:\ProgramData\FSPlatform\logs` |
| Settings an administrator may change | `C:\ProgramData\FSPlatform\fsplatform.ini` (`[app]`: `mfa_required`, `signup_enabled`, `ai_enabled`) |
| Licences of included software | Start menu → FS-Platform → *Third-party notices* |

## Troubleshooting

- **The browser does not open / shows nothing:** use *Open FS-Platform* in the tray. If port 8746 is taken by another
  program, FS-Platform uses the next free port automatically and the tray opens the right address.
- **"FS-Platform could not start":** the message names the reason; details are in the log folder. Save diagnostics
  with FS-Platform Recovery and send the zip to support.
- **"Access is denied" for a colleague's Windows account:** add the account with FS-Platform Recovery, then sign out of
  Windows and back in.
- **"Account temporarily locked":** wait 15 minutes, or an administrator resets the password (*Admin → Users*).
- **"FS-Platform could not start: fsplatform.ini: …":** correct the value it names (`true` or `false`).
- **Authenticator codes are rejected** (authenticator switched on): check that the PC's clock is correct
  (*Settings → Time & language → Sync now*).

## Privacy and security

All data stays on this PC: FS-Platform listens on `127.0.0.1` only and makes no outbound connections. The optional
AI review is switched off; an administrator can turn it on in `fsplatform.ini` (and must enter an API key) — even then
every request is pseudonymised and needs a second person's approval before anything leaves the PC.

Without the authenticator, an account is only as safe as its password: anyone who can use this PC and knows (or
guesses) a password can sign in as that person, and anyone who can use the PC can create an account — which sees
nothing until an administrator gives it a role. Keep Windows accounts personal and locked when you step away, review
*Admin → Users* for accounts you do not recognise, and switch the authenticator on when many people use the PC.
