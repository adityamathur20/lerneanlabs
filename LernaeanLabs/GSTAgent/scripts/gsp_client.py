"""
gsp_client.py
-------------
Handles all external data fetching for GSTAgent:

  1. SupabaseClient  — thin urllib wrapper for Supabase REST API + RPC calls.
                       Reads/writes clients, filing_runs, gsp_sessions tables.

  2. GSPClient       — WhiteBooks GSP API wrapper (sandbox: apisandbox.whitebooks.in).
                       Auth flow: OTP request → auth token (sandbox OTP = 575757).
                       Fetches GSTR-2B and checks GSTIN status.

Design:
  - stdlib only — urllib, json, os (no requests, no supabase-py)
  - dry_run=True returns realistic mock data; no network calls made
  - sandbox=True (default) uses dummy state GSTINs from WhiteBooks sandbox credentials PDF
  - All GSP errors raise GSPError with the raw API response attached

Environment variables required:
  SUPABASE_URL          — https://xxxx.supabase.co
  SUPABASE_SERVICE_KEY  — service_role JWT from Supabase project settings
  GSP_CLIENT_ID         — from WhiteBooks developer dashboard (GST API > Credentials > Sandbox)
  GSP_CLIENT_SECRET     — from WhiteBooks developer dashboard
  GSP_EMAIL             — the email you registered with WhiteBooks

Usage:
  # dry_run — no API keys needed
  gsp = GSPClient(gstin="24AABMT1234C1Z5", dry_run=True)
  gstr2b = gsp.fetch_gstr2b(period="032026")

  # sandbox live
  gsp = GSPClient(gstin="24AABMT1234C1Z5", sandbox=True)
  gstr2b = gsp.fetch_gstr2b(period="032026")   # auto-authenticates
"""

import http.client
import json, os, urllib.request, urllib.error, urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Union


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class GSPError(Exception):
    def __init__(self, message: str, raw: dict = None):
        super().__init__(message)
        self.raw = raw or {}


class SupabaseError(Exception):
    pass


# ---------------------------------------------------------------------------
# SupabaseClient
# ---------------------------------------------------------------------------

class SupabaseClient:
    """
    Thin urllib wrapper around the Supabase REST API.
    Uses service_role key — bypasses RLS, full table access.

    All methods raise SupabaseError on HTTP failure.
    """

    def __init__(
        self,
        url: Optional[str] = None,
        service_key: Optional[str] = None,
    ):
        self.url = (url or os.environ.get("SUPABASE_URL", "")).rstrip("/")
        self.key = (service_key or os.environ.get("SUPABASE_SERVICE_KEY")
                    or os.environ.get("SUPABASE_KEY", ""))
        if not self.url or not self.key:
            raise SupabaseError(
                "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set. "
                "Add them to ~/.tcshrc and run: source ~/.tcshrc"
            )

    def _headers(self) -> dict:
        return {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }

    def _request(self, method: str, path: str, body: dict = None) -> Union[list, dict]:
        url = f"{self.url}{path}"
        data = json.dumps(body).encode("utf-8") if body else None
        req = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8")
            raise SupabaseError(f"HTTP {e.code} {method} {path}: {body}") from e
        except urllib.error.URLError as e:
            raise SupabaseError(f"Network error {method} {path}: {e.reason}") from e

    # ------------------------------------------------------------------
    # Table operations
    # ------------------------------------------------------------------

    def select(self, table: str, filters: dict = None) -> list:
        """
        GET rows from a table.
        filters: {"column": "eq.value", "other": "gte.0"} — Supabase query syntax
        """
        qs = "&".join(f"{k}={v}" for k, v in (filters or {}).items())
        path = f"/rest/v1/{table}" + (f"?{qs}" if qs else "")
        result = self._request("GET", path)
        return result if isinstance(result, list) else [result]

    def insert(self, table: str, row: dict) -> dict:
        """INSERT one row, return the created row."""
        result = self._request("POST", f"/rest/v1/{table}", row)
        return result[0] if isinstance(result, list) else result

    def upsert(self, table: str, row: dict, on_conflict: str = "") -> dict:
        """INSERT … ON CONFLICT DO UPDATE."""
        path = f"/rest/v1/{table}" + (f"?on_conflict={on_conflict}" if on_conflict else "")
        headers_extra = {"Prefer": "return=representation,resolution=merge-duplicates"}
        # Re-use _request but override Prefer header
        url = f"{self.url}{path}"
        data = json.dumps(row).encode("utf-8")
        hdrs = {**self._headers(), **headers_extra}
        req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                result = json.loads(raw) if raw.strip() else {}
                return result[0] if isinstance(result, list) else result
        except urllib.error.HTTPError as e:
            raise SupabaseError(f"HTTP {e.code} UPSERT {table}: {e.read().decode()}") from e

    def update(self, table: str, filters: dict, values: dict) -> list:
        """PATCH rows matching filters."""
        qs = "&".join(f"{k}={v}" for k, v in filters.items())
        result = self._request("PATCH", f"/rest/v1/{table}?{qs}", values)
        return result if isinstance(result, list) else [result]

    # ------------------------------------------------------------------
    # RPC (Postgres functions)
    # ------------------------------------------------------------------

    def rpc(self, function_name: str, params: dict = None) -> Union[list, dict]:
        """Call a Postgres function via the REST API."""
        return self._request("POST", f"/rest/v1/rpc/{function_name}", params or {})

    # ------------------------------------------------------------------
    # Convenience: client helpers
    # ------------------------------------------------------------------

    def get_active_clients(self) -> list:
        """Return all clients with subscription_status = active or trial."""
        return self.select("clients", filters={"subscription_status": "in.(active,trial)"})

    def get_client_by_gstin(self, gstin: str) -> Optional[dict]:
        rows = self.select("clients", filters={"gstin": f"eq.{gstin}"})
        return rows[0] if rows else None

    def create_filing_run(
        self,
        client_id: str,
        period: str,
        period_label: str,
        gstr1_due: str,
        gstr3b_due: str,
    ) -> dict:
        return self.insert("filing_runs", {
            "client_id": client_id,
            "period": period,
            "period_label": period_label,
            "gstr1_due_date": gstr1_due,
            "gstr3b_due_date": gstr3b_due,
            "run_status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
        })

    def complete_filing_run(
        self,
        run_id: str,
        reconciliation_status: str,
        issue_count: int,
        net_payable_inr: float,
        cost_usd: float,
        cost_inr: float,
    ):
        self.update("filing_runs", {"id": f"eq.{run_id}"}, {
            "run_status": "completed",
            "reconciliation_status": reconciliation_status,
            "issue_count": issue_count,
            "net_payable_inr": net_payable_inr,
            "cost_usd": cost_usd,
            "cost_inr": cost_inr,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })

    def fail_filing_run(self, run_id: str, error_message: str):
        self.update("filing_runs", {"id": f"eq.{run_id}"}, {
            "run_status": "failed",
            "error_message": error_message,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })

    def save_reconciliation_result(self, run_id: str, payload: dict):
        self.insert("reconciliation_results", {"filing_run_id": run_id, **payload})

    def log_alert(
        self,
        run_id: str,
        alert_type: str,
        recipient: str,
        status: str,
        provider: str = None,
        provider_message_id: str = None,
        error_message: str = None,
    ):
        self.insert("alerts_sent", {
            "filing_run_id": run_id,
            "alert_type": alert_type,
            "recipient": recipient,
            "status": status,
            "provider": provider,
            "provider_message_id": provider_message_id,
            "error_message": error_message,
            "sent_at": datetime.now(timezone.utc).isoformat() if status == "sent" else None,
        })


# ---------------------------------------------------------------------------
# GSPClient
# ---------------------------------------------------------------------------

@dataclass
class GSTNStatus:
    gstin: str
    legal_name: str
    trade_name: str
    status: str                   # "Active" | "Cancelled" | "Suspended"
    registration_date: str
    cancellation_date: Optional[str] = None


# ---------------------------------------------------------------------------
# WhiteBooks sandbox credentials (from Sandbox Credentials PDF)
# Keys are the 2-digit state code prefix of the taxpayer GSTIN.
# Value: (sandbox_gstin, gsp_username, gsp_password)
# ---------------------------------------------------------------------------

SANDBOX_CREDENTIALS: dict[str, tuple[str, str, str]] = {
    "01": ("01AAGCB1286Q007", "BVMJK",  "Bvm@123456"),   # J&K
    "02": ("02AAGCB1286Q006", "BVMHP",  "Bvm@123456"),   # Himachal Pradesh
    "03": ("03AAGCB1286Q005", "BVMPB",  "Bvm@123456"),   # Punjab
    "04": ("04AAGCB1286Q004", "BVMCH",  "Bvm@123456"),   # Chandigarh
    "05": ("05AAGCB1286Q003", "BVMUK",  "Bvm@123456"),   # Uttarakhand
    "06": ("06AAGCB1286Q006", "BVMHR",  "Bvm@123456"),   # Haryana
    "07": ("07AAGCB1286Q002", "BVMDL",  "Bvm@123456"),   # Delhi
    "08": ("08AAGCB1286Q001", "BVMRJ",  "Bvm@123456"),   # Rajasthan
    "09": ("09AAGCB1286Q000", "BVMUP",  "Bvm@123456"),   # Uttar Pradesh
    "10": ("10AAGCB1286Q009", "BVMBR",  "Bvm@123456"),   # Bihar
    "11": ("11AAGCB1286Q008", "BVMSK",  "Bvm@123456"),   # Sikkim
    "12": ("12AAGCB1286Q007", "BVMAR",  "Bvm@123456"),   # Arunachal Pradesh
    "13": ("13AAGCB1286Q006", "BVMNL",  "Bvm@123456"),   # Nagaland
    "14": ("14AAGCB1286Q005", "BVMMN",  "Bvm@123456"),   # Manipur
    "15": ("15AAGCB1286Q004", "BVMMZ",  "Bvm@123456"),   # Mizoram
    "16": ("16AAGCB1286Q003", "BVMTR",  "Bvm@123456"),   # Tripura
    "17": ("17AAGCB1286Q002", "BVMML",  "Bvm@123456"),   # Meghalaya
    "18": ("18AAGCB1286Q001", "BVMAS",  "Bvm@123456"),   # Assam
    "19": ("19AAGCB1286Q000", "BVMWB",  "Bvm@123456"),   # West Bengal
    "20": ("20AAGCB1286Q009", "BVMJH",  "Bvm@123456"),   # Jharkhand
    "21": ("21AAGCB1286Q008", "BVMOD",  "Bvm@123456"),   # Odisha
    "22": ("22AAGCB1286Q007", "BVMCG",  "Bvm@123456"),   # Chhattisgarh
    "23": ("23AAGCB1286Q006", "BVMMP",  "Bvm@123456"),   # Madhya Pradesh
    "24": ("24AAGCB1286Q029", "BVMGJ",  "Bvm@123456"),   # Gujarat
    "27": ("27AAGCB1286Q005", "BVMMH",  "Bvm@123456"),   # Maharashtra
    "28": ("28AAGCB1286Q004", "BVMAP",  "Bvm@123456"),   # Andhra Pradesh (old)
    "29": ("29AAGCB1286Q000", "BVMGSP", "Wbooks@0142"),  # Karnataka
    "30": ("30AAGCB1286Q003", "BVMGA",  "Bvm@123456"),   # Goa
    "32": ("32AAGCB1286Q001", "BVMKL",  "Bvm@123456"),   # Kerala
    "33": ("33AAGCB1286Q003", "BVMTN",  "Bvm@123456"),   # Tamil Nadu
    "34": ("34AAGCB1286Q002", "BVMPY",  "Bvm@123456"),   # Puducherry
    "36": ("36AAGCB1286Q000", "BVMTS",  "Bvm@123456"),   # Telangana
    "37": ("37AAGCB1286Q009", "BVMAP2", "Bvm@123456"),   # Andhra Pradesh (new)
}


class GSPClient:
    """
    WhiteBooks GSP API client.

    Auth flow (WhiteBooks sandbox):
      1. GET /authentication/otprequest  → receive txn (OTP request transaction ID)
      2. GET /authentication/authtoken?otp=575757  → receive session txn
      3. Use session txn in all subsequent API calls as the `txn` header

    Sandbox OTP is always 575757.
    Sandbox GSTINs are mapped per state from SANDBOX_CREDENTIALS above.

    dry_run=True: returns mock data from testcases/, no network calls.
    """

    BASE_URL = "https://apisandbox.whitebooks.in"
    SANDBOX_OTP = "575757"

    def __init__(
        self,
        gstin: str,
        dry_run: bool = False,
        sandbox: bool = True,
        db: Optional[SupabaseClient] = None,
    ):
        self.gstin    = gstin
        self.dry_run  = dry_run
        self.sandbox  = sandbox

        self.client_id     = os.environ.get("GSP_CLIENT_ID",     "")
        self.client_secret = os.environ.get("GSP_CLIENT_SECRET", "")
        self.email         = os.environ.get("GSP_EMAIL",         "")

        # GSP_SANDBOX_GSTIN and GSP_GST_USERNAME env vars override the generic PDF credentials.
        # WhiteBooks provides account-specific sandbox credentials that differ from the PDF.
        env_gstin    = os.environ.get("GSP_SANDBOX_GSTIN", "")
        env_username = os.environ.get("GSP_GST_USERNAME",  "")
        if env_gstin and env_username:
            self.gsp_gstin    = env_gstin
            self.gsp_username = env_username
            self.gsp_password = ""
            # state_cd must match the sandbox GSTIN, not the real client GSTIN
            self.state_cd = env_gstin[:2]
        else:
            self.state_cd = gstin[:2]
            creds = SANDBOX_CREDENTIALS.get(self.state_cd)
            if creds:
                self.gsp_gstin, self.gsp_username, self.gsp_password = creds
            else:
                self.gsp_gstin, self.gsp_username, self.gsp_password = SANDBOX_CREDENTIALS["24"]

        self._session_txn: Optional[str] = None
        self._ip: Optional[str] = None

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def authenticate(self) -> str:
        """
        Two-step WhiteBooks auth. Returns the session txn string.
        In dry_run, returns a placeholder immediately.
        """
        if self.dry_run:
            self._session_txn = "dry-run-txn"
            return self._session_txn

        # Step 1: request OTP  (email is a required query param per OpenAPI spec)
        enc_email = urllib.parse.quote(self.email, safe="")
        url1 = f"{self.BASE_URL}/authentication/otprequest?email={enc_email}"
        resp1 = self._wb_get(url1)
        # WhiteBooks uses status_cd "1" for success
        if str(resp1.get("status_cd", resp1.get("status", ""))) != "1":
            err = resp1.get("error", {})
            raise GSPError(
                f"WhiteBooks OTP request failed [{err.get('error_cd', '?')}]: {err.get('message', resp1)}",
                raw=resp1,
            )
        otp_txn = (resp1.get("txn") or resp1.get("Txn") or resp1.get("TXN")
                   or resp1.get("header", {}).get("txn", ""))
        if not otp_txn:
            raise GSPError(f"WhiteBooks OTP response missing txn — full response: {resp1}", raw=resp1)

        # Step 2: exchange OTP for session token
        url2 = f"{self.BASE_URL}/authentication/authtoken?otp={self.SANDBOX_OTP}&email={enc_email}"
        resp2 = self._wb_get(url2, txn=otp_txn)
        if str(resp2.get("status_cd", resp2.get("status", ""))) != "1":
            err = resp2.get("error", {})
            raise GSPError(
                f"WhiteBooks auth token exchange failed [{err.get('error_cd', '?')}]: {err.get('message', resp2)}",
                raw=resp2,
            )

        session_txn = (
            resp2.get("txn") or resp2.get("Txn") or resp2.get("TXN") or
            resp2.get("AuthToken") or resp2.get("auth_token") or
            resp2.get("header", {}).get("txn", "")
        )
        if not session_txn:
            raise GSPError(f"WhiteBooks auth response missing session txn — full response: {resp2}", raw=resp2)

        self._session_txn = session_txn
        return self._session_txn

    def logout(self):
        """Release the current session on WhiteBooks — frees up a session slot."""
        if self.dry_run or not self._session_txn:
            return
        try:
            url = f"{self.BASE_URL}/authentication/logout?email={urllib.parse.quote(self.email, safe='')}"
            self._wb_get(url)
        except Exception:
            pass  # best-effort — don't crash if logout fails
        finally:
            self._session_txn = None

    def _ensure_authenticated(self):
        if self.dry_run:
            return
        if not self._session_txn:
            self.authenticate()

    # ------------------------------------------------------------------
    # GSTR-2B fetch
    # ------------------------------------------------------------------

    def generate_gstr2b(self, period: str) -> dict:
        """
        Trigger on-demand GSTR-2B generation for the given period.
        Must be called before fetch_gstr2b() if no data exists yet.
        PUT /gstr2b/gen2b — gstin and ret_period go in headers, not query.
        """
        if self.dry_run:
            return {"status_cd": "1"}
        self._ensure_authenticated()
        url = f"{self.BASE_URL}/gstr2b/gen2b?email={urllib.parse.quote(self.email, safe='')}"
        extra = {"gstin": self.gsp_gstin, "ret_period": period}
        body = {"rtin": self.gsp_gstin, "itcprd": period}
        resp = self._wb_put(url, body=body, extra_headers=extra)
        if str(resp.get("status_cd", resp.get("status", ""))) != "1":
            err = resp.get("error", {})
            raise GSPError(
                f"GSTR-2B generation failed for {period} [{err.get('error_cd', '?')}]: {err.get('message', resp)}",
                raw=resp,
            )
        return resp

    def fetch_gstr2b(self, period: str) -> dict:
        """
        Fetch GSTR-2B for the given period (MMYYYY, e.g. "032026").

        dry_run: returns mock data from testcases/
        live:    calls WhiteBooks /gstr2b/all using sandbox GSTIN credentials

        Returns a dict compatible with GSTR2BReader.from_api_response().
        """
        if self.dry_run:
            return self._mock_gstr2b(period)

        self._ensure_authenticated()
        url = (
            f"{self.BASE_URL}/gstr2b/all"
            f"?gstin={self.gsp_gstin}&rtnprd={period}&email={urllib.parse.quote(self.email, safe='')}"
        )
        resp = self._wb_get(url)
        if str(resp.get("status_cd", resp.get("status", ""))) != "1":
            err = resp.get("error", {})
            raise GSPError(
                f"GSTR-2B fetch failed for {period} [{err.get('error_cd', '?')}]: {err.get('message', resp)}",
                raw=resp,
            )
        return resp.get("data", resp)

    # ------------------------------------------------------------------
    # GSTIN status check
    # ------------------------------------------------------------------

    def check_gstin_status(self, gstin: str) -> GSTNStatus:
        """
        Check GSTIN registration status via WhiteBooks public search.
        Returns GSTNStatus with status = "Active" | "Cancelled" | "Suspended".
        """
        if self.dry_run:
            return self._mock_gstin_status(gstin)

        # public/search does not need auth (no txn required)
        # passes the real GSTIN — sandbox GSTINs won't be found here
        url = f"{self.BASE_URL}/public/search?gstin={gstin}&email={urllib.parse.quote(self.email, safe='')}"
        resp = self._wb_get(url)
        if str(resp.get("status_cd", resp.get("status", ""))) != "1":
            raise GSPError(f"GSTIN lookup failed for {gstin}", raw=resp)

        tp = resp.get("taxpayerInfo") or resp.get("data") or {}
        return GSTNStatus(
            gstin=gstin,
            legal_name=tp.get("lgnm", ""),
            trade_name=tp.get("tradeNam", ""),
            status=tp.get("sts", "Unknown"),
            registration_date=tp.get("rgdt", ""),
            cancellation_date=tp.get("cxdt") or None,
        )

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _get_ip(self) -> str:
        """Return local IP address for WhiteBooks ip_address header."""
        if self._ip:
            return self._ip
        import socket
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                self._ip = s.getsockname()[0]
        except Exception:
            self._ip = "127.0.0.1"
        return self._ip

    def _wb_headers(self, txn: Optional[str] = None) -> dict:
        """Build the standard WhiteBooks request headers."""
        hdrs = {
            "Content-Type":  "application/json",
            "client_id":     self.client_id,
            "client_secret": self.client_secret,
            "gst_username":  self.gsp_username,
            "state_cd":      self.state_cd,
            "ip_address":    self._get_ip(),
        }
        effective_txn = txn or self._session_txn
        if effective_txn:
            hdrs["txn"] = effective_txn
        return hdrs

    def _wb_put(self, url: str, body: dict, extra_headers: dict = None) -> dict:
        parsed = urllib.parse.urlparse(url)
        path   = parsed.path + ("?" + parsed.query if parsed.query else "")
        hdrs   = {**self._wb_headers(), **(extra_headers or {})}
        data   = json.dumps(body).encode("utf-8")
        try:
            conn = http.client.HTTPSConnection(parsed.netloc, timeout=30)
            conn.request("PUT", path, body=data, headers=hdrs)
            resp = conn.getresponse()
            raw  = resp.read().decode("utf-8")
            if not raw.strip():
                raise GSPError(f"WhiteBooks returned empty body (HTTP {resp.status}) for PUT {url}")
            if resp.status >= 400:
                raise GSPError(f"GSP HTTP {resp.status} — PUT {url}\nResponse: {raw}")
            return json.loads(raw)
        except GSPError:
            raise
        except Exception as e:
            raise GSPError(f"GSP PUT error for {url}: {e}") from e
        finally:
            conn.close()

    def _wb_get(self, url: str, txn: Optional[str] = None) -> dict:
        # Use http.client directly — urllib capitalizes header names, breaking WhiteBooks
        parsed = urllib.parse.urlparse(url)
        path   = parsed.path + ("?" + parsed.query if parsed.query else "")
        hdrs   = self._wb_headers(txn)
        try:
            conn = http.client.HTTPSConnection(parsed.netloc, timeout=30)
            conn.request("GET", path, headers=hdrs)
            resp = conn.getresponse()
            raw  = resp.read().decode("utf-8")
            if not raw.strip():
                raise GSPError(
                    f"WhiteBooks returned empty body (HTTP {resp.status}) for {url}\n"
                    f"Headers sent: {hdrs}"
                )
            if resp.status >= 400:
                raise GSPError(f"GSP HTTP {resp.status} — {url}\nResponse: {raw}")
            return json.loads(raw)
        except GSPError:
            raise
        except Exception as e:
            raise GSPError(f"GSP request error for {url}: {e}") from e
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Mock data (dry_run)
    # ------------------------------------------------------------------

    def _get_scenario(self) -> int:
        """
        Deterministic scenario 0-3 based on GSTIN character sum.
        0=CRITICAL (cancelled GSTIN + missing ITC + HSN)
        1=WARNING  (missing ITC + HSN, all GSTINs active)
        2=WARNING  (cancelled GSTIN + HSN, all ITC matched)
        3=CLEAN    (all ITC matched, all GSTINs active, only HSN flag)
        """
        return sum(ord(c) for c in self.gstin) % 4

    def _mock_gstr2b(self, period: str) -> dict:
        from mock_tally import get_gstr2b, MEHTA_GSTIN
        if self.gstin == MEHTA_GSTIN:
            return self._mock_gstr2b_mehta(period)
        return get_gstr2b(self.gstin, period)

    def _mock_gstr2b_mehta(self, period: str) -> dict:
        """Original Mehta Textile mock GSTR-2B (scenario 0 — CRITICAL)."""
        return {
            "data": {
                "gstin": self.gstin,
                "rtnprd": period,
                "gendt": datetime.now().strftime("%d-%m-%Y"),
                "docdata": {
                    "b2b": [
                        {
                            "ctin": "24AABSM1111A1Z8",
                            "suppName": "Silk Mills Ltd",
                            "suppFilingStatus": "Filed",
                            "suppFilingDate": "10-11-2024",
                            "inv": [{"inum": "SM/2024/1102", "dt": "03-10-2024",
                                     "val": 201600.00, "pos": "24", "rev": "N",
                                     "itcavl": "Y", "rsn": "", "elg": "Input",
                                     "items": [{"num": 1, "rt": 12, "txval": 180000.00,
                                                "igst": 0.00, "cgst": 10800.00, "sgst": 10800.00, "cess": 0.00}]}]
                        },
                        {
                            "ctin": "24AABCH2222B1Z6",
                            "suppName": "Cotton Hub Traders",
                            "suppFilingStatus": "Filed",
                            "suppFilingDate": "08-11-2024",
                            "inv": [{"inum": "CH/OCT/2024/456", "dt": "08-10-2024",
                                     "val": 106400.00, "pos": "24", "rev": "N",
                                     "itcavl": "Y", "rsn": "", "elg": "Input",
                                     "items": [{"num": 1, "rt": 12, "txval": 95000.00,
                                                "igst": 0.00, "cgst": 5700.00, "sgst": 5700.00, "cess": 0.00}]}]
                        },
                        {
                            "ctin": "24AABPC3333C1Z4",
                            "suppName": "Packaging Co Surat",
                            "suppFilingStatus": "Filed",
                            "suppFilingDate": "09-11-2024",
                            "inv": [{"inum": "PC/2024/789", "dt": "18-10-2024",
                                     "val": 20160.00, "pos": "24", "rev": "N",
                                     "itcavl": "Y", "rsn": "", "elg": "Input",
                                     "items": [{"num": 1, "rt": 12, "txval": 18000.00,
                                                "igst": 0.00, "cgst": 1080.00, "sgst": 1080.00, "cess": 0.00}]}]
                        },
                    ],
                    "b2ba": [], "cdnr": [], "impg": [], "imps": [],
                },
            }
        }

    def _mock_gstin_status(self, gstin: str) -> GSTNStatus:
        from mock_tally import get_cancelled_gstins, MEHTA_GSTIN
        if self.gstin == MEHTA_GSTIN:
            scenario = self._get_scenario()
            cancelled = {"24AAFVT9999Z1Z9"} if scenario in (0, 2) else set()
        else:
            cancelled = get_cancelled_gstins(self.gstin)
        status = "Cancelled" if gstin in cancelled else "Active"
        return GSTNStatus(
            gstin=gstin,
            legal_name=f"Mock Legal Name ({gstin})",
            trade_name="Mock Trade Name",
            status=status,
            registration_date="01-07-2017",
            cancellation_date="15-09-2024" if status == "Cancelled" else None,
        )
