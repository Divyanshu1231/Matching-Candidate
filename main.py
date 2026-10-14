from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import csv, io, re, unicodedata
from collections import Counter

BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="Master List & Entry CSV Matcher", version="1.3.0")
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
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "Please upload a .csv file.")
    try:
        headers, rows = read_csv_upload(await file.read(), file.filename)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"filename": file.filename, "headers": headers, "row_count": len(rows), "preview": rows[:5]}


@app.post("/api/match")
async def match_csvs(
    first_file: UploadFile = File(...),
    second_file: UploadFile = File(...),
    first_key: str = Form(...),
    second_key: str = Form(...),
    match_type: str = Form("auto"),
    include_match_reason: bool = Form(False),
):
    for f in (first_file, second_file):
        if not f.filename or not f.filename.lower().endswith(".csv"):
            raise HTTPException(400, "Both uploads must be CSV files.")
    try:
        h1, rows1 = read_csv_upload(await first_file.read(), first_file.filename)
        h2, rows2 = read_csv_upload(await second_file.read(), second_file.filename)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if first_key not in h1:
        raise HTTPException(400, "Selected matching column was not found in the first CSV.")
    if second_key not in h2:
        raise HTTPException(400, "Selected matching column was not found in the second CSV.")
    if match_type not in {"auto", "text", "email", "phone"}:
        raise HTTPException(400, "Invalid match mode.")
    mode = likely_mode(first_key) if match_type == "auto" else match_type
    if mode == "text" and match_type == "auto":
        mode = likely_mode(second_key)
    # CSV 1 is the Master List; CSV 2 is the Entry CSV.
    # Export matching rows from CSV 2, preserving all Entry CSV columns.
    # Count each usable normalized name/key in both CSVs.
    # Export only keys that match between Master and Entry AND appear at least
    # twice in either file. This catches duplicate candidates without exporting
    # one-off name matches, while still excluding blank/placeholder values.
    reference_values = {}
    master_counts = Counter()

    # Count usable candidate names ONLY in the Master List.
    # A candidate qualifies only if their normalized name appears at least twice
    # in the Master List and also appears in the Entry CSV.
    for row in rows1:
        raw_value = row.get(first_key, "")
        if not is_usable_match_value(raw_value):
            continue
        key = normalize(raw_value, mode)
        if key:
            master_counts[key] += 1
            reference_values.setdefault(key, raw_value)

    matched = []
    for row in rows2:
        raw_value = row.get(second_key, "")
        # Never match blank values or placeholders such as NA/N/A/null.
        if not is_usable_match_value(raw_value):
            continue
        key = normalize(raw_value, mode)
        if key and key in reference_values and master_counts[key] >= 2:
            result_row = dict(row)
            if include_match_reason:
                result_row["Matched Reference Value"] = reference_values[key]
            matched.append(result_row)
    output = io.StringIO(newline="")
    export_headers = list(h2)
    if include_match_reason:
        detail_header = "Matched Reference Value"
        while detail_header in export_headers:
            detail_header += "_"
        if detail_header != "Matched Reference Value":
            for result_row in matched:
                result_row[detail_header] = result_row.pop("Matched Reference Value", "")
        export_headers.append(detail_header)
    writer = csv.DictWriter(output, fieldnames=export_headers, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(matched)
    payload = output.getvalue().encode("utf-8-sig")
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(second_file.filename).stem).strip("_") or "candidates"
    filename = f"matching_candidates_{safe_name}.csv"
    return StreamingResponse(
        io.BytesIO(payload), media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Matched-Rows": str(len(matched)),
            "X-First-Rows": str(len(rows1)),
            "X-Second-Rows": str(len(rows2)),
            "X-Match-Mode": mode,
        },
    )
