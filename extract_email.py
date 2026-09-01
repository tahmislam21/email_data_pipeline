"""
Gmail Data Extractor (optimized)
---------------------------------
Extracts email metadata from Gmail and writes it to a single CSV file.

Setup:
  1. pip install google-auth google-auth-oauthlib google-api-python-client tqdm
  2. Go to https://console.cloud.google.com
     - Create a project -> Enable Gmail API
     - Create OAuth 2.0 credentials (Desktop app) -> Download as credentials.json
  3. Place credentials.json in the same directory as this script
  4. Run: python gmail_extract.py
     (A browser window opens for Google sign-in on first run; token.json is saved for reuse)

Output:
  gmail_data.csv  -- one row per email

Speed notes (what changed vs. the original):
  - Messages are fetched using Gmail API *batch* requests (up to 100 messages per
    HTTP round trip) instead of one HTTP request per message. This is the single
    biggest speedup, since network round-trip latency (not CPU) was the bottleneck.
  - Batches are fetched *concurrently* with a thread pool (MAX_WORKERS batches
    in flight at once). Each worker thread gets its own Gmail service object,
    since the underlying HTTP client isn't thread-safe to share.
  - Headers are parsed into a dict once per message instead of doing a linear
    scan over the header list for every single field (from/to/cc/date/subject/...).
  - Thread bookkeeping (first/last message time, reply latency) used to re-sort
    every timestamp in a thread on every single message (O(n^2 log n) for a
    long thread). It now keeps a running sorted list via bisect.insort and reads
    first/last off the ends, which is much cheaper.
  - attachment counting is iterative instead of recursive.
  - Only ONE date/time column ("timestamp") is written instead of 14 separate
    derived columns (year, quarter, month, week, day_of_week, hour, minute,
    am_pm, time_bucket, ...). Any of those can be derived trivially from
    "timestamp" later in Excel/pandas if you need them, but computing and
    storing 14 columns per row for every email was pure overhead.

Tuning:
  - If you hit Gmail API rate limits (HTTP 429), lower MAX_WORKERS.
  - BATCH_SIZE is capped at 100 by the Gmail API itself.
"""
import csv
import os
import re
import bisect
import threading
from datetime import timezone
from email.utils import parsedate_to_datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from tqdm import tqdm

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
MAX_MESSAGES = None   # Set to None to extract everything (may take a while)
OUTPUT_FILE = "gmail_data.csv"
BATCH_SIZE = 2       # Gmail API batch endpoint allows up to 100 calls/request
MAX_WORKERS = 2     # concurrent in-flight batches (lower this if you see 429s)

EMAIL_RE = re.compile(r"[\w.\-+]+@[\w.\-]+")
ANGLE_EMAIL_RE = re.compile(r"<(.+?)>")
DOMAIN_RE = re.compile(r"@([\w.\-]+)")
DISPLAY_NAME_RE = re.compile(r"<.+>")

PERSONAL_DOMAINS = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
                     "icloud.com", "me.com", "protonmail.com", "live.com"}
FOLDER_PRIORITY = ["INBOX", "SENT", "DRAFT", "SPAM", "TRASH"]


# ── Auth ─────────────────────────────────────────────────────────────────────

def get_credentials():
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as f:
            f.write(creds.to_json())
    return creds


_thread_local = threading.local()

def get_thread_service(creds):
    """Each worker thread gets its own service/HTTP client (not thread-safe to share)."""
    if not hasattr(_thread_local, "service"):
        _thread_local.service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    return _thread_local.service


# ── Helpers ───────────────────────────────────────────────────────────────────

def header_dict(headers):
    """Build a lowercase-keyed dict in one pass instead of scanning the header
    list once per field (from/to/cc/date/subject/in-reply-to)."""
    d = {}
    for h in headers:
        name = h["name"].lower()
        if name not in d:  # keep first occurrence, matching original behavior
            d[name] = h["value"]
    return d


def parse_timestamp(raw_date):
    """Returns (display_string, sortable_datetime) or (None, None)."""
    if not raw_date:
        return None, None
    try:
        dt = parsedate_to_datetime(raw_date)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S"), dt
    except Exception:
        return None, None


def extract_domain(email_str):
    m = DOMAIN_RE.search(email_str or "")
    return m.group(1).lower() if m else ""


def classify_domain(domain):
    if not domain:
        return "unknown"
    return "personal" if domain in PERSONAL_DOMAINS else "work/other"


def count_attachments(payload):
    """Iterative walk of the MIME tree (avoids recursion overhead)."""
    count = 0
    stack = list(payload.get("parts", []) or [])
    while stack:
        p = stack.pop()
        if p.get("filename"):
            count += 1
        stack.extend(p.get("parts", []) or [])
    return count


def count_recipients(to_str, cc_str):
    def _count(s):
        return len([x for x in (s or "").split(",") if x.strip()])
    return _count(to_str) + _count(cc_str)


def get_labels_string(label_ids, label_map):
    return ", ".join(label_map.get(lid, lid) for lid in (label_ids or []))


def get_folder(label_ids):
    ids = set(label_ids or [])
    for p in FOLDER_PRIORITY:
        if p in ids:
            return p
    return "OTHER"


# ── Column definitions ────────────────────────────────────────────────────────

COLUMNS = [
    # Fact / IDs
    "message_id", "thread_id",
    # Sender
    "sender_raw", "sender_email", "sender_display_name", "sender_domain", "sender_domain_type",
    # Recipients
    "to_email", "cc", "recipient_count",
    # Content
    "subject",
    # Single date/time column (was 14 separate columns before)
    "timestamp",
    # Folder / label
    "folder", "all_labels",
    # Measures
    "size_bytes", "attachment_count", "has_attachments",
    "reply_latency_sec",
    # Boolean flags
    "is_read", "is_starred", "is_replied", "is_forwarded",
    # Thread dimension
    "thread_subject", "thread_message_count", "thread_first_message_at", "thread_last_message_at",
    "thread_duration_sec", "thread_participant_count",
]


# ── Fetching (batched + parallel) ─────────────────────────────────────────────

def fetch_all_message_ids(service, max_messages):
    ids = []
    page_token = None
    while True:
        resp = service.users().messages().list(
            userId="me", maxResults=500, pageToken=page_token
        ).execute()
        ids.extend(m["id"] for m in resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token or (max_messages and len(ids) >= max_messages):
            break
    return ids[:max_messages] if max_messages else ids


def fetch_labels(service):
    resp = service.users().labels().list(userId="me").execute()
    return {l["id"]: l["name"] for l in resp.get("labels", [])}


def chunked(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def fetch_messages_batch(creds, batch_ids):
    """Fetch up to BATCH_SIZE messages in a single HTTP round trip."""
    service = get_thread_service(creds)
    results = {}
    errors = []

    def _callback(request_id, response, exception):
        if exception is not None:
            errors.append((request_id, exception))
        else:
            results[request_id] = response

    batch = service.new_batch_http_request(callback=_callback)
    for mid in batch_ids:
        batch.add(
            service.users().messages().get(userId="me", id=mid, format="full"),
            request_id=mid,
        )
    batch.execute()

    for mid, exc in errors:
        print(f"\nSkipping {mid}: {exc}")

    return results


def fetch_all_messages(creds, msg_ids):
    """Fetch every message using concurrent batch requests."""
    messages = {}
    batches = list(chunked(msg_ids, BATCH_SIZE))

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(fetch_messages_batch, creds, b): b for b in batches}
        with tqdm(total=len(msg_ids), unit="msg", desc="Downloading") as pbar:
            for fut in as_completed(futures):
                batch_ids = futures[fut]
                messages.update(fut.result())
                pbar.update(len(batch_ids))

    # preserve original list-API ordering so thread bookkeeping below behaves
    # the same way it did in the sequential version
    return [messages[mid] for mid in msg_ids if mid in messages]


# ── Parsing ────────────────────────────────────────────────────────────────────

def parse_message(msg, label_map, thread_cache):
    payload = msg.get("payload", {})
    headers = header_dict(payload.get("headers", []))
    label_ids = msg.get("labelIds", [])

    raw_from = headers.get("from", "")
    raw_to = headers.get("to", "")
    raw_cc = headers.get("cc", "")
    raw_date = headers.get("date", "")
    subject = headers.get("subject", "")
    in_reply = headers.get("in-reply-to", "")

    m = ANGLE_EMAIL_RE.search(raw_from)
    sender_email = m.group(1) if m else raw_from.strip()
    sender_display = DISPLAY_NAME_RE.sub("", raw_from).strip().strip('"')
    sender_domain = extract_domain(raw_from)

    timestamp, raw_dt = parse_timestamp(raw_date)

    thread_id = msg.get("threadId", "")
    size_bytes = msg.get("sizeEstimate", 0)
    att_count = count_attachments(payload)

    t = thread_cache.get(thread_id)
    if t is None:
        t = thread_cache[thread_id] = {
            "subject": subject, "timestamps": [], "participants": set(),
            "message_count": 0,
        }

    # Reply latency: find the most recent prior timestamp in this thread.
    # thread_cache["timestamps"] is kept sorted via bisect.insort, so this is
    # a binary search instead of a full scan.
    reply_latency = None
    if in_reply and raw_dt and t["timestamps"]:
        idx = bisect.bisect_left(t["timestamps"], raw_dt)
        if idx > 0:
            reply_latency = int((raw_dt - t["timestamps"][idx - 1]).total_seconds())

    if raw_dt:
        bisect.insort(t["timestamps"], raw_dt)

    t["participants"].add(sender_email)
    t["participants"].update(EMAIL_RE.findall(raw_to))
    t["participants"].update(EMAIL_RE.findall(raw_cc))
    t["message_count"] += 1
    if not t["subject"]:
        t["subject"] = subject

    ts_list = t["timestamps"]
    thread_first = ts_list[0].strftime("%Y-%m-%d %H:%M") if ts_list else None
    thread_last = ts_list[-1].strftime("%Y-%m-%d %H:%M") if len(ts_list) > 1 else None
    thread_dur = int((ts_list[-1] - ts_list[0]).total_seconds()) if len(ts_list) > 1 else 0

    return {
        "message_id": msg["id"],
        "thread_id": thread_id,
        "sender_raw": raw_from,
        "sender_email": sender_email,
        "sender_display_name": sender_display,
        "sender_domain": sender_domain,
        "sender_domain_type": classify_domain(sender_domain),
        "to_email": raw_to,
        "cc": raw_cc,
        "recipient_count": count_recipients(raw_to, raw_cc),
        "subject": subject,
        "timestamp": timestamp,
        "folder": get_folder(label_ids),
        "all_labels": get_labels_string(label_ids, label_map),
        "size_bytes": size_bytes,
        "attachment_count": att_count,
        "has_attachments": att_count > 0,
        "reply_latency_sec": reply_latency,
        "is_read": "UNREAD" not in label_ids,
        "is_starred": "STARRED" in label_ids,
        "is_replied": bool(in_reply),
        "is_forwarded": subject.lower().startswith(("fwd:", "fw:")),
        "thread_subject": t["subject"],
        "thread_message_count": t["message_count"],
        "thread_first_message_at": thread_first,
        "thread_last_message_at": thread_last,
        "thread_duration_sec": thread_dur,
        "thread_participant_count": len(t["participants"]),
    }


# ── CSV writer ──────────────────────────────────────────────────────────────

def write_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n✓ Saved {len(rows)} rows -> {path}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    print("Authenticating with Gmail...")
    creds = get_credentials()
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)

    print("Fetching label map...")
    label_map = fetch_labels(service)

    print(f"Fetching message IDs (limit: {MAX_MESSAGES or 'all'})...")
    msg_ids = fetch_all_message_ids(service, MAX_MESSAGES)
    print(f"Found {len(msg_ids)} messages. "
          f"Downloading in batches of {BATCH_SIZE} with {MAX_WORKERS} parallel workers...")

    raw_messages = fetch_all_messages(creds, msg_ids)

    print("Parsing messages...")
    thread_cache = {}
    rows = [parse_message(msg, label_map, thread_cache) for msg in tqdm(raw_messages, unit="msg")]

    print("Writing CSV...")
    write_csv(rows, OUTPUT_FILE)


if __name__ == "__main__":
    main()