"""Plain DNS for Chromium and VLESS inside this container, never the host."""
import json
import os
import tempfile
import ipaddress
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.config import get_settings


class DNSSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: Literal['system', 'custom'] = 'system'
    servers: list[str] = Field(default_factory=list, max_length=3)

    @model_validator(mode='after')
    def validate_servers(self):
        if self.mode == 'system':
            self.servers = []
            return self
        if not self.servers:
            raise ValueError('حداقل یک IP سرور DNS وارد کنید.')
        normalized = []
        for value in self.servers:
            value = value.strip()
            try:
                address = ipaddress.ip_address(value)
                valid = not (address.is_unspecified or address.is_multicast or '%' in value)
                value = str(address)
            except ValueError:
                valid = False
            if not valid:
                raise ValueError('DNS باید IP معتبر بدون لینک یا پورت باشد؛ مانند 8.8.8.8 یا 1.1.1.1')
            if value not in normalized:
                normalized.append(value)
        self.servers = normalized
        return self


def dns_settings(saved):
    dns = saved.get('dns') or {}
    if dns.get('mode') == 'doh':
        # Migrate old provider selections, never resolve arbitrary DoH hosts.
        providers = {'dns.google':'8.8.8.8', 'dns.quad9.net':'9.9.9.9',
                     'cloudflare-dns.com':'1.1.1.1', 'one.one.one.one':'1.1.1.1'}
        servers = []
        for value in dns.get('servers', []):
            host = urlsplit(value).hostname or ''
            servers.append(providers.get(host, host))
        dns = {'mode':'custom', 'servers':servers}
    return DNSSettings.model_validate(dns).model_dump()


def chromium_dns_policy(saved):
    # No templates: legacy secure-mode templates caused BAD_SECURE_CONFIG.
    return {'DnsOverHttpsMode': 'off'}


RESOLV_PATH = Path('/etc/resolv.conf')
SYSTEM_RESOLV_PATH = Path('/dev/shm/shared-browser-system-resolv.conf')


def configure_container_dns(saved):
    dns = dns_settings(saved)
    if not Path('/.dockerenv').exists():
        if dns['mode'] != 'system':
            raise ValueError('تنظیم DNS معمولی فقط داخل کانتینر Docker پشتیبانی می‌شود؛ DNS میزبان تغییر نمی‌کند.')
        return
    # /dev/shm is reset on container recreation. Capture Docker's resolver once
    # per container so switching back to system never restores a custom value.
    if not SYSTEM_RESOLV_PATH.exists():
        with open(SYSTEM_RESOLV_PATH, 'x', opener=lambda name, flags: os.open(name, flags, 0o600)) as backup:
            backup.write(RESOLV_PATH.read_text())
    original = SYSTEM_RESOLV_PATH.read_text()
    if dns['mode'] == 'system':
        content = original
    else:
        search = [line for line in original.splitlines() if line.startswith(('search ', 'domain '))]
        content = '# Managed by shared-browser: plain DNS, container only\n' + ''.join(
            f'nameserver {ip}\n' for ip in dns['servers']) + '\n'.join(search) + '\noptions timeout:2 attempts:2\n'
    # Docker bind-mounts this file; atomic rename is not supported. All browser
    # and proxy processes are stopped by maintenance while it is replaced.
    with RESOLV_PATH.open('w') as file:
        file.write(content)


def write_chromium_dns_policy(saved):
    policy = chromium_dns_policy(saved)
    # Dedicated file: never overwrite password or other administrator policies.
    for directory in ['/etc/chromium/policies/managed', '/etc/opt/chrome/policies/managed']:
        path = Path(directory); path.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.browser-dns-', dir=path)
        try:
            with os.fdopen(fd, 'w') as file:
                json.dump(policy, file)
            os.chmod(temporary, 0o644)
            os.replace(temporary, path / 'shared-browser-dns.json')
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def configure_xray_dns(config, saved):
    dns = dns_settings(saved)
    if dns['mode'] == 'system':
        return config
    # DNS must bootstrap the VPN itself: send plain DNS to the selected IPs
    # directly, never recursively through the tunnel or a different resolver.
    config['routing']['rules'].insert(0, {'type':'field', 'inboundTag':['dns-query'], 'outboundTag':'direct'})
    ipv4_only = get_settings().browser_proxy_ipv4_only
    strategy = 'ForceIPv4' if ipv4_only else 'ForceIP'
    config['dns'] = {'tag': 'dns-query', 'servers': dns['servers'],
                     'queryStrategy': 'UseIPv4' if ipv4_only else 'UseIP', 'disableFallback': True}
    # Resolve public destination names here, not using the remote VPN server's
    # unknown resolver. Failure must not silently defer DNS to that server.
    config['outbounds'][0]['targetStrategy'] = strategy
    config['outbounds'][0]['streamSettings']['sockopt'] = {'domainStrategy': strategy}
    config['outbounds'][1]['settings']['domainStrategy'] = strategy
    return config
