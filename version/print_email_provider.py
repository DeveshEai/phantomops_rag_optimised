"""
print_email_provider.py — prints the active email provider (outlook|gmail).

Exists purely so start.bat can query core.config.get_email_provider() inside
a `for /f` backtick command. cmd.exe's for/f backtick parser is unreliable
with an inline `python -c "...; ...(...)..."` one-liner (multiple embedded
quotes/semicolons/parens in a single quoted argument) — a plain script
invocation with no quoting inside the command string is far more robust.

STDOUT carries ONE line and nothing else: the provider name. Importing
core.config prints its own startup chatter ("[CONFIG] Loaded agent_config.yaml",
and on failure a "[CONFIG] Failed to read ..." line), which would otherwise
land on stdout too and leave `for /f` to iterate MULTIPLE lines — it assigns
on every line, so the value start.bat ends up with depends on which line
happens to come last. That silently mis-detects the provider (and start.bat
then opens the WRONG mailbox's sign-in). Diverting the chatter to stderr
keeps it visible to a human running this by hand while making stdout
unambiguous for the caller.
"""
import contextlib
import sys

with contextlib.redirect_stdout(sys.stderr):
    from core.config import get_email_provider
    _provider = get_email_provider()

print(_provider)
