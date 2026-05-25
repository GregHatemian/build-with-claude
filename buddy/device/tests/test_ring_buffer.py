"""Unit tests for the cumul-graph ring buffer."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

import buddy_state  # type: ignore


class TestRingBuffer(unittest.TestCase):
    def test_empty_buffer_returns_no_samples(self):
        rb = buddy_state.RingBuffer(capacity=4)
        self.assertEqual(list(rb), [])
        self.assertEqual(len(rb), 0)

    def test_push_under_capacity_preserves_order(self):
        rb = buddy_state.RingBuffer(capacity=4)
        rb.push((1, 100))
        rb.push((2, 200))
        rb.push((3, 300))
        self.assertEqual(list(rb), [(1, 100), (2, 200), (3, 300)])

    def test_push_at_capacity_wraps_oldest_first(self):
        rb = buddy_state.RingBuffer(capacity=3)
        rb.push((1, 100))
        rb.push((2, 200))
        rb.push((3, 300))
        rb.push((4, 400))  # evicts (1, 100)
        rb.push((5, 500))  # evicts (2, 200)
        self.assertEqual(list(rb), [(3, 300), (4, 400), (5, 500)])
        self.assertEqual(len(rb), 3)

    def test_clear_empties_buffer(self):
        rb = buddy_state.RingBuffer(capacity=4)
        rb.push((1, 100))
        rb.push((2, 200))
        rb.clear()
        self.assertEqual(list(rb), [])
        self.assertEqual(len(rb), 0)


if __name__ == "__main__":
    unittest.main()
