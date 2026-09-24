"""Quick offline compatibility check against bundled Xray; no real URI/traffic."""
import json
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlencode
from app.services.proxy_manager import vless_config
from app.services.browser_dns import configure_xray_dns

base = 'vless://00000000-0000-4000-8000-000000000001@example.com:443?'
for transport in ['ws', 'tcp', 'grpc']:
    config, _ = vless_config(base + urlencode({'type': transport, 'security': 'tls',
        'allowInsecure': 'true', 'sni': 'edge.example.com', 'host': 'edge.example.com',
        'path': '/qa', 'alpn': 'http/1.1', 'fp': 'chrome',
        'headers': "{'User-Agent': 'QA browser', 'Pragma': 'no-cache'}"}))
    configure_xray_dns(config, {'dns': {'mode':'custom','servers':['8.8.8.8','1.1.1.1']}})
    with tempfile.TemporaryDirectory(prefix='qa-xray-', dir='/dev/shm') as root:
        path = Path(root) / 'config.json'; path.write_text(json.dumps(config))
        result = subprocess.run(['xray', 'run', '-test', '-config', str(path)],
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stdout + result.stderr
print('PASS bundled Xray accepts WS/TLS custom headers, TCP/TLS and gRPC/TLS')
