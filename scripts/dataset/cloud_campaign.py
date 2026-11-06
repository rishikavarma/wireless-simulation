"""Selected dataset campaign on the collector's persistent disk."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CAMPAIGNS = {
    'short': ('short-distributed-300-20k', 20000, 320),
    'full': ('full-distributed', 40000, 640),
}


def selected(root=ROOT):
    path = root/'local/cloud-campaign-mode.txt'
    mode = path.read_text().strip() if path.exists() else 'short'
    if mode not in CAMPAIGNS:
        raise ValueError(f'unknown cloud campaign mode: {mode!r}')
    name, runs, parts = CAMPAIGNS[mode]
    receipt = root/'results'/('cloud-complete.json' if mode == 'short'
                              else f'{name}-cloud-complete.json')
    return dict(mode=mode, name=name, runs=runs, parts=parts, receipt=receipt)
