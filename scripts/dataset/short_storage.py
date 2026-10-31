#!/usr/bin/env python3
"""Mount a bounded macOS dataset filesystem before any campaign writes."""
import plistlib
import subprocess
import sys
from pathlib import Path

CAPACITY = 450 * 1024**3  # 483.2 GB, leaving room under 500 GB for image metadata.
ROOT = Path(__file__).resolve().parents[2]


def ensure_storage(root=ROOT):
    if sys.platform != 'darwin':
        raise RuntimeError('short collection requires the bounded macOS dataset disk image')
    mount = root / 'results'
    image = root / 'local' / 'short-dataset.sparseimage'
    image.parent.mkdir(parents=True, exist_ok=True)
    mount.mkdir(exist_ok=True)
    if mount.is_symlink():
        raise RuntimeError('results must not be a symlink')
    if not mount.is_mount():
        if any(mount.iterdir()):
            raise RuntimeError('results is not empty; move existing results before mounting the bounded store')
        if not image.exists():
            subprocess.run(['hdiutil', 'create', '-size', '450g', '-type', 'SPARSE',
                            '-fs', 'APFS', '-volname', 'WirelessDataset', str(image)], check=True)
        subprocess.run(['hdiutil', 'attach', str(image), '-mountpoint', str(mount),
                        '-nobrowse'], check=True)
    info = plistlib.loads(subprocess.check_output(['diskutil', 'info', '-plist', str(mount)]))
    images = plistlib.loads(subprocess.check_output(['hdiutil', 'info', '-plist']))
    matching = [item for item in images['images']
                if Path(item['image-path']).resolve() == image.resolve()
                and any(entity.get('mount-point') == str(mount)
                        for entity in item.get('system-entities', []))]
    if not matching or not 0 < info['TotalSize'] <= CAPACITY:
        raise RuntimeError('results must be mounted from the 450 GiB dataset image')
    print(f'bounded dataset store: {mount} ({info["TotalSize"]} bytes maximum)')


if __name__ == '__main__':
    ensure_storage()
