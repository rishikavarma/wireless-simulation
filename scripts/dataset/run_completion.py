"""Completed-run validation and immutable membership of sealed raw parts."""
import json
from pathlib import Path


def completed_run(root, campaign, record):
    prefix = record['prefix']
    if not prefix or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.' for c in prefix):
        raise ValueError('unsafe run prefix')
    try:
        status = json.loads((campaign/'logs'/(prefix+'.json')).read_text())
        return (status.get('prefix') == prefix and status.get('exit') in (0, 1)
                and all((root/'results'/prefix/(prefix+'-'+suffix)).is_file()
                        for suffix in ('tcp.csv', 'summary.txt', 'app.txt')))
    except (OSError, ValueError):
        return False


def validate_membership(group, assignment):
    planned = [r['prefix'] for r in group]
    retained = assignment['runs']
    omitted = assignment.get('omittedRuns', [])
    if (len(set(retained)) != len(retained) or len(set(omitted)) != len(omitted)
            or set(retained) & set(omitted) or set(retained) | set(omitted) != set(planned)
            or retained != [prefix for prefix in planned if prefix in set(retained)]):
        raise ValueError('archive run list differs from local plan')
    return [r for r in group if r['prefix'] in set(retained)]


def sealed_parts(campaign, parts):
    result = []
    for number, group in enumerate(parts):
        path = campaign/'sealed-parts'/f'{number:05d}.json'
        result.append(validate_membership(group, json.loads(path.read_text())) if path.exists() else group)
    return result
