"""
connect_gmail.py — One-click Gmail mailbox connector for PhantomOps Version 4.

Reads GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET from the PhantomOps .env (one
folder up) — mirrors OUTLOOK_CLIENT_ID/_SECRET/_TENANT_ID exactly, just paste
the two values from your Google Cloud OAuth "Desktop app" client, no file to
download or place. Opens the Google sign-in page in a browser automatically,
captures the redirect on a local loopback port, and saves gmail_token.json
into the PhantomOps folder — no typing, no copy-paste for the sign-in step.

Requires: google-auth-oauthlib, google-api-python-client (already in
PhantomOps's .venv; run.bat installs them if missing).
"""

import os
import sys


# PhantomOps Version 4 folder = the parent of this Gmail-Connector folder.
HERE      = os.path.dirname(os.path.abspath(__file__))
SALLY_DIR = os.path.abspath(os.path.join(HERE, ".."))
ENV_PATH  = os.path.join(SALLY_DIR, ".env")

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.events",
]


def load_env(path):
    """Minimal .env reader (KEY=VALUE, ignores comments/blank lines) — kept
    self-contained (no import of core.config) so this connector also works
    when launched outside the main PhantomOps venv, same as connect_outlook.py."""
    values = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    values[k.strip()] = v.strip().strip('"').strip("'")
    return values


def main():
    print("=" * 60)
    print("  PhantomOps - Gmail Mailbox Connector (Version 4)")
    print("=" * 60)

    env = load_env(ENV_PATH)
    client_id     = env.get("GMAIL_CLIENT_ID", "")
    client_secret = env.get("GMAIL_CLIENT_SECRET", "")
    token_path    = env.get("GMAIL_TOKEN_PATH") or os.path.join(SALLY_DIR, "gmail_token.json")

    missing = [k for k, v in {
        "GMAIL_CLIENT_ID": client_id,
        "GMAIL_CLIENT_SECRET": client_secret,
    }.items() if not v]
    if missing:
        print(f"\n[ERROR] Missing in {ENV_PATH}:")
        for m in missing:
            print(f"          {m}")
        print("""
These are product-level values that should already be filled in with this
PhantomOps package (the same way OUTLOOK_CLIENT_ID/_SECRET/_TENANT_ID are).

If you're an individual tester: ask whoever provided this package to fill
them in — this isn't something you create yourself.

If you administer PhantomOps and these genuinely haven't been set up yet:
see ONE_TIME_ADMIN_SETUP.txt in this folder (a ~5 minute, ONE-TIME setup
for the whole product, not per mailbox).""")
        input("\nPress Enter to exit.")
        sys.exit(1)

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError:
        # Not in this Python yet (e.g. launched outside the PhantomOps .venv) —
        # install into the CURRENT interpreter so the connector works on its own.
        print("\nThe Google auth libraries aren't installed in this Python - installing")
        print("them now (one-time, needs internet)...")
        import subprocess
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install",
                 "google-auth-oauthlib>=1.2.0", "google-api-python-client>=2.100.0"],
                check=True,
            )
            from google_auth_oauthlib.flow import InstalledAppFlow
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
        except Exception as e:
            print(f"\n[ERROR] Could not install the Google auth libraries automatically ({e}).")
            print("Fix: run install.bat in the PhantomOps folder first, then try again,")
            print("or manually run:  pip install google-auth-oauthlib google-api-python-client")
            input("\nPress Enter to exit.")
            sys.exit(1)

    # Already connected? Same "nothing to do" check as the Outlook connector.
    if os.path.isfile(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path, GMAIL_SCOPES)
            if creds and creds.valid:
                print("\nThis mailbox is already connected.")
                print(f"Token: {token_path}")
                print("To connect a DIFFERENT mailbox, run signout.bat first.")
                input("\nPress Enter to exit.")
                return
            if creds and creds.expired and creds.refresh_token:
                print("\n[Init] Refreshing expired token...")
                creds.refresh(Request())
                with open(token_path, "w", encoding="utf-8") as f:
                    f.write(creds.to_json())
                print("Token refreshed — mailbox still connected.")
                input("\nPress Enter to exit.")
                return
        except Exception:
            pass  # fall through to a fresh sign-in

    print("\nA browser window is opening for Google sign-in...")
    print("Sign in with the AGENT MAILBOX and click Allow.")

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
        # Auto-loopback: opens a local server + browser, no copy-paste needed —
        # same no-typing experience as the Outlook connector.
        creds = flow.run_local_server(port=0)
    except Exception as e:
        print(f"\n[ERROR] Sign-in did not complete: {e}")
        print("Check that GMAIL_CLIENT_ID/_SECRET are correct and the Gmail/Calendar")
        print("APIs are enabled for this Google Cloud project.")
        input("\nPress Enter to exit.")
        sys.exit(1)

    with open(token_path, "w", encoding="utf-8") as f:
        f.write(creds.to_json())

    mailbox = ""
    try:
        from googleapiclient.discovery import build
        svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
        mailbox = svc.users().getProfile(userId="me").execute().get("emailAddress", "")
    except Exception:
        pass

    print("\n" + "=" * 60)
    print("  SUCCESS - mailbox connected.")
    if mailbox:
        print(f"  Account:  {mailbox}")
    print(f"  Saved: {token_path}")
    print("  Close this window and run start.bat to launch PhantomOps.")
    print("=" * 60)
    input("\nPress Enter to exit.")


if __name__ == "__main__":
    main()
