from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import csv, io, re, unicodedata
from collections import Counter

BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="Master List & Entry CSV Matcher", version="1.4.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
MAX_FILE_BYTES = 25 * 1024 * 1024
# Values that commonly represent missing/invalid candidate names or identifiers.
EMPTY_MARKERS = {"", "na", "n/a", "n.a.", "null", "none", "nil", "nan", "unknown", "-", "--", "not available", "not applicable"}


def is_usable_match_value(value: str) -> bool:
    """Reject blank cells and common spreadsheet placeholders as match keys."""
    value = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    return value not in EMPTY_MARKERS


def read_csv_upload(raw: bytes, filename: str):
    if not raw:
        raise ValueError(f"{filename} is empty.")
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError(f"{filename} exceeds the 25 MB limit.")
    text = None
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError(f"Could not read {filename}. Please save it as UTF-8 CSV.")
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text, newline=""), dialect=dialect)
    if not reader.fieldnames or not any((h or "").strip() for h in reader.fieldnames):
        raise ValueError(f"{filename} must contain a header row.")
    headers = [str(h or "").strip() for h in reader.fieldnames]
    if len(set(headers)) != len(headers):
        # Keep duplicate headers distinct so rows are not silently overwritten.
        counts = {}
        fixed = []
        for h in headers:
            counts[h] = counts.get(h, 0) + 1
            fixed.append(h if counts[h] == 1 else f"{h}_{counts[h]}")
        headers = fixed
    rows = []
    for row in reader:
        clean = {}
        for original, header in zip(reader.fieldnames, headers):
            clean[header] = (row.get(original) or "").strip()
        if any(v for v in clean.values()):
            rows.append(clean)
    return headers, rows


def normalize(value: str, mode: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    if mode == "email":
        return value.replace(" ", "")
    if mode == "phone":
        digits = re.sub(r"\D", "", value)
        # Treat Indian numbers with/without the +91 prefix as equivalent.
        if len(digits) == 12 and digits.startswith("91"):
            digits = digits[2:]
        return digits
    # General identifier/name matching: ignore punctuation and spacing.
    return re.sub(r"[^\w]", "", value, flags=re.UNICODE)


def likely_mode(header: str) -> str:
    h = re.sub(r"[^a-z]", "", header.casefold())
    if "email" in h or h in {"mail", "emailid"}:
        return "email"
    if any(x in h for x in ("phone", "mobile", "contactnumber", "whatsapp", "telephone")):
        return "phone"
    return "text"


@app.get("/", response_class=HTMLResponse)
def home():
    return (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/preview")
async def preview(file: UploadFile = File(...)):
