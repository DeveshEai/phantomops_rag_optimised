"""
setup_gmail_oauth.py — One-time Gmail OAuth setup for Sally

Run this once per mailbox to authorise Sally and generate gmail_token.json.

Prerequisites (Google Cloud Console — https://console.cloud.google.com):
    1. Create a project (or reuse one).
    2. APIs & Services -> Library -> enable BOTH "Gmail API" and
       "Google Calendar API" (Calendar is needed for meeting scheduling).
    3. APIs & Services -> OAuth consent screen:
         - User type: External (or Internal for Workspace).
         - Add your Gmail address under "Test users".
    4. APIs & Services -> Credentials -> Create Credentials ->
       OAuth client ID -> Application type: "Desktop app".
    5. Copy the Client ID and Client Secret shown on the credentials page
       (no file to download) into .env:
           GMAIL_CLIENT_ID=...
           GMAIL_CLIENT_SECRET=...

Then run:
    python skills/email_skill/setup_gmail_oauth.py

A browser window opens; sign in with the Gmail account Sally will use and
approve the read/send/modify scopes. The resulting token is written to
gmail_token.json. Set EMAIL_PROVIDER=gmail in .env and start Sally.
"""

import os
import sys

# Ensure project root is importable when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.config import GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_TOKEN_PATH
from skills.email_skill.gmail_client import GMAIL_SCOPES


def main():
    print("=" * 55)
    print("  Sally — Gmail OAuth Setup")
    print("=" * 55)

    missing = [name for name, val in [
        ("GMAIL_CLIENT_ID", GMAIL_CLIENT_ID),
        ("GMAIL_CLIENT_SECRET", GMAIL_CLIENT_SECRET),
    ] if not val]
    if missing:
        print(f"\n[ERROR] Missing in .env: {', '.join(missing)}\n")
        print("Create an OAuth 'Desktop app' client in Google Cloud Console and")
        print("paste its Client ID and Client Secret into .env (see this file's")
        print("docstring for the full steps).")
        sys.exit(1)

    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    creds = None
    if os.path.isfile(GMAIL_TOKEN_PATH):
        try:
            creds = Credentials.from_authorized_user_file(GMAIL_TOKEN_PATH, GMAIL_SCOPES)
        except Exception:
            creds = None

    if creds and creds.valid:
        print(f"\n[OK] A valid token already exists at {GMAIL_TOKEN_PATH}.")
        print("Delete it and re-run if you want to re-authorise.")
        return

    if creds and creds.expired and creds.refresh_token:
        print("\n[Init] Refreshing expired token...")
        creds.refresh(Request())
    else:
        print("\n[Init] Launching browser for Google sign-in...")
        client_config = {
            "installed": {
                "client_id": GMAIL_CLIENT_ID,
                "client_secret": GMAIL_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        }
        flow = InstalledAppFlow.from_client_config(client_config, GMAIL_SCOPES)
        # Auto-loopback: opens a local server so no copy-paste of the code is needed.
        creds = flow.run_local_server(port=0)

    with open(GMAIL_TOKEN_PATH, "w", encoding="utf-8") as f:
        f.write(creds.to_json())

    # Confirm the mailbox we just authorised
    try:
        from googleapiclient.discovery import build
        svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
        profile = svc.users().getProfile(userId="me").execute()
        print(f"\n[OK] Authorised mailbox: {profile.get('emailAddress')}")
    except Exception:
        pass

    print(f"[OK] Token saved to {GMAIL_TOKEN_PATH}")
    print("\nNext steps:")
    print("  1. Set EMAIL_PROVIDER=gmail in your .env")
    print("  2. Start the agent:  python run_agent.py")


if __name__ == "__main__":
    main()
