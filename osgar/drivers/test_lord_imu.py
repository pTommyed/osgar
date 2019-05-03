import unittest
from unittest.mock import MagicMock

from osgar.drivers.lord_imu import get_packet, checksum
from osgar.bus import BusHandler


class LordIMUTest(unittest.TestCase):

    def test_parse_packet(self):
        self.assertEqual(get_packet(b''), (b'', b''))

        buf = b'\x0e\x06=\xea\x01\x19>\xc3R\xd2>\xdb?V\x1e\x93ue\x80*\x0e\x04<\x87C\x97\xbdF%\xd4\xbf\x7f\xb4\x14\x0e\x05\xba.\xd6S\xba)b\xeb\xb9\xf6\xf7\x80\x0e\x06=\xeaD\x10>\xc3\\\xd5>\xdb4\x1d\xda\x04ue\x80*\x0e\x04<\x9d\x1c\xa3\xbdS\x19\xd9\xbf\x7f\xbc^\x0e\x05\xbas\xd7W\xb9\x15\x03\xa0\xbaD\xffc\x0e\x06=\xea'
        packet, buf = get_packet(buf)
        self.assertTrue(packet.startswith(b'\x75\x65'), packet)
#        print(packet.hex())
        self.assertEqual(len(packet), 42)

    def test_checksum(self):
        packet = bytes.fromhex("7565 0C20 0D08 0103 1200 0A04 000A 0500 0A13 0A01 0511 000A 1000 0A01 000A 0200 0A03 000A D43D")
        ch = checksum(packet)
        print(hex(ch))

# vim: expandtab sw=4 ts=4