"""Publish generated state without force-push; retry transient GitHub failures.

Explicit latest-branch checkout in writer workflows avoids queued-event stale
heads. If another writer still advances the branch, rebase only when Git can
merge it cleanly. Conflicting research/delivery state always fails closed.
"""
import argparse
import subprocess
import time


def push_with_retry(attempts=3, sleep=time.sleep):
    branch = subprocess.check_output(['git', 'branch', '--show-current'], text=True).strip()
    if not branch:
        raise RuntimeError('Publication requires an attached branch')
    for attempt in range(attempts):
        result = subprocess.run(['git', 'push', 'origin', f'HEAD:refs/heads/{branch}'])
        if result.returncode == 0:
            return
        if attempt + 1 == attempts:
            break
        sleep(2 ** (attempt+1))
        fetched = subprocess.run(['git', 'fetch', 'origin', branch])
        if fetched.returncode:
            continue
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
        # A lost push response may still have advanced the remote. Do not replay.
        if subprocess.run(['git', 'merge-base', '--is-ancestor', head, 'FETCH_HEAD']).returncode == 0:
            return
        if subprocess.run(['git', 'rebase', 'FETCH_HEAD']).returncode:
            subprocess.run(['git', 'rebase', '--abort'], check=True)
            raise RuntimeError('發布遇到版本衝突；保留成果，不覆蓋遠端資料')
    raise RuntimeError('GitHub 發布重試仍失敗；請由保存的成果重試，不必重新掃描')


if __name__ == '__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    push_with_retry()
