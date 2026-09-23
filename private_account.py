"""Local private account CLI; refuses storage inside any Git checkout.

No default account, fabricated deposits, outbound requests or order placement.
Use --file outside the repository. Restrict that directory's OS permissions.
"""
import argparse
import json
from pathlib import Path
from storage import read_json, write_json
from portfolio import calculate, risk_state, size_order


def private_path(value):
    path = Path(value).expanduser().resolve()
    if any((parent / '.git').exists() for parent in [path.parent, *path.parent.parents]):
        raise ValueError('Private account storage must be outside every Git checkout')
    if path.suffix.lower() != '.json':
        raise ValueError('Private account file must be JSON')
    return path


def update_account(path, account, marks, as_of, additions=()):
    path = private_path(path)
    proposed = {**account, 'events': account.get('events', []) + list(additions)}
    snapshot = calculate(proposed['events'], marks, as_of)
    if snapshot['deposits'] <= 0:
        raise ValueError('An actual confirmed deposit is required')
    prior = account.get('risk', {})
    if account.get('as_of', '') > as_of:
        raise ValueError('Cannot overwrite newer account valuation')
    risk = risk_state(snapshot, account['policy'], prior)
    proposed.update(as_of=as_of, risk=risk, snapshot=snapshot)
    write_json(path, proposed)
    return proposed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', required=True)
    parser.add_argument('--as-of', required=True)
    parser.add_argument('--marks', required=True, help='Private JSON: stock ID -> {date, price}; {} if no holdings')
    parser.add_argument('--events', help='Private JSON list of new actual deposits/fills; IDs must be unique')
    parser.add_argument('--entry', type=float)
    parser.add_argument('--stop', type=float)
    parser.add_argument('--minimum-fee', type=float, help='Actual broker minimum per side, required for sizing')
    args = parser.parse_args()
    path = private_path(args.file)
    account = read_json(path, None)
    if not account or 'policy' not in account:
        raise ValueError('Create private account JSON with policy and events first; no implicit funds')
    marks = read_json(private_path(args.marks), {})
    additions = read_json(private_path(args.events), []) if args.events else []
    if any(x is not None for x in (args.entry, args.stop, args.minimum_fee)) and any(x is None for x in (args.entry, args.stop, args.minimum_fee)):
        raise ValueError('Sizing requires entry, stop and actual minimum fee')
    updated = update_account(path, account, marks, args.as_of, additions)
    result = {'account': updated['snapshot'], 'risk': updated['risk']}
    if args.entry is not None:
        result['proposed_order'] = size_order(updated['snapshot'], updated['risk'], args.entry, args.stop,
                                             account['policy'], minimum_fee=args.minimum_fee)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
