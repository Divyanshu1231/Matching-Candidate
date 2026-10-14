# Candidate CSV Matcher

A lightweight CSV matching web app. Upload a reference CSV (CSV 1), upload a candidate CSV (CSV 2), choose the corresponding match columns, and export only the matching rows from CSV 2.

## Features
- CSV upload and column preview before matching
- Select different matching columns in each file
- Auto-detect email/phone fields, or explicitly choose text, email, or phone normalization
- Email matching ignores case and spaces; phone matching strips punctuation and supports Indian numbers with/without `+91`; text/ID matching ignores spaces and punctuation
- Downloads matching rows from CSV 2, preserving its columns and order
- Files are processed in memory and are not saved to a database or disk
- 25 MB per-file limit
- Responsive interface

## Run locally (Windows PowerShell)

```powershell
cd candidate-matcher
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Open http://127.0.0.1:8000. Health check: http://127.0.0.1:8000/health

If PowerShell blocks activation, run this in the same terminal session:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

## Deploy to Render

1. Test locally first.
2. Create a GitHub repository and upload the contents of this folder (not the parent folder).
3. In Render, choose **New + → Blueprint** and connect the repository containing `render.yaml`, or create a **Web Service** manually.
4. Runtime: Python; Build command: `pip install -r requirements.txt`; Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`.
5. Deploy. Open the Render URL and confirm `/health` returns `{"status":"ok"}`.

This is a single-service deployment; the backend also serves the web UI. No database or environment variables are required.

## Matching behavior
The app treats CSV 1 as the reference set. It builds a set of non-empty normalized values from the selected column in CSV 1, then includes each row from CSV 2 whose selected-column value is in that set. Duplicate matching rows in CSV 2 are retained. It does not perform fuzzy/approximate name matching. Optionally, the export can include a `Matched Reference Value` column for review.

## Limits and privacy
- Maximum upload size: 25 MB per file.
- Uploaded content is processed in server memory for the request and not persisted by this app. As with any hosted service, the hosting provider may maintain standard infrastructure logs; avoid uploading data you are not authorized to process.
