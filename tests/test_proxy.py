import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode
from app.services.proxy_manager import ProxyManager, vless_config, BYPASS

LINK = 'vless://00000000-0000-4000-8000-000000000001@example.com:443?'


class VlessTests(unittest.TestCase):
    def test_chat_formatted_link(self):
        formatted = r'vless\://00000000-[0000-4000-8000-000000000001@example.com](mailto:0000-4000-8000-000000000001@example.com):443?type=ws&security=tls#QA\_test'
        self.assertEqual(vless_config(formatted)[1]['server'], 'example.com')

    def test_ws_tls_headers_and_private_bypass(self):
        config, summary = vless_config(LINK + urlencode({'type': 'ws', 'security': 'tls',
            'sni': 'edge.example.com', 'host': 'edge.example.com', 'path': '/test',
            'allowInsecure': 'true', 'alpn': 'http/1.1', 'fp': 'chrome',
            'headers': "{'User-Agent': 'QA browser', 'Pragma': 'no-cache'}"}))
        stream = config['outbounds'][0]['streamSettings']
        self.assertEqual(stream['wsSettings']['headers']['User-Agent'], 'QA browser')
        self.assertTrue(stream['tlsSettings']['allowInsecure'])
        self.assertTrue(summary['insecure'])
        self.assertEqual(config['inbounds'][0]['listen'], '127.0.0.1')
        self.assertIn('192.168.0.0/16', BYPASS)
        self.assertIn('::1/128', BYPASS)

    def test_rejects_invalid_or_unsupported_without_echoing_secret(self):
        for uri in ['vless://secret@host:443', LINK + 'type=kcp', LINK + 'type=ws&headers=malformed',
                    LINK + 'type=ws&type=tcp', LINK + 'security=reality']:
            with self.assertRaises(ValueError) as error:
                vless_config(uri)
            self.assertNotIn('secret@', str(error.exception))

    def test_reality_and_grpc(self):
        config, _ = vless_config(LINK + 'security=reality&pbk=test&sni=example.com&flow=xtls-rprx-vision')
        self.assertEqual(config['outbounds'][0]['streamSettings']['realitySettings']['publicKey'], 'test')
        config, _ = vless_config(LINK + 'type=grpc&serviceName=qa&security=tls')
        self.assertEqual(config['outbounds'][0]['streamSettings']['grpcSettings']['serviceName'], 'qa')


class ProxyLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_creates_private_runtime_config_and_cleans_up_on_invalid_core_config(self):
        manager = ProxyManager()
        manager.saved = {'enabled': True, 'uri': LINK + 'security=tls&type=ws'}
        process = AsyncMock()
        process.wait.return_value = 1
        with patch('asyncio.create_subprocess_exec', return_value=process):
            with self.assertRaisesRegex(ValueError, 'Xray'):
                await manager.start()
        self.assertIsNone(manager.runtime)

    async def test_persists_private_file_and_rolls_back_failure(self):
        manager = ProxyManager()
        manager.start = AsyncMock()
        manager.stop = AsyncMock()
        with tempfile.TemporaryDirectory() as root, patch('app.services.proxy_manager.get_settings') as settings:
            path = Path(root) / 'proxy.json'; settings.return_value.browser_proxy_file = str(path)
            saved = {'enabled': True, 'uri': LINK + 'security=tls'}
            await manager.apply(saved)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn('uri', manager.status())
            self.assertIn('--disable-quic', manager.browser_args())
            other = ProxyManager(); other.load(); self.assertEqual(other.saved, saved)
            manager.start.side_effect = [ValueError('QA failed'), None]
            with self.assertRaises(ValueError):
                await manager.apply({'enabled': True, 'uri': LINK + 'type=ws'})
            self.assertEqual(manager.saved, saved)
            self.assertEqual(json.loads(path.read_text()), saved)


if __name__ == '__main__': unittest.main()
