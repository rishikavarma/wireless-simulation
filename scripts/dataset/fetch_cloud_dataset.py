#!/usr/bin/env python3
"""Fetch only the finalized dataset archive from the cloud collector via IAP."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from datetime import datetime, timezone

COLLECTOR = 'dataset-collector'
ZONE = 'us-central1-a'
PROJECT = 'cmps-infraai'
REMOTE_ROOT = '/srv/dataset/wireless-simulation/results'
NAME = 'short-distributed-300-20k.tar.gz'


def receipt_path(archive):
    return archive.with_name(archive.name+'.verified.json')


def save_receipt(archive, size, digest):
    with archive.open('rb') as source:
        os.fsync(source.fileno())
    value=dict(verified=True, artifact=NAME, project=PROJECT, zone=ZONE,
               collector=COLLECTOR, source=REMOTE_ROOT+'/'+NAME,
               bytes=size, sha256=digest, verifiedAt=datetime.now(timezone.utc).isoformat())
    target=receipt_path(archive)
    temporary=target.with_suffix('.json.tmp')
    with temporary.open('w') as output:
        json.dump(value,output,indent=2);output.write('\n');output.flush();os.fsync(output.fileno())
    os.replace(temporary,target)


def verify_downloaded_archive(archive):
    if archive.name != NAME or archive.is_symlink() or not archive.is_file():
        raise ValueError('the final local dataset archive must exist as a regular file')
    target=receipt_path(archive)
    if target.is_symlink():raise ValueError('verification receipt must not be a symlink')
    info=json.loads(target.read_text())
    if (info.get('verified') is not True or info.get('artifact') != NAME
            or info.get('project') != PROJECT or info.get('zone') != ZONE
            or info.get('collector') != COLLECTOR or info.get('source') != REMOTE_ROOT+'/'+NAME
            or archive.stat().st_size != info.get('bytes')):
        raise ValueError('local dataset verification receipt does not match this campaign')
    digest=hashlib.sha256()
    with archive.open('rb') as source:
        for chunk in iter(lambda:source.read(8*1024**2),b''):digest.update(chunk)
    if digest.hexdigest() != info.get('sha256'):
        raise ValueError('local dataset checksum mismatch; cloud resources retained')
    return info


def fetch(destination):
    # A checksum sidecar is published only after cloud consolidation completes.
    code = ("import json,pathlib; p=pathlib.Path(" + repr(REMOTE_ROOT+'/'+NAME) + "); "
            "s=p.with_name(p.name+'.sha256'); "
            "print(json.dumps(dict(size=p.stat().st_size,sha256=s.read_text().split()[0])))")
    import shlex
    metadata = json.loads(subprocess.check_output([
        'gcloud', 'compute', 'ssh', COLLECTOR, '--zone', ZONE, '--project', PROJECT,
        '--tunnel-through-iap', '--quiet', '--command', 'python3 -c '+shlex.quote(code)], text=True))
    size, expected = metadata['size'], metadata['sha256']
    if size <= 0 or len(expected) != 64:
        raise ValueError('invalid final artifact metadata')
    if size > 490_000_000_000:
        raise ValueError('final package exceeds the 490 GB local download limit; leave it on the collector')
    destination.mkdir(parents=True, exist_ok=True)
    final = destination/NAME
    if final.exists():
        raise FileExistsError(f'{final} already exists; refusing to download a second local copy')
    incoming = final.with_suffix(final.suffix+'.incoming')
    existing = incoming.stat().st_size if incoming.exists() else 0
    if shutil.disk_usage(destination).free + existing < size + 10_000_000_000:
        raise ValueError('not enough local space for final archive and 10 GB reserve')
    # Download to a single temporary file and rename after checksum verification.
    if incoming.exists():
        incoming.unlink()
    subprocess.run(['gcloud', 'compute', 'scp', '--zone', ZONE, '--project', PROJECT,
                    '--tunnel-through-iap', '--quiet',
                    COLLECTOR+':'+REMOTE_ROOT+'/'+NAME, str(incoming)], check=True)
    digest = hashlib.sha256()
    with incoming.open('rb') as source:
        for chunk in iter(lambda: source.read(8*1024**2), b''):
            digest.update(chunk)
    if incoming.stat().st_size != size or digest.hexdigest() != expected:
        raise ValueError('final archive checksum mismatch; download retained as .incoming')
    os.replace(incoming, final)
    save_receipt(final,size,expected)
    print(final)


def fetch_with_collector(destination):
    flags = ['--zone', ZONE, '--project', PROJECT, '--quiet']
    status = subprocess.check_output(['gcloud','compute','instances','describe',COLLECTOR,
                                      *flags,'--format=value(status)'], text=True).strip()
    restarted = status == 'TERMINATED'
    if status not in ('TERMINATED','RUNNING'):
        raise RuntimeError('collector is transitioning; retry once it is RUNNING or TERMINATED')
    if restarted:
        subprocess.run(['gcloud','compute','instances','start',COLLECTOR,*flags], check=True)
        # VM startup can precede the SSH daemon; let gcloud establish connectivity.
        subprocess.run(['gcloud','compute','ssh',COLLECTOR,*flags,'--tunnel-through-iap',
                        '--ssh-flag=-o ConnectionAttempts=30','--ssh-flag=-o ConnectTimeout=5',
                        '--command=true'], check=True)
    # Cancel a pending end-of-campaign shutdown before a potentially long download.
    subprocess.run(['gcloud','compute','ssh',COLLECTOR,*flags,'--tunnel-through-iap',
                    '--command=if systemctl is-active --quiet wireless-dataset.service; then echo "Campaign finalization is still active; retry shortly" >&2; exit 1; fi; sudo -n /usr/local/sbin/dataset-poweroff --cancel'], check=True)
    fetch(destination)
    # Leave the collector stopped after a verified download.
    subprocess.run(['gcloud','compute','instances','stop',COLLECTOR,*flags], check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, default=Path('local/final-dataset'))
    fetch_with_collector(parser.parse_args().destination)
