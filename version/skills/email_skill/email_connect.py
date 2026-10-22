"""
skills/email_skill/email_connect.py — Programmatic (non-CLI) mailbox connect
helpers, used by the Settings UI's Connect/Disconnect buttons.

Mirrors the standalone connector scripts (v4's Outlook-Connector/
connect_outlook.py and Gmail-Connector/connect_gmail.py) but returns
(success, message) instead of printing to a console and calling input() —
these run inside the Settings UI's own process, triggered by a button click,
not as a separate terminal window. Both connect_* functions are BLOCKING:
they open a browser and wait for the user to finish signing in there.
"""

import http.server
import os
import urllib.parse
import webbrowser

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.events",
]


def _force_account_picker(consent_url: str) -> str:
    """Add prompt=select_account so Microsoft/Google always shows the account
    chooser, instead of silently reusing the browser's existing session —
    without this you can never pick a different mailbox once one is signed in."""
    try:
        parts = urllib.parse.urlsplit(consent_url)
        query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
        query["prompt"] = "select_account"
        return urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), parts.fragment)
        )
    except Exception:
        return consent_url  # never block sign-in on a URL-edit failure


# ── Status checks ───────────────────────────────────────────────────────────

def is_outlook_connected(token_path: str) -> bool:
    """Outlook's O365 library just checks the token file exists and parses.

    Must use the REAL client_id/secret/tenant_id — not placeholder credentials
    — to match the actual agent's connection (skills/email_skill/outlook_client.py's
    get_outlook_account()). MSAL's token cache is scoped by client_id + tenant,
    so a check built with different/empty credentials can genuinely fail to find
    a real, working token: this reported "Not connected" even while the agent
    was actively polling with the same token file, purely because the placeholder
    ("", "") credentials (and the default tenant_id="common" instead of the real
    tenant) don't match what the cache entries are keyed under."""
    if not os.path.isfile(token_path):
        return False
    try:
        from O365 import Account, FileSystemTokenBackend
        from core.config import OUTLOOK_CLIENT_ID, OUTLOOK_CLIENT_SECRET, OUTLOOK_TENANT_ID
        token_backend = FileSystemTokenBackend(
            token_path=os.path.dirname(token_path),
            token_filename=os.path.basename(token_path),
        )
        account = Account(
            (OUTLOOK_CLIENT_ID, OUTLOOK_CLIENT_SECRET),
            tenant_id=OUTLOOK_TENANT_ID,
            token_backend=token_backend,
        )
        return bool(account.is_authenticated)
    except Exception:
        return False


def is_gmail_connected(token_path: str) -> bool:
    if not os.path.isfile(token_path):
        return False
    try:
        from google.oauth2.credentials import Credentials
        creds = Credentials.from_authorized_user_file(token_path, GMAIL_SCOPES)
        return bool(creds and (creds.valid or (creds.expired and creds.refresh_token)))
    except Exception:
        return False


def disconnect(token_path: str) -> tuple[bool, str]:
    """Delete a saved mailbox token so a new one can be connected."""
    if not os.path.isfile(token_path):
        return True, "Already disconnected — no token file found."
    try:
        os.remove(token_path)
        return True, "Disconnected."
    except Exception as e:
        return False, f"Could not remove the token file: {e}"


# ── Connect flows ────────────────────────────────────────────────────────────

def connect_outlook(client_id: str, client_secret: str, tenant_id: str, token_path: str) -> tuple[bool, str]:
    """Run the Outlook OAuth flow. Blocking — opens a browser and waits for
    the user to sign in. Returns (success, message)."""
    missing = [k for k, v in {
        "OUTLOOK_CLIENT_ID": client_id,
        "OUTLOOK_CLIENT_SECRET": client_secret,
        "OUTLOOK_TENANT_ID": tenant_id,
    }.items() if not v]
    if missing:
        return False, f"Missing in .env: {', '.join(missing)}"

    try:
        from O365 import Account, FileSystemTokenBackend
    except ImportError:
        return False, "The O365 library isn't installed. Run install.bat first, or: pip install O365"

    token_backend = FileSystemTokenBackend(
        token_path=os.path.dirname(token_path),
        token_filename=os.path.basename(token_path),
    )
    account = Account((client_id, client_secret), tenant_id=tenant_id, token_backend=token_backend)

    if account.is_authenticated:
        return True, f"Already connected. Token: {token_path}"

    redirect = "http://localhost:8000"
    port = 8000

    def browser_consent(consent_url):
        consent_url = _force_account_picker(consent_url)
        captured = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if "code=" in self.path or "error=" in self.path:
                    captured["url"] = redirect + self.path
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(
                        b"<html><body style='font-family:sans-serif'>"
                        b"<h2>Mailbox connected.</h2>"
                        b"<p>You can close this tab and return to the Settings page.</p>"
                        b"</body></html>"
                    )
                else:
                    self.send_response(204)
                    self.end_headers()

            def log_message(self, *args):
                pass  # silence default HTTP request logging

        try:
            httpd = http.server.HTTPServer(("localhost", port), Handler)
        except OSError as e:
            raise RuntimeError(
                f"Could not open the local listener on port {port} ({e}). "
                "Something else may be using it — close it and try again."
            )

        try:
            webbrowser.open(consent_url)
        except Exception:
            pass

        while "url" not in captured:
            httpd.handle_request()
        httpd.server_close()
        return captured["url"]

    try:
        ok = account.authenticate(
            requested_scopes=["basic", "message_all"],
            redirect_uri=redirect,
            handle_consent=browser_consent,
        )
    except Exception as e:
        return False, f"Sign-in failed: {e}"

    if ok:
        return True, f"Connected. Token saved: {token_path}"
    return False, f"Sign-in did not complete. Check that the Azure app's redirect URI includes {redirect}"


def connect_gmail(client_id: str, client_secret: str, token_path: str) -> tuple[bool, str]:
    """Run the Gmail OAuth flow. Blocking — opens a browser and waits for
    the user to sign in. Returns (success, message)."""
    missing = [k for k, v in {
        "GMAIL_CLIENT_ID": client_id,
        "GMAIL_CLIENT_SECRET": client_secret,
    }.items() if not v]
    if missing:
        return False, f"Missing in .env: {', '.join(missing)}"

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError:
        return False, ("The Google auth libraries aren't installed. Run install.bat first, "
                        "or: pip install google-auth-oauthlib google-api-python-client")

    if os.path.isfile(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path, GMAIL_SCOPES)
            if creds and creds.valid:
                return True, f"Already connected. Token: {token_path}"
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(token_path, "w", encoding="utf-8") as f:
                    f.write(creds.to_json())
                return True, "Token refreshed — mailbox still connected."
        except Exception:
            pass  # fall through to a fresh sign-in

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    try:
        flow = InstalledAppFlow.from_client_config(client_config, GMAIL_SCOPES)
        creds = flow.run_local_server(port=0)
    except Exception as e:
        return False, f"Sign-in failed: {e}"

    try:
        with open(token_path, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
    except Exception as e:
        return False, f"Signed in, but could not save the token file: {e}"

    return True, f"Connected. Token saved: {token_path}"
