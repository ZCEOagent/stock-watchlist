"""Restore latest same-branch artifact. GitHub CLI handles download authentication.

Fails closed: a listing/download error or a missing artifact exits non-zero, because
running on an empty store silently drops dedupe receipts and the slot ledger. An
empty baseline is only allowed with an explicit --bootstrap.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

PER_PAGE = 100
MAX_PAGES = 50  # 5000 artifacts; far above 90-day retention for one name.


class RestoreError(RuntimeError):
    pass


def gh_json(path):
    try:
        return json.loads(subprocess.check_output(['gh', 'api', path], text=True))
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        raise RestoreError(f'artifact listing failed: {path}: {exc}') from exc


def list_artifacts(repo, name):
    """Every page: API ordering is undocumented, so the newest may sit past page one."""
    found = []
    for page in range(1, MAX_PAGES + 1):
        body = gh_json(f'repos/{repo}/actions/artifacts?name={name}&per_page={PER_PAGE}&page={page}')
        batch = body.get('artifacts')
        if not isinstance(batch, list):
            raise RestoreError(f'artifact listing malformed on page {page}')
        found.extend(batch)
        if len(batch) < PER_PAGE or len(found) >= body.get('total_count', 0):
            return found
    raise RestoreError(f'artifact listing exceeded {MAX_PAGES} pages')


def latest_artifact(artifacts, name, branch):
    matches = [a for a in artifacts if a.get('name', name) == name and not a.get('expired')
               and (a.get('workflow_run') or {}).get('head_branch') == branch]
    return max(matches, key=lambda a: a['created_at']) if matches else None


def restore(name, destination, bootstrap=False):
    repo = os.environ['GITHUB_REPOSITORY']
    branch = os.environ.get('RADAR_DEFAULT_BRANCH', 'master')
    artifact = latest_artifact(list_artifacts(repo, name), name, branch)
    if artifact is None:
        if bootstrap:
            print(f'{name}: no retained artifact; --bootstrap given, initializing empty baseline')
            return None
        raise RestoreError(f'{name}: no retained artifact on {branch}; refusing empty state '
                           '(dispatch with bootstrap=true only for a deliberate first run)')
    Path(destination).mkdir(parents=True, exist_ok=True)
    run_id = str(artifact['workflow_run']['id'])
    try:
        subprocess.run(['gh', 'run', 'download', run_id, '--repo', repo,
                        '--name', name, '--dir', str(destination)], check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RestoreError(f'{name}: download from run {run_id} failed: {exc}') from exc
    if not any(Path(destination).iterdir()):
        raise RestoreError(f'{name}: download from run {run_id} produced no files')
    print(f'{name}: restored artifact {artifact.get("id")} from run {run_id} ({artifact["created_at"]})')
    return artifact


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name', default='market-radar-state')
    parser.add_argument('--destination', default='.radar')
    parser.add_argument('--bootstrap', action='store_true',
                        help='allow an empty baseline when no artifact exists (first run only)')
    args = parser.parse_args(argv)
    try:
        restore(args.name, args.destination, args.bootstrap)
    except RestoreError as exc:
        print(f'::error::{exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
