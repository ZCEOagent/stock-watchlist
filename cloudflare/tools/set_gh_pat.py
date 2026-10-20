"""Upload the GitHub token for the dispatcher from the clipboard, after verifying it.

Why: on 2026-10-20 the pasted GH_PAT was 26 characters (a fine-grained token is
~93) and every workflow_dispatch returned HTTP 401. This reads the clipboard,
checks the token against GitHub, and only then stores it as the Worker secret.
The token is never printed or written anywhere except a temp file that is
deleted immediately after `wrangler secret bulk` reads it.

Usage: copy the token in the browser, then run from the cloudflare folder:
    python tools/set_gh_pat.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

REPO = 'ZCEOagent/stock-watchlist'
PATTERN = re.compile(r'^(github_pat_[A-Za-z0-9_]{70,}|ghp_[A-Za-z0-9]{36})$')


def clipboard():
    out = subprocess.run(['powershell.exe', '-NoProfile', '-STA', '-Command', 'Get-Clipboard -Raw'],
                         capture_output=True, text=True, encoding='utf-8')
    return (out.stdout or '').strip()


def check(token, path):
    request = urllib.request.Request(f'https://api.github.com/{path}', headers={
        'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
        'User-Agent': 'stock-watchlist-setup', 'X-GitHub-Api-Version': '2022-11-28'})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def main():
    token = clipboard()
    if not PATTERN.match(token):
        print(f'Clipboard does not look like a GitHub token (length {len(token)}; expected github_pat_... about 93 chars).')
        print('Copy the token again from the GitHub page and rerun. Nothing was uploaded.')
        return 1
    status = check(token, f'repos/{REPO}/actions/workflows')
    if status != 200:
        hint = {401: 'token is invalid or expired', 403: 'token lacks Actions permission',
                404: 'token is not granted access to this repository'}.get(status, 'unexpected response')
        print(f'GitHub rejected the token: HTTP {status} ({hint}). Nothing was uploaded.')
        return 1
    print('GitHub accepted the token (repository and Actions read verified).')
    fd, path = tempfile.mkstemp(suffix='.json')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump({'GH_PAT': token}, handle)
        result = subprocess.run(['npx.cmd', '--yes', 'wrangler@4', 'secret', 'bulk', path])
    finally:
        os.remove(path)
    if result.returncode != 0:
        print('wrangler failed; the secret may not be updated.')
        return result.returncode
    print('GH_PAT uploaded to the Worker. Clear the clipboard if you like (copy any other text).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
