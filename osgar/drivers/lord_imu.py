"""
  Parse 3DM-GX5-25 data
"""
# www.microstrain.com/sites/default/files/3dm-gx5-25_dcp_manual_8500-0065.pdf

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
        packet_size = buf[i + 3]
        packet = buf[i:i + packet_size]
#        ch = checksum(packet)
#        print(hex(ch))
        return packet, buf[i + packet_size:]
    except ValueError:
        return b'', buf
    
