import asyncio
import unittest

from app.services.input_buffer import InputBuffer


class InputBufferTests(unittest.IsolatedAsyncioTestCase):
    async def test_hover_burst_does_not_delay_action(self):
        buffer = InputBuffer()
        for x in range(1000):
            await buffer.put({'type':'move','x':x,'y':20,'buttons':0})
        await buffer.put({'type':'down','x':999,'y':20})
        await buffer.put({'type':'text','text':'سلام'})
        self.assertEqual((await buffer.get())['x'], 999)
        self.assertEqual((await buffer.get())['type'], 'down')
        self.assertEqual((await buffer.get())['text'], 'سلام')

    async def test_actions_drag_and_acknowledgements_stay_ordered(self):
        events = [
            {'type':'move','x':1}, {'type':'down'},
            {'type':'move','x':2,'buttons':1}, {'type':'move','x':3,'buttons':1},
            {'type':'up'}, {'type':'move','x':4,'id':10},
            {'type':'move','x':5,'id':11}, {'type':'key_up','key':'Shift'},
            {'type':'text','text':'الف'}, {'type':'text','text':'ب'},
        ]
        buffer = InputBuffer()
        for event in events:
            await buffer.put(event)
        self.assertEqual([await buffer.get() for _ in events], events)

    async def test_wheel_keeps_distance_direction_and_target(self):
        buffer = InputBuffer()
        for delta in (100, 120, 150):
            await buffer.put({'type':'wheel','x':10,'y':20,'dy':delta})
        await buffer.put({'type':'wheel','x':10,'y':20,'dy':-50})
        await buffer.put({'type':'wheel','x':99,'y':20,'dy':-50})
        await buffer.put({'type':'wheel','x':99,'y':20,'dy':-2000})
        self.assertEqual([(await buffer.get())['dy'] for _ in range(4)], [370,-50,-50,-2000])

    async def test_queue_is_bounded_without_dropping_keys(self):
        buffer = InputBuffer(maxsize=2)
        await buffer.put({'type':'text','text':'a'})
        await buffer.put({'type':'text','text':'b'})
        producer = asyncio.create_task(buffer.put({'type':'text','text':'c'}))
        await asyncio.sleep(0)
        self.assertFalse(producer.done())
        self.assertEqual((await buffer.get())['text'], 'a')
        await asyncio.wait_for(producer, 1)
        self.assertEqual([(await buffer.get())['text'] for _ in range(2)], ['b','c'])


if __name__ == '__main__':
    unittest.main()
