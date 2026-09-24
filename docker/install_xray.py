"""Install a pinned, checksum-verified official Xray binary (no geo datasets)."""
import hashlib
import io
import platform
import urllib.request
import zipfile
from pathlib import Path

assets = {
    'x86_64': ('64', 'df22ad60c1251c9fb63d7f85b3677872edf61c6715eba64b06adbfec658f4938'),
    'aarch64': ('arm64-v8a', '67ea391812f90fa17e29a60c8faa834bcf30ead0f84eb6b2bbd2d398d56b759e'),
}
asset, digest = assets[platform.machine()]
# New Xray releases removed allowInsecure (some enforce a date-based cutoff).
# Pin this compatible release to honor existing admin links with an explicit UI warning.
url = f'https://github.com/XTLS/Xray-core/releases/download/v25.10.15/Xray-linux-{asset}.zip'
with urllib.request.urlopen(url, timeout=90) as response:
    archive = response.read()
if hashlib.sha256(archive).hexdigest() != digest:
    raise RuntimeError('Xray checksum mismatch')
with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
    binary = Path('/usr/local/bin/xray')
    binary.write_bytes(bundle.read('xray'))
    binary.chmod(0o755)
