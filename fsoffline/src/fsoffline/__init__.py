"""FS-Platform offline edition (deployment Mode C, 04 §2).

The same web UI and engine as Mode A, packaged for one PC: SQLite instead of
PostgreSQL, a local object store instead of MinIO, an in-process worker, and a
server bound to the loopback interface only. Work moves to and from the firm's
server exclusively as signed, encrypted ``.fspkg`` engagement packages
(07 §4, E-DATA-05) — never by synchronising databases.
"""

__version__ = "1.0.1"
