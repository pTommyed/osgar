"""
  Parse 3DM-GX5-25 data
"""
# www.microstrain.com/sites/default/files/3dm-gx5-25_dcp_manual_8500-0065.pdf
import struct


def checksum(packet):
    # 16-bit Fletcher Checksum Algorithm
    ch1, ch2 = 0, 0
    for b in packet[2:]:
        ch1 += b
        ch2 += ch1
    return ((ch1 << 8) & 0xFFFF) + (ch2 & 0xFFFF)


def get_packet(buf):
    try:
        i = buf.index(b'\x75\x65')
        print(i)
        packet_size = buf[i + 3] + 4 + 2  # + header size + checksum
        packet = buf[i:i + packet_size]
#        ch = checksum(packet)
#        print(hex(ch))
        return packet, buf[i + packet_size:]
    except ValueError:
        return b'', buf

def parse_packet(packet, verbose=False):
    assert packet.startswith(b'\x75\x65'), packet.hex()
    assert len(packet) > 3, len(packet)
    packet_size = packet[3] + 4 + 2 # + header size + checksum
    assert len(packet) == packet_size, (len(packet), packet_size)
    desc = packet[2]

    # IMU Data Set (0x80)
    assert desc == 0x80, hex(desc)
    i = 4
    acc, gyro, mag = None, None, None
    while i < packet_size - 2:
        field_length = packet[i]
        cmd = packet[i + 1]

        if cmd == 0x04:
            # Scaled Accelerometer Vector
            assert field_length == 14, field_length
            acc = struct.unpack_from('>fff', packet, i + 2)
            if verbose:
                print('acc', acc)
        elif cmd == 0x05:
            # Scaled Gyro Vector (0x80, 0x05)
            assert field_length == 14, field_length
            gyro = struct.unpack_from('>fff', packet, i + 2)
            if verbose:
                print('gyro', gyro)
        elif cmd == 0x06:
            # Scaled Magnetometer Vector (0x80, 0x06)
            assert field_length == 14, field_length
            mag = struct.unpack_from('>fff', packet, i + 2)
            if verbose:
                print('mag', mag)
        else:
            assert False, hex(cmd)

        i += field_length

        return acc, gyro, mag

# vim: expandtab sw=4 ts=4
