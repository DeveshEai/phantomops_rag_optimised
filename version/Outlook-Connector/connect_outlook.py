"""
connect_outlook.py — One-click Outlook mailbox connector for PhantomOps Version 4.

Reads OUTLOOK_CLIENT_ID / _SECRET / _TENANT_ID from the PhantomOps .env (one folder
up), opens the Microsoft sign-in page in a browser automatically, captures the
redirect on http://localhost:8000, and saves outlook_token.txt into the PhantomOps
folder — no manual ID entry and no copy-pasting of URLs.

Requires: O365 (already in PhantomOps's .venv; run.bat installs it if missing).
"""

import os
import sys
import http.server
import urllib.parse
import webbrowser


def _force_account_picker(consent_url):
    """Add prompt=select_account so Microsoft always shows the account chooser.

    Without it, the browser's existing session is reused silently and the
    already-signed-in account is connected without asking — so you can never
    pick a different mailbox. 'select_account' makes the picker appear (with an
    'Use another account' option) every time.
    """
    try:
        parts = urllib.parse.urlsplit(consent_url)
        query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
        query["prompt"] = "select_account"
        new_query = urllib.parse.urlencode(query)
        return urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, parts.path, new_query, parts.fragment)
        )
    except Exception:
        return consent_url  # never block sign-in on a URL-edit failure

# PhantomOps Version 4 folder = the parent of this Outlook-Connector folder.
HERE       = os.path.dirname(os.path.abspath(__file__))
SALLY_DIR  = os.path.abspath(os.path.join(HERE, ".."))
ENV_PATH   = os.path.join(SALLY_DIR, ".env")
TOKEN_DIR  = SALLY_DIR
TOKEN_FILE = "outlook_token.txt"
REDIRECT   = "http://localhost:8000"
PORT       = 8000


def load_env(path):
    """Minimal .env reader (KEY=VALUE, ignores comments/blank lines)."""
    values = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    values[k.strip()] = v.strip().strip('"').strip("'")
    return values


def browser_consent(consent_url):
    """Open the sign-in page and capture the redirect automatically.

    Returns the full redirect URL (with the auth code) that O365 needs.
    Falls back to manual paste if the local listener cannot start.
    """
    consent_url = _force_account_picker(consent_url)
    captured = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if "code=" in self.path or "error=" in self.path:
                captured["url"] = REDIRECT + self.path
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"<html><body style='font-family:sans-serif'>"
                    b"<h2>PhantomOps is connected.</h2>"
                    b"<p>You can close this tab and return to the setup window.</p>"
                    b"</body></html>"
                )
            else:
                self.send_response(204)
                self.end_headers()

        def log_message(self, *args):
            pass  # silence the default HTTP logging

    try:
        httpd = http.server.HTTPServer(("localhost", PORT), Handler)
    except OSError as e:
        print(f"\n[!] Could not open the local listener on port {PORT} ({e}).")
        print("    Falling back to manual mode.")
        print("\n  1. Open this URL in your browser and sign in:\n")
        print("   " + consent_url + "\n")
        return input("  2. Paste the FULL address bar URL here and press Enter:\n  ").strip()

    print("\nA browser window is opening for Microsoft sign-in...")
    print("Sign in with the AGENT MAILBOX and click Accept.")
    print("(If the browser did not open, paste this URL manually:)")
    print("\n   " + consent_url + "\n")
    try:
        webbrowser.open(consent_url)
    except Exception:
        pass

    print("Waiting for sign-in to complete...")
    while "url" not in captured:
        httpd.handle_request()
    httpd.server_close()
    return captured["url"]


def main():
    print("=" * 60)
    print("  PhantomOps - Outlook Mailbox Connector (Version 4)")
    print("=" * 60)

    env = load_env(ENV_PATH)
    client_id     = env.get("OUTLOOK_CLIENT_ID", "")
    client_secret = env.get("OUTLOOK_CLIENT_SECRET", "")
    tenant_id     = env.get("OUTLOOK_TENANT_ID", "")

    missing = [k for k, v in {
        "OUTLOOK_CLIENT_ID": client_id,
        "OUTLOOK_CLIENT_SECRET": client_secret,
        "OUTLOOK_TENANT_ID": tenant_id,
    }.items() if not v]
    if missing:
        print(f"\n[ERROR] Missing in {ENV_PATH}:")
        for m in missing:
            print(f"          {m}")
        print("\nOpen the PhantomOps .env file, fill those in, and run this again.")
        input("\nPress Enter to exit.")
        sys.exit(1)

    try:
        from O365 import Account, FileSystemTokenBackend
    except ImportError:
        # Not in this Python yet (e.g. launched outside the PhantomOps .venv) — install
        # it into the CURRENT interpreter so the connector works on its own.
        print("\nThe O365 library isn't installed in this Python - installing it now")
        print("(one-time, needs internet)...")
        import subprocess
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "O365>=2.0.33"],
                check=True,
            )
            from O365 import Account, FileSystemTokenBackend
        except Exception as e:
            print(f"\n[ERROR] Could not install O365 automatically ({e}).")
            print("Fix: run install.bat in the PhantomOps folder first, then try again,")
            print("or manually run:  pip install O365")
            input("\nPress Enter to exit.")
            sys.exit(1)

    token_backend = FileSystemTokenBackend(token_path=TOKEN_DIR, token_filename=TOKEN_FILE)
    account = Account((client_id, client_secret), tenant_id=tenant_id, token_backend=token_backend)

    if account.is_authenticated:
        print("\nThis mailbox is already connected.")
        print(f"Token: {os.path.join(TOKEN_DIR, TOKEN_FILE)}")
        print("To connect a DIFFERENT mailbox, delete that file and run this again.")
        input("\nPress Enter to exit.")
        return

    # requested_scopes (correct O365 2.1.x parameter): read + send mail.
    # Add 'calendar_all' here if your Azure app grants Calendars.ReadWrite
    # and you use meeting scheduling.
    ok = account.authenticate(
        requested_scopes=["basic", "message_all"],
        redirect_uri=REDIRECT,
        handle_consent=browser_consent,
    )

    if ok:
        print("\n" + "=" * 60)
        print("  SUCCESS - mailbox connected.")
        print(f"  Saved: {os.path.join(TOKEN_DIR, TOKEN_FILE)}")
        print("  Close this window and run start.bat to launch PhantomOps.")
        print("=" * 60)
    else:
        print("\n[ERROR] Sign-in did not complete. Please try again.")
        print("Check that the Azure app's redirect URI includes " + REDIRECT)

    input("\nPress Enter to exit.")


if __name__ == "__main__":
    main()
