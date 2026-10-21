# Client guides

Printable one-page guides for clients, generated as PDFs.

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
