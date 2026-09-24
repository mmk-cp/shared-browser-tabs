"""Admin-managed VLESS transport. Secrets never appear in status or process args."""
import ast
import asyncio
import json
import logging
import os
import re
import tempfile
import uuid
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from app.config import get_settings

logger = logging.getLogger(__name__)
PORT = 10808
PRIVATE_IPS = ['127.0.0.0/8', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16',
               '169.254.0.0/16', '100.64.0.0/10', '::1/128', 'fc00::/7', 'fe80::/10']
BYPASS = ';'.join(['localhost', '*.localhost', '<local>', '*.local', *PRIVATE_IPS])


def normalize_vless_uri(uri):
    """Undo chat/Markdown mailto decoration without decoding query parameters."""
    uri = uri.strip().strip('`')
    uri = re.sub(r'\[([^\]\r\n]+)\]\(mailto:[^\)\r\n]+\)', r'\1', uri)
    return uri.replace('vless\\://', 'vless://').replace('\\_', '_')


def vless_config(uri):
    """Whitelist supported URI transports instead of accepting arbitrary Xray JSON."""
    try:
        url = urlsplit(normalize_vless_uri(uri))
        if url.scheme != 'vless' or not url.hostname or not url.port or url.password:
            raise ValueError()
        user_id = str(uuid.UUID(unquote(url.username or '')))
        params = parse_qs(url.query, keep_blank_values=True)
        def value(key, default=''):
            values = params.get(key, [default])
            if len(values) != 1:
                raise ValueError()
            return values[0]
        network = value('type', 'tcp')
        security = value('security', 'none')
        if network not in {'ws', 'tcp', 'grpc'} or security not in {'none', 'tls', 'reality'}:
            raise ValueError('فقط VLESS با انتقال WS، TCP یا gRPC و TLS/Reality پشتیبانی می‌شود.')
        if value('encryption', 'none') != 'none' or value('headerType', 'none') != 'none':
            raise ValueError('encryption یا headerType این لینک پشتیبانی نمی‌شود.')
        flow = value('flow')
        if flow not in {'', 'xtls-rprx-vision'} or (flow and network != 'tcp'):
            raise ValueError('flow این لینک پشتیبانی نمی‌شود.')
        stream = {'network': network, 'security': security}
        insecure = value('allowInsecure').lower() in {'1', 'true'} or value('insecure').lower() in {'1', 'true'}
        if security == 'tls':
            stream['tlsSettings'] = {'serverName': value('sni', value('host', url.hostname)),
                                     'allowInsecure': insecure, 'fingerprint': value('fp', 'chrome')}
            if value('alpn'):
                stream['tlsSettings']['alpn'] = value('alpn').split(',')
        elif security == 'reality':
            if not value('pbk') or not value('sni'):
                raise ValueError('کلید عمومی و SNI برای Reality لازم است.')
            stream['realitySettings'] = {'serverName': value('sni'), 'fingerprint': value('fp', 'chrome'),
                'publicKey': value('pbk'), 'shortId': value('sid'), 'spiderX': value('spx', '/')}
        if network == 'ws':
            headers = {}
            if value('headers'):
                try:
                    headers = json.loads(value('headers'))
                except json.JSONDecodeError:
                    headers = ast.literal_eval(value('headers'))
                if (not isinstance(headers, dict) or len(headers) > 30 or
                        any(not isinstance(k, str) or not isinstance(v, str) or
                            '\r' in k + v or '\n' in k + v for k, v in headers.items())):
                    raise ValueError()
            stream['wsSettings'] = {'path': value('path', '/'), 'host': value('host'), 'headers': headers}
        elif network == 'grpc':
            stream['grpcSettings'] = {'serviceName': value('serviceName'),
                                      'multiMode': value('mode') == 'multi'}
        user = {'id': user_id, 'encryption': 'none'}
        if flow:
            user['flow'] = flow
        config = {
            'log': {'loglevel': 'none'},
            'inbounds': [{'listen': '127.0.0.1', 'port': PORT, 'protocol': 'http', 'settings': {}}],
            'outbounds': [
                {'tag': 'proxy', 'protocol': 'vless', 'settings': {'vnext': [
                    {'address': url.hostname, 'port': url.port, 'users': [user]}]}, 'streamSettings': stream},
                {'tag': 'direct', 'protocol': 'freedom', 'settings': {}},
            ],
            'routing': {'domainStrategy': 'IPIfNonMatch', 'rules': [
                {'type': 'field', 'domain': ['localhost', 'domain:localhost', 'domain:local'], 'outboundTag': 'direct'},
                {'type': 'field', 'ip': PRIVATE_IPS, 'outboundTag': 'direct'},
            ]},
        }
        return config, {'server': url.hostname, 'port': url.port, 'transport': network,
                        'insecure': insecure and security == 'tls'}
    except (ValueError, TypeError, SyntaxError, RecursionError) as error:
        # Never echo a credential-bearing URI or parser exception to logs/client.
        message = str(error) if str(error).startswith(('فقط ', 'encryption ', 'flow ', 'کلید ')) else 'لینک VLESS معتبر نیست؛ لینک خام و کامل را وارد کنید.'
        raise ValueError(message) from None


class ProxyManager:
    def __init__(self):
        self.saved = {'enabled': False, 'uri': ''}
        self.process = None
        self.runtime = None
        self.load_failed = False

    def load(self):
        path = Path(get_settings().browser_proxy_file)
        if path.exists():
            try:
                data = json.loads(path.read_text())
                if type(data.get('enabled')) is not bool or not isinstance(data.get('uri'), str):
                    raise ValueError()
                if data['uri']:
                    vless_config(data['uri'])
                if data['enabled'] and not data['uri']:
                    raise ValueError()
                self.saved = data
            except Exception:
                # Fail closed, but keep the admin panel available to repair it.
                self.saved = {'enabled': True, 'uri': ''}
                self.load_failed = True
                logger.error('PROXY_SETTINGS_UNREADABLE: repair from admin panel')

    def status(self):
        summary = vless_config(self.saved['uri'])[1] if self.saved['uri'] else {}
        return {'enabled': self.saved['enabled'], 'configured': bool(self.saved['uri']),
                'running': self.process is not None and self.process.returncode is None,
                'load_failed': self.load_failed, **summary}

    def browser_args(self):
        if not self.saved['enabled']:
            return []
        return [f'--proxy-server=http://127.0.0.1:{PORT}', f'--proxy-bypass-list={BYPASS}',
                '--disable-quic', '--force-webrtc-ip-handling-policy=disable_non_proxied_udp']

    async def stop(self):
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        self.process = None
        if self.runtime:
            self.runtime.cleanup()
            self.runtime = None

    async def start(self):
        if not self.saved['enabled']:
            return
        config, _ = vless_config(self.saved['uri'])
        self.runtime = tempfile.TemporaryDirectory(prefix='sbt-proxy-', dir='/dev/shm')
        path = Path(self.runtime.name) / 'config.json'
        with open(path, 'x', opener=lambda name, flags: os.open(name, flags, 0o600)) as file:
            json.dump(config, file)
        try:
            check = await asyncio.create_subprocess_exec('xray', 'run', '-test', '-config', str(path),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            try:
                code = await asyncio.wait_for(check.wait(), 8)
            except asyncio.TimeoutError:
                check.kill(); await check.wait()
                raise ValueError('اعتبارسنجی تنظیمات پروکسی به پایان نرسید.') from None
            if code:
                raise ValueError('Xray این تنظیمات را نپذیرفت؛ نوع اتصال و پارامترهای لینک را بررسی کنید.')
            self.process = await asyncio.create_subprocess_exec('xray', 'run', '-config', str(path),
                env={**os.environ, 'GOMEMLIMIT': '192MiB'},
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            for _ in range(40):
                await asyncio.sleep(.1)
                if self.process.returncode is not None:
                    break
                try:
                    _, writer = await asyncio.wait_for(asyncio.open_connection('127.0.0.1', PORT), .2)
                    writer.close(); await writer.wait_closed()
                    return
                except (OSError, asyncio.TimeoutError):
                    pass
            raise ValueError('سرویس پروکسی راه‌اندازی نشد؛ تنظیمات قبلی برگردانده می‌شود.')
        except Exception:
            await self.stop()
            raise

    async def apply(self, candidate):
        previous = self.saved
        await self.stop()
        self.saved = candidate
        try:
            await self.start()
            path = Path(get_settings().browser_proxy_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix='.browser-proxy-', dir=path.parent)
            try:
                with os.fdopen(fd, 'w') as file:
                    json.dump(candidate, file)
                    file.flush(); os.fsync(file.fileno())
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            self.load_failed = False
        except Exception:
            await self.stop()
            self.saved = previous
            try:
                await self.start()
            except Exception:
                logger.error('PROXY_ROLLBACK_START_FAILED; browser remains fail-closed')
            raise


proxy_manager = ProxyManager()
