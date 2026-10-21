"""FS-Platform Windows render worker (04 §3 "Windows render workers", E-ARCH-03).

A small, stateless service that runs on a Windows Server with licensed, pinned
Microsoft Excel. It heartbeats to the API, claims render jobs from the internal
`render-win` queue, exports the PDF through Excel COM (the export itself lives in
the engine: ``fscore.pdf.render_excel_com``), uploads the result and reports
failures. One Excel instance per job, guarded by a watchdog that kills only the
Excel processes this worker started.
"""

__version__ = "1.0.0"
