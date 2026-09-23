import base64
import json
import time
import unittest
from unittest.mock import AsyncMock, patch

from app.services.file_transfer import (
    FileTransfers, PageTransfers, Target, TransferError, decode_files, is_image,
)


def body(data=b'hello', name='test.txt', mime='text/plain'):
    return json.dumps({'files':[{'name':name,'type':mime,'data':base64.b64encode(data).decode()}]}).encode()


class DecodeTests(unittest.TestCase):
    def test_exact_bytes_and_basename(self):
        self.assertEqual(decode_files(body(b'\x00\xff', '../dir\\photo\x00.png')), [
            {'name':'photo_.png','mimeType':'text/plain','buffer':b'\x00\xff'}])

    def test_invalid_payloads(self):
        for payload in [b'null', b'[]', b'{}', b'{', b'{"files":[]}', b'{"files":[{"path":"/etc/passwd"}]}',
                        b'{"files":[{"name":"x","type":"x","data":"***"}]}']:
            with self.subTest(payload=payload), self.assertRaises(TransferError) as caught:
                decode_files(payload)
            self.assertEqual(caught.exception.status, 400)

    def test_limits(self):
        with patch('app.services.file_transfer.MAX_BYTES', 4), self.assertRaises(TransferError) as caught:
            decode_files(body())
        self.assertEqual(caught.exception.status, 413)
        item = json.loads(body())['files'][0]
        with self.assertRaises(TransferError):
            decode_files(json.dumps({'files':[item]*9}).encode())

    def test_image_signatures(self):
        self.assertTrue(is_image({'buffer':b'\x89PNG\r\n\x1a\n123', 'mimeType':'image/png'}))
        self.assertFalse(is_image({'buffer':b'<svg>bad</svg>', 'mimeType':'image/png'}))
        self.assertFalse(is_image({'buffer':b'\x89PNG\r\n\x1a\n', 'mimeType':'image/svg+xml'}))


class TargetsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.manager = FileTransfers()
        self.page = object()
        self.state = PageTransfers()
        self.manager.pages[self.page] = self.state
        self.element = AsyncMock()
        self.state.pending = Target('nonce','session','input',self.element,object(),time.monotonic()+300)

    async def test_binding_and_expiration(self):
        for page, session, token in [(object(),'session','nonce'),(self.page,'other','nonce'),(self.page,'session','other')]:
            with self.assertRaises(TransferError): self.manager.require(page,session,token)
        self.state.pending.expires = time.monotonic()-1
        with self.assertRaises(TransferError): self.manager.require(self.page,'session','nonce')

    async def test_once_only(self):
        await self.manager.deliver(self.page,'session','nonce',decode_files(body()))
        self.element.set_input_files.assert_awaited_once()
        with self.assertRaises(TransferError):
            await self.manager.deliver(self.page,'session','nonce',decode_files(body()))

    async def test_cancel_preserves_files(self):
        await self.manager.cancel(self.page,'session','nonce')
        self.element.set_input_files.assert_not_called()
        self.element.evaluate.assert_awaited_once()
        self.element.dispose.assert_awaited_once()
        self.assertIsNone(self.state.pending)

    async def test_paste_rejects_disguised_file(self):
        self.state.pending.kind = 'paste'
        with self.assertRaises(TransferError) as caught:
            await self.manager.deliver(self.page,'session','nonce',decode_files(body(b'fake','fake.png','image/png')))
        self.assertEqual(caught.exception.status,415)
        self.element.evaluate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
