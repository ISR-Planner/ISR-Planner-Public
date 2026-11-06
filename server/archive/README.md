# server/archive — Legacy Server Files

These files have been archived as part of the repository cleanup. They are **not used** in the production server and are preserved for historical reference only.

| File | Was | Reason archived |
|------|-----|-----------------|
| `main_v2.py` | Experimental second version of the FastAPI app | Zero active imports; superseded by `server/main.py` |
| `ui.py` | Tkinter-based desktop UI prototype | Pure tkinter, not part of the web server; zero active imports |

The active server entry point is `server/main.py`.
