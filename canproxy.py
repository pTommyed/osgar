"""
  CAN bus proxy, i.e wrapper for some task which will be handled by CAN later.
  In particular:
      - state machine for gas pedal control
      - timing for turn (without feedback)
"""

import ctypes

PULSE_DURATION = 0.3  #0.5  # seconds

CENTER_GAS_MIN = 14500
CENTER_GAS_MAX = 16500

GO_LIMIT = 19000

SCALE_NEW = 0.5  # 0.0 < x < 1.0

def sint16_diff(a, b):
    '''clip integer value (a - b) to signed int16'''
    assert 0 <= a <= 0xFFFF, a
    assert 0 <= b <= 0xFFFF, b
    return ctypes.c_short(a - b).value


class CANProxy:

    def __init__(self, can):
        self.can = can
        self.time = 0.0
        self.gas = None
        self.filteredGas = None
        self.cmd = None
        self.turn_cmd = None
        self.turn_stop_time = 0.0

        self.prev_enc_raw = None
        self.dist_left_raw = 0
        self.dist_right_raw = 0

        self.wheel_angle = None

    def go(self):
        self.cmd = 'go'

    def stop(self):
        self.cmd = 'stop'

    def pulse_left(self, duration):
        self.turn_cmd = 'left'
        self.turn_stop_time = self.time + duration

    def pulse_right(self, duration):
        self.turn_cmd = 'right'
        self.turn_stop_time = self.time + duration

    def update_gas_status(self, (id, data)):
        # note partial duplicity with johndeere.py
        if id == 0x281:
            assert( len(data)>=8 ) 
            self.gas = data[1]*256 + data[0]
            if self.filteredGas is None:
                self.filteredGas = self.gas
            else:
                self.filteredGas = SCALE_NEW*self.gas + (1.0 - SCALE_NEW)*self.filteredGas

    def update_encoders(self, (id, data)):
        if id == 0x284:
            assert len(data) == 4, data
            arr = [data[2*i+1]*256 + data[2*i] for i in xrange(2)]
            if self.prev_enc_raw is not None:
                diffL = sint16_diff(arr[0], self.prev_enc_raw[0])
                diffR = sint16_diff(arr[1], self.prev_enc_raw[1])

                if abs(diffL) > 128:
                    print "ERR-L\t{}\t{}\t{}".format(self.dist_left_raw, self.prev_enc_raw[0], arr[0])
                else:
                    self.dist_left_raw += diffL

                if abs(diffR) > 128:
                    print "ERR-R\t{}\t{}\t{}".format(self.dist_right_raw, self.prev_enc_raw[1], arr[1])
                else:
                    self.dist_right_raw += diffR
            self.prev_enc_raw = arr
#            print "ENC\t{}\t{}\t{}".format(self.time, self.dist_left_raw, self.dist_right_raw)
        if id == 0x184:  # change report
            assert len(data) == 1, data
#            print "CHANGE", data[0] & 0x3, (data[0] >> 2) & 0x3, self.dist_right_raw

    def update_wheel_angle_status(self, (id, data)):
        if id == 0x182:
            assert(len(data) == 2) 
            self.wheel_angle = ctypes.c_short(data[1]*256 + data[0]).value
            print "WHEEL", self.wheel_angle

    def update(self, packet):
        self.update_gas_status(packet)
        self.update_encoders(packet)
        self.update_wheel_angle_status(packet)

    def set_time(self, time):
        self.time = time

    def send_speed(self):
        if self.cmd == 'go':
            if self.filteredGas < GO_LIMIT:
                self.can.sendData(0x201, [0xC])  # pulse forward
            else:
                self.can.sendData(0x201, [0])
                self.cmd = None

        elif self.cmd == 'stop':
            if self.filteredGas < CENTER_GAS_MIN:
                self.can.sendData(0x201, [0xC])  # pulse forward
            elif self.filteredGas > CENTER_GAS_MAX:
                self.can.sendData(0x201, [3])  # pulse backward
            else:
                self.can.sendData(0x201, [0])
                self.cmd = None

        if self.turn_cmd is not None:
            if self.turn_stop_time <= self.time:
                self.can.sendData(0x202, [0])
                self.turn_cmd = None
            else:
                if self.turn_cmd == 'left':
                    self.can.sendData(0x202, [5])
                elif self.turn_cmd == 'right':
                    self.can.sendData(0x202, [0x6])
                else:
                    assert 0, self.turn_cmd  # unknown turn_cmd

# vim: expandtab sw=4 ts=4 
