import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode
from app.services.proxy_manager import ProxyManager, vless_config, BYPASS
from app.services.browser_dns import DNSSettings, chromium_dns_policy, configure_xray_dns, configure_container_dns, dns_settings
import httpx

LINK = 'vless://00000000-0000-4000-8000-000000000001@example.com:443?'


class VlessTests(unittest.TestCase):
    def test_dns_validation_and_both_resolvers(self):
        saved={'dns':{'mode':'custom','servers':['8.8.8.8','1.1.1.1']}}
        self.assertEqual(chromium_dns_policy(saved),{'DnsOverHttpsMode':'off'})
        config,_=vless_config(LINK+'security=tls')
        configure_xray_dns(config,saved)
        self.assertEqual(config['outbounds'][0]['targetStrategy'],'ForceIPv4')
        self.assertEqual(config['dns']['servers'],['8.8.8.8','1.1.1.1'])
        self.assertEqual(config['outbounds'][0]['streamSettings']['sockopt']['domainStrategy'],'ForceIPv4')
        self.assertEqual(config['dns']['queryStrategy'],'UseIPv4')
        self.assertEqual(config['routing']['rules'][0]['outboundTag'],'direct')
        self.assertEqual(chromium_dns_policy({})['DnsOverHttpsMode'],'off')
        for value in ['8.8.8.8:53','http://dns.test/dns-query','https://dns.google/dns-query','0.0.0.0','224.0.0.1']:
            with self.assertRaises(ValueError): DNSSettings(mode='custom',servers=[value])
        self.assertEqual(dns_settings({'dns':{'mode':'doh','servers':['https://dns.google/dns-query']}}),
                         {'mode':'custom','servers':['8.8.8.8']})

    def test_explicit_dual_stack_opt_in(self):
        config,_=vless_config(LINK+'security=tls')
        with patch('app.services.browser_dns.get_settings') as settings:
            settings.return_value.browser_proxy_ipv4_only=False
            configure_xray_dns(config,{'dns':{'mode':'custom','servers':['8.8.8.8']}})
        self.assertEqual(config['dns']['queryStrategy'],'UseIP')
        self.assertEqual(config['outbounds'][0]['targetStrategy'],'ForceIP')

    def test_container_dns_switch_and_restore_only_temporary_files(self):
        with tempfile.TemporaryDirectory() as root:
            resolv=Path(root)/'resolv.conf'; backup=Path(root)/'system.conf'
            original='nameserver 127.0.0.11\nsearch internal.test\n'
            resolv.write_text(original)
            with patch('app.services.browser_dns.RESOLV_PATH',resolv),patch('app.services.browser_dns.SYSTEM_RESOLV_PATH',backup):
                configure_container_dns({'dns':{'mode':'custom','servers':['8.8.8.8']}})
                self.assertIn('nameserver 8.8.8.8',resolv.read_text())
                self.assertNotIn('127.0.0.11',resolv.read_text())
                configure_container_dns({'dns':{'mode':'custom','servers':['1.1.1.1']}})
                configure_container_dns({'dns':{'mode':'system','servers':[]}})
                self.assertEqual(resolv.read_text(),original)

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
    async def test_network_probe_success_and_failure(self):
        real_client=httpx.AsyncClient
        def client(**kwargs):
            self.assertEqual(kwargs['proxy'],'http://127.0.0.1:10808')
            self.assertFalse(kwargs['trust_env'])
            kwargs.pop('proxy')
            return real_client(**kwargs,transport=httpx.MockTransport(lambda request:httpx.Response(200,json={'ip':'203.0.113.12'})))
        with patch('app.services.proxy_manager.httpx.AsyncClient',side_effect=client):
            result=await ProxyManager().test_connection()
        self.assertTrue(result['ok']); self.assertEqual(result['ip'],'203.0.113.12')
        def failed_client(**kwargs):
            kwargs.pop('proxy')
            return real_client(**kwargs,transport=httpx.MockTransport(lambda request:httpx.Response(403)))
        with patch('app.services.proxy_manager.httpx.AsyncClient',side_effect=failed_client):
            result=await ProxyManager().test_connection()
        self.assertFalse(result['ok']); self.assertEqual(result['http_status'],403)

    async def test_start_creates_private_runtime_config_and_cleans_up_on_invalid_core_config(self):
        manager = ProxyManager()
        manager.saved = {'enabled': True, 'uri': LINK + 'security=tls&type=ws'}
        process = AsyncMock()
        process.wait.return_value = 1
        with patch('asyncio.create_subprocess_exec', return_value=process),patch('app.services.proxy_manager.configure_container_dns'):
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
