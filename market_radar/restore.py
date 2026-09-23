"""Restore latest same-branch artifact. GitHub CLI handles download authentication."""
import json
import os
from pathlib import Path
import subprocess


def restore(name, destination):
    repo = os.environ['GITHUB_REPOSITORY']
    branch = os.environ.get('RADAR_DEFAULT_BRANCH', 'master')
    raw = subprocess.check_output(['gh', 'api', f'repos/{repo}/actions/artifacts?name={name}&per_page=50'], text=True)
    artifacts = json.loads(raw)['artifacts']
    matches = [a for a in artifacts if not a['expired'] and a.get('workflow_run', {}).get('head_branch') == branch]
    if not matches:
        print(f'{name}: no retained artifact; initializing baseline')
        return
    artifact = max(matches, key=lambda a: a['created_at'])
    Path(destination).mkdir(parents=True, exist_ok=True)
    subprocess.run(['gh', 'run', 'download', str(artifact['workflow_run']['id']), '--repo', repo,
                    '--name', name, '--dir', destination], check=True)


if __name__ == '__main__':
    restore('market-radar-state', '.radar')
