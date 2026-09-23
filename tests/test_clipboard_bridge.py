import asyncio
import time
import unittest
from app.services.clipboard_bridge import PageClipboard


class ClipboardTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_recent_input_no_publish(self):
        state=PageClipboard()
        queue=asyncio.Queue(maxsize=1)
        state.subscribers.add(queue)
        self.assertFalse(state.publish('background text'))
        self.assertTrue(queue.empty())
        state.last_input=time.monotonic()-6
        self.assertFalse(state.publish('expired gesture'))

    async def test_bounded_page_scoped_text_only(self):
        first, second=PageClipboard(), PageClipboard()
        q1, q2=asyncio.Queue(maxsize=1), asyncio.Queue(maxsize=1)
        first.subscribers.add(q1); second.subscribers.add(q2)
        first.last_input=time.monotonic()
        self.assertTrue(first.publish('one'))
        self.assertTrue(first.publish('دو'))
        self.assertEqual(q1.qsize(),1)
        self.assertEqual(await q1.get(),'دو')
        self.assertTrue(q2.empty())
        self.assertFalse(first.publish('x'*1_000_001))
        self.assertFalse(first.publish({'text':'invalid'}))
        self.assertFalse(first.publish(''))


if __name__=='__main__':
    unittest.main()
