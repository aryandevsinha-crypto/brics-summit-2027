"""BRICS Summit 2027 delegate site.

On this computer: python server.py
On an always-on host:
  BRICS_HOST=0.0.0.0 BRICS_PORT=$PORT BRICS_DATA=/data python server.py
Point BRICS_DATA at a persistent disk so the database, payment QR, and
receipt PDFs survive restarts. Do not commit the data directory.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
TEMPLATE = ROOT / "templates" / "page.html"
DATA = Path(os.environ.get("BRICS_DATA", str(ROOT / "data"))).expanduser().resolve()
DB_PATH = DATA / "summit.db"
QR_PATH = DATA / "payment-qr"
PASSES = DATA / "passes"
PROOFS = DATA / "proofs"
PASSWORD_FILE = DATA / "secretariat-password.txt"

HOST = os.environ.get("BRICS_HOST", "127.0.0.1")
PORT = int(os.environ.get("BRICS_PORT") or os.environ.get("PORT") or "8787")
SESSION_HOURS = 12
MAX_JSON = 80_000
MAX_UPLOAD = 2_000_000
MAX_PROOF = 8_000_000

COMMITTEES = [
    {
        "id": "plenary",
        "number": 1,
        "name": "BRICS Plenary / UN General Assembly (UNGA)",
        "short": "Plenary / UNGA",
        "agenda": "Reforming Global Governance, Multipolar Diplomacy & UN/IMF/World Bank Reforms.",
        "overview": (
            "This committee will deliberate on proposals for institutional reform across the "
            "United Nations, International Monetary Fund, and World Bank, with the goal of "
            "creating a more representative, accountable, and effective multilateral order."
        ),
        "gap": "The handbook numbers these portfolios 1–10 and 12–25.",
        "portfolios": [
            (1, "India (Chair)"),
            (2, "China"),
            (3, "Russia"),
            (4, "Brazil"),
            (5, "South Africa"),
            (6, "Iran"),
            (7, "Saudi Arabia"),
            (8, "UAE"),
            (9, "Egypt"),
            (10, "Ethiopia"),
            (12, "Malaysia"),
            (13, "Thailand"),
            (14, "Vietnam"),
            (15, "Türkiye"),
            (16, "Belarus"),
            (17, "Bolivia"),
            (18, "Cuba"),
            (19, "Nigeria"),
            (20, "Argentina"),
            (21, "USA (Observer)"),
            (22, "EU (Observer)"),
            (23, "Japan (Observer)"),
            (24, "Australia (Observer)"),
            (25, "African Union Chair"),
        ],
    },
    {
        "id": "trade",
        "number": 2,
        "name": "BRICS Economic & Trade Committee (UNCTAD/WTO)",
        "short": "Economic & Trade",
        "agenda": "De-dollarization, Local Currency Settlement, Supply Chains & SME Finance.",
        "overview": (
            "The BRICS Economic & Trade Committee (UNCTAD/WTO) will deliberate on building "
            "resilient and inclusive trade systems. Delegates will explore pathways for "
            "de-dollarization and local currency settlements, strengthen financial cooperation, "
            "and advance fair and transparent trade rules. The Committee will focus on enhancing "
            "supply-chain continuity, reducing trade frictions, and mobilizing finance for small "
            "and medium enterprises to drive innovation, employment, and sustainable growth across "
            "BRICS and partner economies."
        ),
        "gap": "The handbook numbers these portfolios 1–12 and 14–25.",
        "portfolios": [
            (1, "Minister of Trade/Commerce from India"),
            (2, "Minister of Trade/Commerce from China"),
            (3, "Minister of Trade/Commerce from Russia"),
            (4, "Minister of Trade/Commerce from Brazil"),
            (5, "Minister of Trade/Commerce from South Africa"),
            (6, "Minister of Trade/Commerce from Iran"),
            (7, "Minister of Trade/Commerce from Saudi Arabia"),
            (8, "Minister of Trade/Commerce from UAE"),
            (9, "Minister of Trade/Commerce from Egypt"),
            (10, "Minister of Trade/Commerce from Ethiopia"),
            (11, "Minister of Trade/Commerce from Indonesia"),
            (12, "Minister of Trade/Commerce from Malaysia"),
            (14, "Minister of Trade/Commerce from Vietnam"),
            (15, "Minister of Trade/Commerce from Belarus"),
            (16, "Minister of Trade/Commerce from Bolivia"),
            (17, "Minister of Trade/Commerce from Cuba"),
            (18, "President, New Development Bank (NDB)"),
            (19, "Director-General, WTO"),
            (20, "Managing Director, IMF"),
            (21, "President, World Bank"),
            (22, "Chair, BRICS Business Council"),
            (23, "US Commerce Secretary"),
            (24, "EU Trade Commissioner"),
            (25, "RBI Governor"),
        ],
    },
    {
        "id": "energy",
        "number": 3,
        "name": "Committee on Energy Security & Sustainable Development",
        "short": "Energy & Development",
        "agenda": "West Asia Dynamics, Oil Volatility, Shipping Security & Green Transition.",
        "overview": (
            "This committee examines the complex interplay between energy security, geopolitical "
            "risk, and sustainable development. Delegates will deliberate on West Asia dynamics, "
            "oil price volatility, maritime chokepoint security, supply chain resilience, and the "
            "imperative of a just and inclusive green transition that balances energy access, "
            "affordability, and climate goals."
        ),
        "gap": "",
        "portfolios": [
            (1, "Energy Minister of India"),
            (2, "Energy Minister of China"),
            (3, "Energy Minister of Russia"),
            (4, "Energy Minister of Brazil"),
            (5, "Energy Minister of South Africa"),
            (6, "Energy Minister of Iran"),
            (7, "Energy Minister of Saudi Arabia"),
            (8, "Energy Minister of UAE"),
            (9, "Energy Minister of Egypt"),
            (10, "Energy Minister of Ethiopia"),
            (11, "Energy Minister of Indonesia"),
            (12, "Energy Minister of Malaysia"),
            (13, "Energy Minister of Thailand"),
            (14, "Energy Minister of Vietnam"),
            (15, "Energy Minister of Bolivia"),
            (16, "Energy Minister of Cuba"),
            (17, "Energy Minister of Australia"),
            (18, "US Energy Secretary"),
            (19, "OPEC Secretary-General"),
            (20, "IEA Director"),
            (21, "UNFCCC Secretary"),
            (22, "CEO of Saudi Aramco"),
            (23, "CEO of Gazprom"),
            (24, "CEO of IOCL"),
            (25, "CEO of CNPC"),
        ],
    },
    {
        "id": "tech",
        "number": 4,
        "name": "Committee on Science, Technology & Innovation",
        "short": "Science & Innovation",
        "agenda": "Ethical AI Frameworks, Space Research & Digital Payment Infrastructure.",
        "overview": (
            "Committee 4 focuses on advancing responsible innovation for inclusive and sustainable "
            "development. Delegates will deliberate on ethical AI governance, strengthening "
            "scientific collaboration, expanding space research cooperation, and building trusted "
            "digital public infrastructure that is secure, interoperable, and accessible to all."
        ),
        "gap": "",
        "portfolios": [
            (1, "Tech/Science Minister of India"),
            (2, "Tech/Science Minister of China"),
            (3, "Tech/Science Minister of Russia"),
            (4, "Tech/Science Minister of Brazil"),
            (5, "Tech/Science Minister of South Africa"),
            (6, "Tech/Science Minister of Iran"),
            (7, "Tech/Science Minister of Saudi Arabia"),
            (8, "Tech/Science Minister of UAE"),
            (9, "Tech/Science Minister of Egypt"),
            (10, "Tech/Science Minister of Ethiopia"),
            (11, "Tech/Science Minister of Indonesia"),
            (12, "Tech/Science Minister of Malaysia"),
            (13, "Tech/Science Minister of Thailand"),
            (14, "Tech/Science Minister of Vietnam"),
            (15, "Tech/Science Minister of Belarus"),
            (16, "Head of ISRO"),
            (17, "Head of CNSA"),
            (18, "Head of Roscosmos"),
            (19, "Head of UN CSTD"),
            (20, "Head of WIPO"),
            (21, "US Chief Technology Officer"),
            (22, "Head of EU Innovation"),
            (23, "Head of Japan Digital"),
            (24, "Head of BRICS Network University"),
            (25, "Head of BRICS Tech Center"),
        ],
    },
]

COMMITTEE_BY_ID = {item["id"]: item for item in COMMITTEES}
PORTFOLIO_INDEX = {}
for committee in COMMITTEES:
    for number, title in committee["portfolios"]:
        PORTFOLIO_INDEX[f"{committee['id']}-{number}"] = {
            "id": f"{committee['id']}-{number}",
            "committeeId": committee["id"],
            "number": number,
            "title": title,
        }

PAGES = {
    "/": ("home", "BRICS Summit 2027", "Summit"),
    "/leadership": ("leadership", "Messages from leadership", "Leadership"),
    "/committees": ("committees", "Committees and portfolios", "Committees"),
    "/schedule": ("schedule", "Schedule and rules", "Schedule"),
    "/register": ("register", "Delegate registration", "Register"),
    "/status": ("status", "Find your registration", "Your place"),
    "/proof": ("proof", "Upload payment proof", "Payment proof"),
    "/admin": ("admin", "Portfolio desk", "Admin"),
    "/secretariat": ("secretariat", "Secretariat desk", "Secretariat"),
}

NAV = [
    ("/", "Summit"),
    ("/leadership", "Leadership"),
    ("/committees", "Committees"),
    ("/schedule", "Schedule"),
    ("/register", "Register"),
    ("/proof", "Payment proof"),
    ("/status", "Your place"),
]

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
LOGIN_FAILS: dict[str, list[float]] = {}
FAIL_LOCK = threading.Lock()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime | None = None) -> str:
    return (moment or now_utc()).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def put_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def hash_password(password: str, salt_hex: str | None = None) -> tuple[str, str]:
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return salt.hex(), digest.hex()


def check_password(password: str, salt_hex: str, digest_hex: str) -> bool:
    _, candidate = hash_password(password, salt_hex)
    return hmac.compare_digest(candidate, digest_hex)


def init_db() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    PASSES.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS registrations (
                id INTEGER PRIMARY KEY,
                code TEXT NOT NULL UNIQUE,
                claim_token TEXT NOT NULL UNIQUE,
                full_name TEXT NOT NULL,
                email TEXT NOT NULL,
                mobile TEXT NOT NULL,
                school TEXT NOT NULL,
                class_year TEXT NOT NULL,
                city TEXT NOT NULL,
                committee_id TEXT NOT NULL,
                portfolio_id TEXT UNIQUE,
                alternate_portfolio_id TEXT,
                experience TEXT NOT NULL DEFAULT '',
                emergency_name TEXT NOT NULL,
                emergency_phone TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                payment_status TEXT NOT NULL DEFAULT 'unpaid',
                payment_utr TEXT NOT NULL DEFAULT '',
                payer_name TEXT NOT NULL DEFAULT '',
                payment_reject_reason TEXT NOT NULL DEFAULT '',
                fee_rupees_at_submit INTEGER,
                payment_submitted_at TEXT,
                payment_reviewed_at TEXT,
                pass_qr_ext TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_reg_email ON registrations (email, status);
            CREATE TABLE IF NOT EXISTS payment_proofs (
                id INTEGER PRIMARY KEY,
                full_name TEXT NOT NULL,
                email TEXT NOT NULL,
                mobile TEXT NOT NULL,
                committee_id TEXT NOT NULL,
                portfolio_id TEXT,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            """
        )
        PROOFS.mkdir(parents=True, exist_ok=True)
        if not setting(conn, "admin_password_hash"):
            password = os.environ.get("BRICS_ADMIN_PASSWORD", "").strip() or secrets.token_urlsafe(9)
            salt, digest = hash_password(password)
            put_setting(conn, "admin_password_salt", salt)
            put_setting(conn, "admin_password_hash", digest)
            PASSWORD_FILE.write_text(
                "BRICS Summit 2027 secretariat password\n"
                f"{password}\n"
                "Change this after you sign in. Delete this file once you have saved the new password.\n",
                encoding="utf-8",
            )
            print(f"Secretariat password (also saved in {PASSWORD_FILE}): {password}")
        if not setting(conn, "registration_open"):
            put_setting(conn, "registration_open", "1")
        conn.commit()


def qr_file() -> Path | None:
    for ext in ("png", "jpeg", "webp", "gif"):
        path = QR_PATH.with_suffix("." + ext)
        if path.is_file():
            return path
    return None


def pass_file(reg_id: int, ext: str | None) -> Path | None:
    if not ext:
        return None
    path = PASSES / f"{reg_id}.{ext}"
    return path if path.is_file() else None


def image_kind(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    return None


MEDIA = {"png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp", "gif": "image/gif"}
FORM_MESSAGE = (
    "Paste a Google Form link that starts with https://docs.google.com/forms/ "
    "or https://forms.gle/."
)


def normalize_google_form(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    host = parsed.netloc.lower().removeprefix("www.")
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ValueError(FORM_MESSAGE)
    if host == "forms.gle":
        if not re.fullmatch(r"/[A-Za-z0-9_-]{4,80}", parsed.path):
            raise ValueError(FORM_MESSAGE)
        return "https://forms.gle" + parsed.path
    if host != "docs.google.com":
        raise ValueError(FORM_MESSAGE)
    if not re.fullmatch(r"/forms/d(?:/e)?/[A-Za-z0-9_-]+/(?:viewform|edit)", parsed.path):
        raise ValueError(FORM_MESSAGE)
    path = parsed.path[:-5] + "/viewform" if parsed.path.endswith("/edit") else parsed.path
    return "https://docs.google.com" + path


def embed_google_form(url: str) -> str:
    if url.startswith("https://docs.google.com/forms/"):
        return url + "?embedded=true"
    return ""


def public_event(conn: sqlite3.Connection) -> dict:
    fee_raw = setting(conn, "fee_rupees").strip()
    fee = int(fee_raw) if fee_raw.isdigit() else None
    return {
        "feeRupees": fee,
        "paymentNote": setting(conn, "payment_note"),
        "qrReady": qr_file() is not None,
        "registrationOpen": setting(conn, "registration_open", "1") == "1",
    }


def catalog(conn: sqlite3.Connection) -> dict:
    taken = {
        row["portfolio_id"]
        for row in conn.execute(
            "SELECT portfolio_id FROM registrations WHERE status = 'active' AND portfolio_id IS NOT NULL"
        )
    }
    committees = []
    open_seats = 0
    for committee in COMMITTEES:
        portfolios = []
        for number, title in committee["portfolios"]:
            portfolio_id = f"{committee['id']}-{number}"
            is_open = portfolio_id not in taken
            if is_open:
                open_seats += 1
            portfolios.append(
                {"id": portfolio_id, "number": number, "title": title, "open": is_open}
            )
        form_url = setting(conn, "form_url_" + committee["id"])
        committees.append(
            {
                "id": committee["id"],
                "number": committee["number"],
                "name": committee["name"],
                "short": committee["short"],
                "agenda": committee["agenda"],
                "overview": committee["overview"],
                "gap": committee["gap"],
                "portfolios": portfolios,
                "openCount": sum(1 for item in portfolios if item["open"]),
                "seatCount": len(portfolios),
                "formUrl": form_url,
                "formEmbedUrl": embed_google_form(form_url),
            }
        )
    active = conn.execute("SELECT COUNT(*) AS n FROM registrations WHERE status = 'active'").fetchone()["n"]
    return {"event": public_event(conn), "committees": committees, "openSeats": open_seats, "delegateCount": active}


def owner_view(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    committee = COMMITTEE_BY_ID[row["committee_id"]]
    portfolio = PORTFOLIO_INDEX.get(row["portfolio_id"] or "")
    alternate = PORTFOLIO_INDEX.get(row["alternate_portfolio_id"] or "")
    event = public_event(conn)
    return {
        "id": row["id"],
        "code": row["code"],
        "claimToken": row["claim_token"],
        "fullName": row["full_name"],
        "email": row["email"],
        "mobile": row["mobile"],
        "school": row["school"],
        "classYear": row["class_year"],
        "city": row["city"],
        "committeeId": committee["id"],
        "committeeName": committee["name"],
        "committeeShort": committee["short"],
        "portfolio": None
        if not portfolio
        else {"id": portfolio["id"], "number": portfolio["number"], "title": portfolio["title"]},
        "alternate": None
        if not alternate
        else {"id": alternate["id"], "number": alternate["number"], "title": alternate["title"]},
        "experience": row["experience"],
        "emergencyName": row["emergency_name"],
        "emergencyPhone": row["emergency_phone"],
        "notes": row["notes"],
        "status": row["status"],
        "paymentStatus": row["payment_status"],
        "paymentUtr": row["payment_utr"],
        "payerName": row["payer_name"],
        "rejectReason": row["payment_reject_reason"],
        "feeAtSubmit": row["fee_rupees_at_submit"],
        "paymentSubmittedAt": row["payment_submitted_at"],
        "paymentReviewedAt": row["payment_reviewed_at"],
        "passQrReady": pass_file(row["id"], row["pass_qr_ext"]) is not None,
        "createdAt": row["created_at"],
        "event": event,
    }


def clean_text(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def flagged(value: object) -> bool:
    return clean_text(value, 12).lower() in {"1", "true", "on", "yes"}


def clean_phone(value: object) -> str:
    raw = str(value or "").strip()
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+"):
        return "+" + digits
    return digits


def valid_phone(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    return 10 <= len(digits) <= 15


def make_code(conn: sqlite3.Connection) -> str:
    while True:
        body = "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        code = f"BS2027-{body}"
        exists = conn.execute("SELECT 1 FROM registrations WHERE code = ?", (code,)).fetchone()
        if not exists:
            return code


def read_json(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0 or length > MAX_JSON:
        raise ValueError("Request body is missing or too large.")
    payload = json.loads(handler.rfile.read(length).decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Expected a JSON object.")
    return payload


def parse_multipart(body: bytes, content_type: str) -> dict:
    marker = content_type.split("boundary=", 1)[1].strip().strip('"')
    delimiter = ("--" + marker).encode("utf-8", "replace")
    fields: dict[str, str] = {}
    files: dict[str, dict] = {}
    for part in body.split(delimiter):
        if part.startswith(b"\r\n"):
            part = part[2:]
        if part.endswith(b"\r\n"):
            part = part[:-2]
        if not part or part == b"--":
            continue
        if part.endswith(b"--"):
            part = part[:-2].rstrip(b"\r\n")
        header_blob, sep, content = part.partition(b"\r\n\r\n")
        if not sep:
            continue
        disposition = ""
        for line in header_blob.decode("utf-8", "replace").split("\r\n"):
            if line.lower().startswith("content-disposition:"):
                disposition = line.split(":", 1)[1]
        name = filename = None
        for item in disposition.split(";"):
            item = item.strip()
            if item.startswith("name="):
                name = item.split("=", 1)[1].strip().strip('"')
            elif item.startswith("filename="):
                filename = item.split("=", 1)[1].strip().strip('"')
        if not name:
            continue
        if filename is None:
            fields[name] = content.decode("utf-8", "replace").strip()
        else:
            files[name] = {"filename": filename, "data": content}
    return {"fields": fields, "files": files}


def save_image(data: bytes, destination: Path) -> str:
    if len(data) < 32 or len(data) > MAX_UPLOAD:
        raise ValueError("Use an image under 2 MB.")
    kind = image_kind(data)
    if not kind:
        raise ValueError("Use a PNG, JPEG, WEBP, or GIF.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    for existing in destination.parent.glob(destination.name + ".*"):
        existing.unlink(missing_ok=True)
    target = destination.with_suffix("." + kind)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(target)
    return kind


def client_ip(handler: BaseHTTPRequestHandler) -> str:
    return handler.client_address[0]


def login_blocked(ip: str) -> bool:
    with FAIL_LOCK:
        recent = [stamp for stamp in LOGIN_FAILS.get(ip, []) if stamp > now_utc().timestamp() - 900]
        LOGIN_FAILS[ip] = recent
        return len(recent) >= 8


def note_login_fail(ip: str) -> None:
    with FAIL_LOCK:
        LOGIN_FAILS.setdefault(ip, []).append(now_utc().timestamp())


def clear_login_fail(ip: str) -> None:
    with FAIL_LOCK:
        LOGIN_FAILS.pop(ip, None)


class Handler(BaseHTTPRequestHandler):
    server_version = "BRICSSummit/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def do_GET(self) -> None:
        self.route("GET")

    def do_POST(self) -> None:
        self.route("POST")

    def do_DELETE(self) -> None:
        self.route("DELETE")

    def route(self, method: str) -> None:
        try:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            if path != "/" and path.endswith("/"):
                path = path[:-1]
            query = parse_qs(parsed.query)
            if path.startswith("/api/"):
                self.api(method, path, query)
                return
            if method != "GET":
                self.send_error_json(405, "Method not allowed.")
                return
            self.page(path)
        except BrokenPipeError:
            return
        except Exception as exc:  # noqa: BLE001 — last-resort response for unexpected faults
            self.send_error_json(500, "The desk could not complete that request.")
            print("ERROR", type(exc).__name__, exc)

    def page(self, path: str) -> None:
        if self.serve_static(path):
            return
        dynamic = None
        if path.startswith("/pay/"):
            dynamic = ("pay", "Pay your delegate fee", "Payment")
        elif path.startswith("/pass/"):
            dynamic = ("delegate", "Your delegate pass", "Your place")
        spec = PAGES.get(path) or dynamic
        if not spec:
            spec = ("missing", "Page not found", "")
            body = (PUBLIC / "missing.html").read_text(encoding="utf-8")
            self.send_html(self.wrap(spec, body), 404)
            return
        fragment = PUBLIC / f"{spec[0]}.html"
        if not fragment.is_file():
            self.send_error_json(404, "That page is not on this site.")
            return
        self.send_html(self.wrap(spec, fragment.read_text(encoding="utf-8")))

    def wrap(self, spec: tuple[str, str, str], body: str) -> str:
        page_id, title, current = spec
        links = []
        for href, label in NAV:
            current_attr = ' aria-current="page"' if label == current else ""
            links.append(f'<a href="{href}"{current_attr}>{label}</a>')
        layout = TEMPLATE.read_text(encoding="utf-8")
        full_title = title if title.startswith("BRICS") else f"{title} · BRICS Summit 2027"
        script = ""
        if (PUBLIC / "js" / f"{page_id}.js").is_file():
            script = f'<script src="/js/{page_id}.js"></script>'
        return (
            layout.replace("__TITLE__", full_title)
            .replace("__PAGE__", page_id)
            .replace("__NAV__", "\n".join(links))
            .replace("__BODY__", body)
            .replace("__SCRIPT__", script)
        )

    def serve_static(self, path: str) -> bool:
        if path.startswith("/api/") or ".." in path:
            return False
        candidate = (PUBLIC / path.lstrip("/")).resolve()
        if not candidate.is_file() or not candidate.is_relative_to(PUBLIC.resolve()):
            return False
        types = {
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".webp": "image/webp",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".ico": "image/x-icon",
        }
        self.send_bytes(candidate.read_bytes(), 200, types.get(candidate.suffix.lower(), "application/octet-stream"))
        return True

    def api(self, method: str, path: str, query: dict) -> None:
        if method in {"POST", "DELETE"} and not self.origin_ok():
            self.send_error_json(403, "This request was refused.")
            return
        if path == "/api/catalog" and method == "GET":
            with connect() as conn:
                self.send_json(catalog(conn))
            return
        if path == "/api/payment-qr" and method == "GET":
            self.send_qr(qr_file())
            return
        if path == "/api/register" and method == "POST":
            self.register()
            return
        if path == "/api/registration" and method == "GET":
            self.lookup(query)
            return
        if path.startswith("/api/pass-qr/") and method == "GET":
            self.send_pass_qr(path.removeprefix("/api/pass-qr/"))
            return
        if path.startswith("/api/pass/") and method == "GET":
            token = path.removeprefix("/api/pass/")
            self.pass_by_token(token)
            return
        if path == "/api/payment" and method == "POST":
            self.submit_payment()
            return
        if path == "/api/proof" and method == "POST":
            self.submit_proof()
            return
        if path == "/api/secretariat/login" and method == "POST":
            self.login()
            return
        if path == "/api/secretariat/logout" and method == "POST":
            self.logout()
            return
        if not self.session_ok():
            self.send_error_json(401, "Sign in at the secretariat desk.")
            return
        if path == "/api/secretariat/session" and method == "GET":
            self.send_json({"ok": True})
            return
        if path == "/api/admin/board" and method == "GET":
            self.admin_board()
            return
        if path.startswith("/api/admin/proof/") and method == "GET":
            self.send_proof(path.removeprefix("/api/admin/proof/"))
            return
        if path == "/api/admin/proof" and method == "DELETE":
            self.remove_proof()
            return
        if path == "/api/secretariat/desk" and method == "GET":
            self.desk()
            return
        if path == "/api/secretariat/fee" and method == "POST":
            self.save_fee()
            return
        if path == "/api/secretariat/registration-window" and method == "POST":
            self.save_window()
            return
        if path == "/api/secretariat/forms" and method == "POST":
            self.save_forms()
            return
        if path == "/api/secretariat/qr" and method == "POST":
            self.upload_payment_qr()
            return
        if path == "/api/secretariat/qr" and method == "DELETE":
            self.clear_payment_qr()
            return
        if path == "/api/secretariat/payment" and method == "POST":
            self.review_payment()
            return
        if path == "/api/secretariat/reassign" and method == "POST":
            self.reassign()
            return
        if path == "/api/secretariat/pass-qr" and method == "POST":
            self.upload_pass_qr()
            return
        if path == "/api/secretariat/pass-qr" and method == "DELETE":
            self.clear_pass_qr()
            return
        if path == "/api/secretariat/cancel" and method == "POST":
            self.cancel_registration()
            return
        if path == "/api/secretariat/password" and method == "POST":
            self.change_password()
            return
        if path == "/api/secretariat/export.csv" and method == "GET":
            self.export_csv()
            return
        self.send_error_json(404, "Unknown desk action.")

    def origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return urlparse(origin).netloc == (self.headers.get("Host") or "")

    def session_token(self) -> str:
        cookie = self.headers.get("Cookie") or ""
        for part in cookie.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "brics_session":
                return value
        return ""

    def session_ok(self) -> bool:
        token = self.session_token()
        if not token:
            return False
        with connect() as conn:
            row = conn.execute("SELECT expires_at FROM sessions WHERE token = ?", (token,)).fetchone()
            if not row:
                return False
            if row["expires_at"] <= iso():
                conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
                conn.commit()
                return False
        return True

    def register(self) -> None:
        parsed = self.read_upload_with_fields(
            "proof",
            limit=MAX_PROOF,
            missing="Attach a PDF of your Paytm or bank payment proof, up to 8 MB. Registration is only accepted with that PDF.",
        )
        if parsed is None:
            return
        fields, data = parsed
        if len(data) < 50 or not data.startswith(b"%PDF-"):
            self.send_error_json(
                400,
                "The payment proof has to be a PDF from Paytm or your bank. Registration is only accepted with that PDF.",
            )
            return
        full_name = clean_text(fields.get("fullName"), 120)
        email = clean_text(fields.get("email"), 160).lower()
        mobile = clean_phone(fields.get("mobile"))
        school = clean_text(fields.get("school"), 160)
        class_year = clean_text(fields.get("classYear"), 40)
        city = clean_text(fields.get("city"), 80)
        committee_id = clean_text(fields.get("committeeId"), 40)
        portfolio_id = clean_text(fields.get("portfolioId"), 40)
        alternate_id = clean_text(fields.get("alternatePortfolioId"), 40)
        experience = clean_text(fields.get("experience"), 600)
        emergency_name = clean_text(fields.get("emergencyName"), 120)
        emergency_phone = clean_phone(fields.get("emergencyPhone"))
        notes = clean_text(fields.get("notes"), 600)
        conduct = flagged(fields.get("conductAccepted"))
        dress = flagged(fields.get("dressAccepted"))
        errors = []
        if len(full_name) < 3:
            errors.append("Enter the delegate's full name.")
        if not EMAIL_RE.match(email):
            errors.append("Enter a working email address.")
        if not valid_phone(mobile):
            errors.append("Enter a mobile number with 10 to 15 digits.")
        if len(school) < 2:
            errors.append("Enter the school or institution.")
        if len(class_year) < 1:
            errors.append("Enter the class or year.")
        if len(city) < 2:
            errors.append("Enter the city.")
        if committee_id not in COMMITTEE_BY_ID:
            errors.append("Choose a committee from the handbook.")
        portfolio = PORTFOLIO_INDEX.get(portfolio_id)
        if not portfolio or portfolio["committeeId"] != committee_id:
            errors.append("Choose an open portfolio in that committee.")
        alternate = PORTFOLIO_INDEX.get(alternate_id) if alternate_id else None
        if alternate_id and (not alternate or alternate["committeeId"] != committee_id):
            errors.append("The alternate portfolio has to be in the same committee.")
        if alternate_id and alternate_id == portfolio_id:
            errors.append("The alternate has to be a different portfolio.")
        if len(emergency_name) < 3:
            errors.append("Enter an emergency contact name.")
        if not valid_phone(emergency_phone):
            errors.append("Enter an emergency contact number.")
        if not conduct or not dress:
            errors.append("Accept the code of conduct and the formal dress code.")
        if errors:
            self.send_json({"ok": False, "errors": errors}, 400)
            return
        proof_path = None
        committed = False
        try:
            with connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                if setting(conn, "registration_open", "1") != "1":
                    conn.rollback()
                    self.send_error_json(403, "Registration is closed.")
                    return
                existing = conn.execute(
                    "SELECT 1 FROM registrations WHERE email = ? AND status = 'active'",
                    (email,),
                ).fetchone()
                if existing:
                    conn.rollback()
                    self.send_error_json(409, "This email already has an active registration.")
                    return
                held = conn.execute(
                    "SELECT 1 FROM registrations WHERE portfolio_id = ? AND status = 'active'",
                    (portfolio_id,),
                ).fetchone()
                if held:
                    conn.rollback()
                    self.send_error_json(409, "That portfolio was just reserved. Choose another open seat.")
                    return
                code = make_code(conn)
                claim = secrets.token_urlsafe(32)
                conn.execute(
                    """
                    INSERT INTO registrations (
                        code, claim_token, full_name, email, mobile, school, class_year, city,
                        committee_id, portfolio_id, alternate_portfolio_id, experience,
                        emergency_name, emergency_phone, notes, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        code,
                        claim,
                        full_name,
                        email,
                        mobile,
                        school,
                        class_year,
                        city,
                        committee_id,
                        portfolio_id,
                        alternate_id or None,
                        experience,
                        emergency_name,
                        emergency_phone,
                        notes,
                        iso(),
                    ),
                )
                proof_cursor = conn.execute(
                    """
                    INSERT INTO payment_proofs (
                        full_name, email, mobile, committee_id, portfolio_id, note, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (full_name, email, mobile, committee_id, portfolio_id, notes, iso()),
                )
                proof_id = int(proof_cursor.lastrowid)
                proof_path = PROOFS / f"{proof_id}.pdf"
                try:
                    PROOFS.mkdir(parents=True, exist_ok=True)
                    proof_path.write_bytes(data)
                except OSError:
                    conn.rollback()
                    proof_path.unlink(missing_ok=True)
                    self.send_error_json(500, "The payment proof could not be stored. Registration was not saved.")
                    return
                row = conn.execute("SELECT * FROM registrations WHERE code = ?", (code,)).fetchone()
                conn.commit()
                committed = True
                self.send_json({"ok": True, "registration": owner_view(conn, row)})
        except sqlite3.IntegrityError:
            self.send_error_json(409, "That portfolio was just reserved. Choose another open seat.")
        finally:
            if proof_path is not None and not committed:
                proof_path.unlink(missing_ok=True)

    def lookup(self, query: dict) -> None:
        code = clean_text((query.get("code") or [""])[0], 40).upper()
        email = clean_text((query.get("email") or [""])[0], 160).lower()
        if not code or not email:
            self.send_error_json(400, "Enter the registration code and email.")
            return
        with connect() as conn:
            row = conn.execute(
                "SELECT * FROM registrations WHERE code = ? AND email = ?",
                (code, email),
            ).fetchone()
            if not row:
                self.send_error_json(404, "No registration matches that code and email.")
                return
            self.send_json({"ok": True, "registration": owner_view(conn, row)})

    def pass_by_token(self, token: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_\-]{20,80}", token or ""):
            self.send_error_json(404, "This delegate link is not valid.")
            return
        with connect() as conn:
            row = conn.execute("SELECT * FROM registrations WHERE claim_token = ?", (token,)).fetchone()
            if not row:
                self.send_error_json(404, "This delegate link is not valid.")
                return
            self.send_json({"ok": True, "registration": owner_view(conn, row)})

    def submit_payment(self) -> None:
        try:
            payload = read_json(self)
        except (ValueError, json.JSONDecodeError):
            self.send_error_json(400, "The payment details could not be read.")
            return
        token = clean_text(payload.get("claimToken"), 80)
        utr = clean_text(payload.get("utr"), 40).upper()
        payer = clean_text(payload.get("payerName"), 120)
        if not re.fullmatch(r"[A-Z0-9]{6,30}", utr):
            self.send_error_json(400, "Enter the UPI or bank reference: 6 to 30 letters or digits.")
            return
        if len(payer) < 3:
            self.send_error_json(400, "Enter the name of the person who paid.")
            return
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM registrations WHERE claim_token = ?", (token,)).fetchone()
            if not row or row["status"] != "active":
                conn.rollback()
                self.send_error_json(404, "This registration cannot take a payment.")
                return
            if row["payment_status"] == "confirmed":
                conn.rollback()
                self.send_error_json(409, "This payment is already confirmed.")
                return
            event = public_event(conn)
            if event["feeRupees"] is None:
                conn.rollback()
                self.send_error_json(409, "The secretariat has not published the fee yet.")
                return
            if not event["qrReady"]:
                conn.rollback()
                self.send_error_json(409, "The payment QR is not in its slot yet.")
                return
            conn.execute(
                """
                UPDATE registrations
                SET payment_status = 'submitted', payment_utr = ?, payer_name = ?,
                    payment_reject_reason = '', fee_rupees_at_submit = ?,
                    payment_submitted_at = ?, payment_reviewed_at = NULL
                WHERE id = ?
                """,
                (utr, payer, event["feeRupees"], iso(), row["id"]),
            )
            updated = conn.execute("SELECT * FROM registrations WHERE id = ?", (row["id"],)).fetchone()
            conn.commit()
            self.send_json({"ok": True, "registration": owner_view(conn, updated)})

    def login(self) -> None:
        ip = client_ip(self)
        if login_blocked(ip):
            self.send_error_json(429, "Too many attempts. Wait fifteen minutes and try again.")
            return
        try:
            payload = read_json(self)
        except (ValueError, json.JSONDecodeError):
            self.send_error_json(400, "Enter the secretariat password.")
            return
        password = str(payload.get("password") or "")
        with connect() as conn:
            salt = setting(conn, "admin_password_salt")
            digest = setting(conn, "admin_password_hash")
            if not salt or not digest or not check_password(password, salt, digest):
                note_login_fail(ip)
                self.send_error_json(401, "That password does not match the secretariat desk.")
                return
            clear_login_fail(ip)
            token = secrets.token_urlsafe(32)
            expires = iso(now_utc() + timedelta(hours=SESSION_HOURS))
            conn.execute("INSERT INTO sessions (token, expires_at) VALUES (?, ?)", (token, expires))
            conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (iso(),))
            conn.commit()
        self.send_json(
            {"ok": True},
            extra_headers={"Set-Cookie": f"brics_session={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={SESSION_HOURS * 3600}"},
        )

    def logout(self) -> None:
        token = self.session_token()
        if token:
            with connect() as conn:
                conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
                conn.commit()
        self.send_json(
            {"ok": True},
            extra_headers={"Set-Cookie": "brics_session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0"},
        )

    def desk(self) -> None:
        with connect() as conn:
            rows = conn.execute("SELECT * FROM registrations ORDER BY id DESC").fetchall()
            self.send_json(
                {
                    "event": public_event(conn),
                    "catalog": catalog(conn),
                    "registrations": [owner_view(conn, row) for row in rows],
                }
            )

    def save_fee(self) -> None:
        try:
            payload = read_json(self)
        except (ValueError, json.JSONDecodeError):
            self.send_error_json(400, "The fee could not be read.")
            return
        note = clean_text(payload.get("paymentNote"), 500)
        clear = bool(payload.get("clearFee"))
        with connect() as conn:
            if clear:
                put_setting(conn, "fee_rupees", "")
            else:
                amount = payload.get("feeRupees")
                if isinstance(amount, bool) or not isinstance(amount, int) or not 1 <= amount <= 100000:
                    self.send_error_json(400, "Enter the fee in rupees, from 1 to 100000.")
                    return
                put_setting(conn, "fee_rupees", str(amount))
            put_setting(conn, "payment_note", note)
            conn.commit()
            self.send_json({"ok": True, "event": public_event(conn)})

    def save_forms(self) -> None:
        try:
            payload = read_json(self)
        except (ValueError, json.JSONDecodeError):
            self.send_error_json(400, "The form links could not be read.")
            return
        forms = payload.get("forms")
        if not isinstance(forms, dict):
            self.send_error_json(400, "Send one Google Form link for each committee.")
            return
        cleaned = {}
        for committee in COMMITTEES:
            raw = forms.get(committee["id"], "")
            if raw is None:
                raw = ""
            if not isinstance(raw, str):
                self.send_error_json(400, committee["short"] + ": paste the Google Form link as text.")
                return
            try:
                cleaned[committee["id"]] = normalize_google_form(raw)
            except ValueError as exc:
                self.send_error_json(400, committee["short"] + ": " + str(exc))
                return
        with connect() as conn:
            for committee_id, url in cleaned.items():
                put_setting(conn, "form_url_" + committee_id, url)
            conn.commit()
            self.send_json({"ok": True, "catalog": catalog(conn)})

    def save_window(self) -> None:
        try:
            payload = read_json(self)
        except (ValueError, json.JSONDecodeError):
            self.send_error_json(400, "The registration window could not be read.")
            return
        with connect() as conn:
            put_setting(conn, "registration_open", "1" if payload.get("open") else "0")
            conn.commit()
            self.send_json({"ok": True, "event": public_event(conn)})

    def upload_payment_qr(self) -> None:
        data = self.read_upload("qr")
        if data is None:
            return
        try:
            save_image(data, QR_PATH)
        except ValueError as exc:
            self.send_error_json(400, str(exc))
            return
        with connect() as conn:
            self.send_json({"ok": True, "event": public_event(conn)})

    def clear_payment_qr(self) -> None:
        current = qr_file()
        if current:
            current.unlink(missing_ok=True)
        with connect() as conn:
            self.send_json({"ok": True, "event": public_event(conn)})

    def review_payment(self) -> None:
        try:
            payload = read_json(self)
        except (ValueError, json.JSONDecodeError):
            self.send_error_json(400, "The review could not be read.")
            return
        reg_id = payload.get("id")
        action = payload.get("action")
        reason = clean_text(payload.get("reason"), 300)
        if not isinstance(reg_id, int) or action not in {"confirm", "reject"}:
            self.send_error_json(400, "Choose confirm or reject for a registration.")
            return
        if action == "reject" and len(reason) < 3:
            self.send_error_json(400, "Add a short reason the delegate can read.")
            return
        with connect() as conn:
            row = conn.execute("SELECT * FROM registrations WHERE id = ?", (reg_id,)).fetchone()
            if not row or row["status"] != "active":
                self.send_error_json(404, "That registration is not active.")
                return
            if row["payment_status"] not in {"submitted", "confirmed", "rejected"}:
                self.send_error_json(409, "The delegate has not submitted a payment reference yet.")
                return
            status = "confirmed" if action == "confirm" else "rejected"
            conn.execute(
                """
                UPDATE registrations
                SET payment_status = ?, payment_reject_reason = ?, payment_reviewed_at = ?
                WHERE id = ?
                """,
                (status, "" if action == "confirm" else reason, iso(), reg_id),
            )
            conn.commit()
            updated = conn.execute("SELECT * FROM registrations WHERE id = ?", (reg_id,)).fetchone()
            self.send_json({"ok": True, "registration": owner_view(conn, updated)})

    def reassign(self) -> None:
        try:
            payload = read_json(self)
        except (ValueError, json.JSONDecodeError):
            self.send_error_json(400, "The assignment could not be read.")
            return
        reg_id = payload.get("id")
        portfolio_id = clean_text(payload.get("portfolioId"), 40)
        portfolio = PORTFOLIO_INDEX.get(portfolio_id)
        if not isinstance(reg_id, int) or not portfolio:
            self.send_error_json(400, "Choose a portfolio from the handbook.")
            return
        try:
            with connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM registrations WHERE id = ?", (reg_id,)).fetchone()
                if not row or row["status"] != "active":
                    conn.rollback()
                    self.send_error_json(404, "That registration is not active.")
                    return
                if portfolio["committeeId"] != row["committee_id"]:
                    conn.rollback()
                    self.send_error_json(400, "Reassign within the same committee, or cancel and ask them to register again.")
                    return
                held = conn.execute(
                    "SELECT id FROM registrations WHERE portfolio_id = ? AND status = 'active' AND id != ?",
                    (portfolio_id, reg_id),
                ).fetchone()
                if held:
                    conn.rollback()
                    self.send_error_json(409, "That portfolio is already held.")
                    return
                conn.execute(
                    "UPDATE registrations SET portfolio_id = ? WHERE id = ?",
                    (portfolio_id, reg_id),
                )
                conn.commit()
                updated = conn.execute("SELECT * FROM registrations WHERE id = ?", (reg_id,)).fetchone()
                self.send_json({"ok": True, "registration": owner_view(conn, updated)})
        except sqlite3.IntegrityError:
            self.send_error_json(409, "That portfolio is already held.")

    def upload_pass_qr(self) -> None:
        fields_and_file = self.read_upload_with_fields("qr")
        if fields_and_file is None:
            return
        fields, data = fields_and_file
        try:
            reg_id = int(fields.get("id") or "")
        except ValueError:
            self.send_error_json(400, "Choose the delegate for this pass QR.")
            return
        with connect() as conn:
            row = conn.execute("SELECT * FROM registrations WHERE id = ?", (reg_id,)).fetchone()
            if not row or row["status"] != "active":
                self.send_error_json(404, "That registration is not active.")
                return
            try:
                kind = save_image(data, PASSES / str(reg_id))
            except ValueError as exc:
                self.send_error_json(400, str(exc))
                return
            conn.execute("UPDATE registrations SET pass_qr_ext = ? WHERE id = ?", (kind, reg_id))
            conn.commit()
            updated = conn.execute("SELECT * FROM registrations WHERE id = ?", (reg_id,)).fetchone()
            self.send_json({"ok": True, "registration": owner_view(conn, updated)})

    def clear_pass_qr(self) -> None:
        try:
            payload = read_json(self)
            reg_id = int(payload.get("id"))
        except (ValueError, json.JSONDecodeError, TypeError):
            self.send_error_json(400, "Choose the delegate whose pass QR should be cleared.")
            return
        with connect() as conn:
            row = conn.execute("SELECT * FROM registrations WHERE id = ?", (reg_id,)).fetchone()
            if not row:
                self.send_error_json(404, "That registration was not found.")
                return
            current = pass_file(reg_id, row["pass_qr_ext"])
            if current:
                current.unlink(missing_ok=True)
            conn.execute("UPDATE registrations SET pass_qr_ext = NULL WHERE id = ?", (reg_id,))
            conn.commit()
            updated = conn.execute("SELECT * FROM registrations WHERE id = ?", (reg_id,)).fetchone()
            self.send_json({"ok": True, "registration": owner_view(conn, updated)})

    def cancel_registration(self) -> None:
        try:
            payload = read_json(self)
            reg_id = int(payload.get("id"))
        except (ValueError, json.JSONDecodeError, TypeError):
            self.send_error_json(400, "Choose the registration to cancel.")
            return
        with connect() as conn:
            row = conn.execute("SELECT * FROM registrations WHERE id = ?", (reg_id,)).fetchone()
            if not row or row["status"] != "active":
                self.send_error_json(404, "That registration is not active.")
                return
            current = pass_file(reg_id, row["pass_qr_ext"])
            if current:
                current.unlink(missing_ok=True)
            conn.execute(
                """
                UPDATE registrations
                SET status = 'cancelled', portfolio_id = NULL, pass_qr_ext = NULL
                WHERE id = ?
                """,
                (reg_id,),
            )
            conn.commit()
            updated = conn.execute("SELECT * FROM registrations WHERE id = ?", (reg_id,)).fetchone()
            self.send_json({"ok": True, "registration": owner_view(conn, updated)})

    def change_password(self) -> None:
        try:
            payload = read_json(self)
        except (ValueError, json.JSONDecodeError):
            self.send_error_json(400, "The password could not be read.")
            return
        current = str(payload.get("current") or "")
        new_password = str(payload.get("next") or "")
        if len(new_password) < 10:
            self.send_error_json(400, "Use at least 10 characters for the new password.")
            return
        with connect() as conn:
            salt = setting(conn, "admin_password_salt")
            digest = setting(conn, "admin_password_hash")
            if not check_password(current, salt, digest):
                self.send_error_json(401, "The current password does not match.")
                return
            new_salt, new_digest = hash_password(new_password)
            put_setting(conn, "admin_password_salt", new_salt)
            put_setting(conn, "admin_password_hash", new_digest)
            conn.execute("DELETE FROM sessions")
            conn.commit()
        if PASSWORD_FILE.is_file():
            PASSWORD_FILE.unlink()
        self.send_json(
            {"ok": True},
            extra_headers={"Set-Cookie": "brics_session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0"},
        )

    def export_csv(self) -> None:
        import csv
        from io import StringIO

        buffer = StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "Code",
                "Status",
                "Name",
                "Email",
                "Mobile",
                "School",
                "Class",
                "City",
                "Committee",
                "Portfolio",
                "Alternate",
                "Payment",
                "UTR",
                "Payer",
                "Fee at submit",
                "Emergency name",
                "Emergency phone",
                "Experience",
                "Notes",
                "Registered at",
            ]
        )
        with connect() as conn:
            rows = conn.execute("SELECT * FROM registrations ORDER BY id").fetchall()
            for row in rows:
                view = owner_view(conn, row)
                portfolio = view["portfolio"]
                alternate = view["alternate"]
                writer.writerow(
                    [
                        view["code"],
                        view["status"],
                        view["fullName"],
                        view["email"],
                        view["mobile"],
                        view["school"],
                        view["classYear"],
                        view["city"],
                        view["committeeName"],
                        "" if not portfolio else f"{portfolio['number']}. {portfolio['title']}",
                        "" if not alternate else f"{alternate['number']}. {alternate['title']}",
                        view["paymentStatus"],
                        view["paymentUtr"],
                        view["payerName"],
                        "" if view["feeAtSubmit"] is None else view["feeAtSubmit"],
                        view["emergencyName"],
                        view["emergencyPhone"],
                        view["experience"],
                        view["notes"],
                        view["createdAt"],
                    ]
                )
        payload = "\ufeff" + buffer.getvalue()
        data = payload.encode("utf-8")
        self.send_bytes(
            data,
            200,
            "text/csv; charset=utf-8",
            extra_headers={"Content-Disposition": 'attachment; filename="brics-summit-2027-registrations.csv"'},
        )

    def send_pass_qr(self, token: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_\-]{20,80}", token or ""):
            self.send_error_json(404, "No pass QR for this link.")
            return
        with connect() as conn:
            row = conn.execute(
                "SELECT id, pass_qr_ext FROM registrations WHERE claim_token = ?",
                (token,),
            ).fetchone()
        if not row:
            self.send_error_json(404, "No pass QR for this link.")
            return
        self.send_qr(pass_file(row["id"], row["pass_qr_ext"]))

    def send_qr(self, path: Path | None) -> None:
        if not path:
            self.send_error_json(404, "The QR slot is empty.")
            return
        kind = path.suffix.lower().lstrip(".")
        self.send_bytes(path.read_bytes(), 200, MEDIA.get(kind, "application/octet-stream"))

    def submit_proof(self) -> None:
        parsed = self.read_upload_with_fields(
            "proof",
            limit=MAX_PROOF,
            missing="Attach the payment proof as a PDF.",
        )
        if parsed is None:
            return
        fields, data = parsed
        if len(data) < 50 or not data.startswith(b"%PDF-"):
            self.send_error_json(400, "The payment proof has to be a PDF from Paytm or your bank.")
            return
        full_name = clean_text(fields.get("fullName"), 120)
        email = clean_text(fields.get("email"), 160).lower()
        mobile = clean_phone(fields.get("mobile"))
        committee_id = clean_text(fields.get("committeeId"), 40)
        portfolio_id = clean_text(fields.get("portfolioId"), 40)
        note = clean_text(fields.get("note"), 400)
        portfolio = PORTFOLIO_INDEX.get(portfolio_id) if portfolio_id else None
        if len(full_name) < 3:
            self.send_error_json(400, "Enter the delegate's full name.")
            return
        if not EMAIL_RE.match(email):
            self.send_error_json(400, "Enter a working email address.")
            return
        if not valid_phone(mobile):
            self.send_error_json(400, "Enter a mobile number with 10 to 15 digits.")
            return
        if committee_id not in COMMITTEE_BY_ID:
            self.send_error_json(400, "Choose the committee from the handbook.")
            return
        if portfolio_id and (not portfolio or portfolio["committeeId"] != committee_id):
            self.send_error_json(400, "Choose a portfolio from that committee.")
            return
        PROOFS.mkdir(parents=True, exist_ok=True)
        with connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO payment_proofs (
                    full_name, email, mobile, committee_id, portfolio_id, note, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    full_name,
                    email,
                    mobile,
                    committee_id,
                    portfolio_id or None,
                    note,
                    iso(),
                ),
            )
            proof_id = cursor.lastrowid
            path = PROOFS / f"{proof_id}.pdf"
            path.write_bytes(data)
            conn.commit()
        self.send_json({"ok": True})

    def admin_board(self) -> None:
        with connect() as conn:
            holders = {
                row["portfolio_id"]: {
                    "name": row["full_name"],
                    "email": row["email"],
                    "school": row["school"],
                    "code": row["code"],
                }
                for row in conn.execute(
                    """
                    SELECT portfolio_id, full_name, email, school, code
                    FROM registrations
                    WHERE status = 'active' AND portfolio_id IS NOT NULL
                    """
                )
            }
            proof_rows = conn.execute(
                "SELECT * FROM payment_proofs ORDER BY id DESC"
            ).fetchall()
            registration_rows = conn.execute(
                "SELECT * FROM registrations WHERE status = 'active' ORDER BY id DESC"
            ).fetchall()
        requests: dict[str, list[dict]] = {}
        proofs = []
        for row in proof_rows:
            committee = COMMITTEE_BY_ID.get(row["committee_id"])
            portfolio = PORTFOLIO_INDEX.get(row["portfolio_id"] or "")
            item = {
                "id": row["id"],
                "fullName": row["full_name"],
                "email": row["email"],
                "mobile": row["mobile"],
                "committeeId": row["committee_id"],
                "committeeName": committee["name"] if committee else row["committee_id"],
                "portfolio": None
                if not portfolio
                else {"id": portfolio["id"], "number": portfolio["number"], "title": portfolio["title"]},
                "note": row["note"],
                "createdAt": row["created_at"],
            }
            proofs.append(item)
            if portfolio:
                requests.setdefault(portfolio["id"], []).append(
                    {"id": row["id"], "name": row["full_name"], "email": row["email"]}
                )
        committees = []
        to_give = 0
        for committee in COMMITTEES:
            portfolios = []
            for number, title in committee["portfolios"]:
                portfolio_id = f"{committee['id']}-{number}"
                holder = holders.get(portfolio_id)
                if holder is None:
                    to_give += 1
                portfolios.append(
                    {
                        "id": portfolio_id,
                        "number": number,
                        "title": title,
                        "toGive": holder is None,
                        "holder": holder,
                        "requests": requests.get(portfolio_id, []),
                    }
                )
            committees.append(
                {
                    "id": committee["id"],
                    "number": committee["number"],
                    "name": committee["name"],
                    "short": committee["short"],
                    "portfolios": portfolios,
                }
            )
        proofs_for_email: dict[str, list[int]] = {}
        for item in proofs:
            proofs_for_email.setdefault(item["email"], []).append(item["id"])
        registrations = []
        for row in registration_rows:
            committee = COMMITTEE_BY_ID.get(row["committee_id"])
            portfolio = PORTFOLIO_INDEX.get(row["portfolio_id"] or "")
            registrations.append(
                {
                    "id": row["id"],
                    "code": row["code"],
                    "fullName": row["full_name"],
                    "email": row["email"],
                    "mobile": row["mobile"],
                    "school": row["school"],
                    "classYear": row["class_year"],
                    "city": row["city"],
                    "committeeName": committee["name"] if committee else row["committee_id"],
                    "portfolio": None
                    if not portfolio
                    else {"number": portfolio["number"], "title": portfolio["title"]},
                    "emergencyName": row["emergency_name"],
                    "emergencyPhone": row["emergency_phone"],
                    "note": row["notes"],
                    "createdAt": row["created_at"],
                    "proofIds": proofs_for_email.get(row["email"], []),
                }
            )
        self.send_json(
            {
                "toGive": to_give,
                "committees": committees,
                "proofs": proofs,
                "registrations": registrations,
            }
        )

    def send_proof(self, raw_id: str) -> None:
        if not raw_id.isdigit():
            self.send_error_json(404, "That payment proof was not found.")
            return
        path = PROOFS / f"{int(raw_id)}.pdf"
        if not path.is_file():
            self.send_error_json(404, "That payment proof was not found.")
            return
        self.send_bytes(
            path.read_bytes(),
            200,
            "application/pdf",
            extra_headers={"Content-Disposition": f'inline; filename="payment-proof-{int(raw_id)}.pdf"'},
        )

    def remove_proof(self) -> None:
        try:
            payload = read_json(self)
            proof_id = int(payload.get("id"))
        except (ValueError, json.JSONDecodeError, TypeError):
            self.send_error_json(400, "Choose the payment proof to remove.")
            return
        with connect() as conn:
            row = conn.execute("SELECT id FROM payment_proofs WHERE id = ?", (proof_id,)).fetchone()
            if not row:
                self.send_error_json(404, "That payment proof was not found.")
                return
            conn.execute("DELETE FROM payment_proofs WHERE id = ?", (proof_id,))
            conn.commit()
        (PROOFS / f"{proof_id}.pdf").unlink(missing_ok=True)
        self.send_json({"ok": True})

    def read_upload(self, field: str) -> bytes | None:
        parsed = self.read_upload_with_fields(field)
        if parsed is None:
            return None
        return parsed[1]

    def read_upload_with_fields(
        self,
        field: str,
        limit: int = MAX_UPLOAD,
        missing: str = "Attach an image.",
    ) -> tuple[dict, bytes] | None:
        content_type = self.headers.get("Content-Type") or ""
        length = int(self.headers.get("Content-Length") or 0)
        if "multipart/form-data" not in content_type or length <= 0 or length > limit + 20_000:
            self.send_error_json(400, missing)
            return None
        parsed = parse_multipart(self.rfile.read(length), content_type)
        uploaded = parsed["files"].get(field)
        if not uploaded or not uploaded["data"]:
            self.send_error_json(400, missing)
            return None
        return parsed["fields"], uploaded["data"]

    def send_json(self, payload: dict, status: int = 200, extra_headers: dict | None = None) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_bytes(data, status, "application/json; charset=utf-8", extra_headers)

    def send_error_json(self, status: int, message: str) -> None:
        self.send_json({"ok": False, "error": message}, status)

    def send_html(self, html: str, status: int = 200) -> None:
        self.send_bytes(html.encode("utf-8"), status, "text/html; charset=utf-8")

    def send_bytes(self, data: bytes, status: int, content_type: str, extra_headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Date", formatdate(usegmt=True))
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    init_db()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"BRICS Summit 2027 delegate site: http://{HOST}:{PORT}")
    print(f"Secretariat desk: http://{HOST}:{PORT}/secretariat")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
