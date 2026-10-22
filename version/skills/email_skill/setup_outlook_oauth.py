import os
import sys
import urllib.parse

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from O365 import Account, FileSystemTokenBackend
from core.config import OUTLOOK_CLIENT_ID, OUTLOOK_CLIENT_SECRET, OUTLOOK_TENANT_ID

TOKEN_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'outlook_token.txt')


def _force_account_picker(consent_url):
    """Add prompt=select_account so Microsoft always shows the account chooser.

    Without it the browser's existing session is reused silently and the
    already-signed-in account is connected without asking — so you can never
    pick a different mailbox. 'select_account' makes the picker (with an
    'Use another account' option) appear every time.
    """
    try:
        parts = urllib.parse.urlsplit(consent_url)
        query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
        query["prompt"] = "select_account"
        return urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, parts.path,
             urllib.parse.urlencode(query), parts.fragment)
        )
    except Exception:
        return consent_url  # never block sign-in on a URL-edit failure


def _console_consent(consent_url):
    """Print the (picker-forced) sign-in URL and read the pasted redirect URL."""
    consent_url = _force_account_picker(consent_url)
    print("\nOpen this URL, sign in with the AGENT MAILBOX, then paste the full")
    print("redirect URL from the address bar back here:\n")
    print("   " + consent_url + "\n")
    return input("Paste the resulting URL and press Enter:\n  ").strip()


def main():
    if not all([OUTLOOK_CLIENT_ID, OUTLOOK_CLIENT_SECRET, OUTLOOK_TENANT_ID]):
        print("\n[ERROR] Missing Outlook credentials in .env file.")
        sys.exit(1)

    credentials = (OUTLOOK_CLIENT_ID, OUTLOOK_CLIENT_SECRET)

    token_backend = FileSystemTokenBackend(
        token_path=os.path.dirname(TOKEN_PATH),
        token_filename='outlook_token.txt'
    )

    account = Account(credentials, tenant_id=OUTLOOK_TENANT_ID, token_backend=token_backend)

    print("\n[Setup] Starting Outlook OAuth flow...")

    # Scopes needed for reading and sending email. NOTE: the O365 param is
    # 'requested_scopes' (not 'scopes') — passing 'scopes' is silently ignored
    # and the flow fails with "Must provide at least one scope".
    scopes = ['basic', 'message_all']
    redirect_url = 'http://localhost:8000'

    if account.authenticate(requested_scopes=scopes, redirect_uri=redirect_url,
                            handle_consent=_console_consent):
        print("\n============================================================")
        print("  SUCCESS! Authentication complete.")
        print("  outlook_token.txt has been generated in the project root.")
        print("============================================================\n")
    else:
        print("\n[ERROR] Authentication failed.")


if __name__ == "__main__":
    main()
