import unittest
from app import total
class TestTotal(unittest.TestCase):
    def test_base(self): self.assertEqual(total(2,100),200)
