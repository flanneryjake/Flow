"""E10 tests: python test_queue_order.py"""
import unittest

import queue_order as q


def card(n, labels=(), **kw):
    c = {'id': f'gh-{n}', 'number': n, 'labels': list(labels), 'priority': ''}
    c.update(kw)
    return c


def order(cards, parked=None):
    return [c['number'] for c in sorted(cards, key=lambda c: q.key(c, parked))]


class QueueOrder(unittest.TestCase):
    def test_priority_steps(self):
        cards = [card(1, ['p3']), card(2), card(3, ['p2']), card(5, ['p1']), card(6, ['p0'])]
        self.assertEqual(order(cards), [6, 5, 3, 2, 1])

    def test_now_parked_resume_first(self):
        cards = [card(1, ['p0']), card(2, ['resume']), card(3), card(4, ['now'])]
        self.assertEqual(order(cards, parked='gh-3'), [4, 3, 2, 1])

    def test_5060_style_flags(self):   # 5060 cards carry now/resume/priority keys as well as labels
        cards = [card(1, priority='P0'), card(2, now=True)]
        self.assertEqual(order(cards), [2, 1])

    def test_focus_project_then_phase(self):
        cards = [card(1, ['p2']), card(2, ['p2', 'project:admin']), card(3, ['p2', 'project:homebase', 'phase:3']),
                 card(4, ['p2', 'project:brains', 'phase:1']), card(5, ['p2', 'project:income', 'phase:later'])]
        self.assertEqual(order(cards), [4, 3, 5, 2, 1])

    def test_stable_inside_a_step(self):   # ready() already gives oldest first
        cards = [card(9, ['p2']), card(3, ['p2']), card(7, ['p2'])]
        self.assertEqual(order(cards), [9, 3, 7])

    def test_rig_card_priority_key_only(self):   # gh_card's "priority" without labels
        self.assertEqual(order([card(1, priority='P2'), card(2, priority='P1')]), [2, 1])


if __name__ == '__main__':
    unittest.main()
