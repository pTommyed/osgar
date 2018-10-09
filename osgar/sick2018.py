"""
  SICK Robot Day 2018
"""
import sys
import math
from datetime import timedelta

from osgar.lib.config import load as config_load
from osgar.lib.scan_feature import detect_box
from osgar.lib.mathex import normalizeAnglePIPI
from osgar.robot import Robot


# TODO shared place for multiple applications
class EmergencyStopException(Exception):
    pass


def min_dist(laser_data):
    if len(laser_data) > 0:
        # remove ultra near reflections and unlimited values == 0
        laser_data = [x if x > 10 else 10000 for x in laser_data]
        return min(laser_data)/1000.0
    return 0


def distance(pose1, pose2):
    return math.hypot(pose1[0] - pose2[0], pose1[1] - pose2[1])


HAND_TRAVEL = b'40/50/0/0\n'  # ready for pickup & traveling position
HAND_DOWN   = b'30/40/0/0\n'  # hit balls
HAND_UP     = b'20/80/0/0\n'  # move up


class SICKRobot2018:
    def __init__(self, config, bus):
        self.bus = bus
        self.last_position = [0, 0, 0]  # proper should be None, but we really start from zero
        self.time = None
        self.raise_exception_on_stop = False
        self.verbose = False
        self.last_scan = None
        self.scan_count = 0
        self.buttons = None

        self.max_speed = 0.2  # TODO load from config
        self.max_angular_speed = math.radians(45)

    def update(self):
        packet = self.bus.listen()
        if packet is not None:
            timestamp, channel, data = packet
            self.time = timestamp
            if channel == 'pose2d':
                self.last_position = data
            elif channel == 'scan':
                if self.verbose:
                    print('%.3f\t%.3f\t%.3f\t%.3f' % (
                        min_dist(data[135:270]), min_dist(data[270:811//2]),
                        min_dist(data[811//2:-270]), min_dist(data[-270:])))
                self.last_scan = data
                self.scan_count += 1
            elif channel == 'buttons':
                self.buttons = data
            elif channel == 'emergency_stop':
                if self.raise_exception_on_stop and data:
                    raise EmergencyStopException()

    def wait(self, dt):  # TODO refactor to some common class
        if self.time is None:
            self.update()
        start_time = self.time
        while self.time - start_time < dt:
            self.update()

    def go_straight(self, how_far):
        print("go_straight %.1f" % how_far, self.last_position)
        start_pose = self.last_position
        if how_far >= 0:
            self.bus.publish('desired_speed', [self.max_speed, 0.0])
        else:
            self.bus.publish('desired_speed', [-self.max_speed, 0.0])
        while distance(start_pose, self.last_position) < abs(how_far):
            self.update()
        self.bus.publish('desired_speed', [0.0, 0.0])

    def turn(self, angle):
        print("turn %.1f" % math.degrees(angle))
        start_pose = self.last_position
        if angle >= 0:
            self.bus.publish('desired_speed', [0.0, self.max_angular_speed])
        else:
            self.bus.publish('desired_speed', [0.0, -self.max_angular_speed])
        while abs(start_pose[2] - self.last_position[2]) < abs(angle):
            self.update()
        self.bus.publish('desired_speed', [0.0, 0.0])

    def send_speed_cmd(self, speed, angular_speed):
        self.bus.publish('desired_speed', [speed, angular_speed])

    def send_hand_cmd(self, cmd):
        self.bus.publish('hand', cmd)

    def drop_balls(self):
        print("drop ball 1 + 2")
        self.bus.publish('hand', b'40/50/1/1\n')
        self.wait(timedelta(seconds=3))
        self.bus.publish('hand', b'40/50/0/0\n')
        print("drop ball END")

    def ver0(self):
        self.go_straight(1.0)
        self.bus.publish('hand', b'40/50/0/0\n')  # ready for pickup
        self.go_straight(1.0)
        self.bus.publish('hand', b'30/40/0/0\n')  # hit balls
        self.wait(timedelta(seconds=3))
        self.bus.publish('hand', b'20/80/0/0\n')  # move up
        self.go_straight(-1.0)
        self.bus.publish('hand', b'40/50/0/0\n')  # traveling position
        self.turn(math.radians(180))
        self.go_straight(1.25)
        self.drop_balls()
        self.wait(timedelta(seconds=3))

    def approach_box(self, at_dist):
        speed = 0.2
        box_pos = None
        while self.last_scan is None:
            self.update()
        if min_dist(self.last_scan[270:-270]) > at_dist:
            self.send_speed_cmd(speed, 0.0)
            prev_count = self.scan_count
            while min_dist(self.last_scan[270:-270]) > at_dist:
                self.update()
                if prev_count != self.scan_count:
                    box_i = detect_box(self.last_scan)
                    if box_i is not None:
                        angle = math.radians((len(self.last_scan)//2 - box_i)/3)
                        dist = self.last_scan[box_i]/1000.0
                        pose = self.last_position
                        box_pos = (pose[0] + dist * math.cos(pose[2] + angle),
                                   pose[1] + dist * math.sin(pose[2] + angle))
                        # TODO laser position offset??
                        # print('%.3f\t %.3f' % box_pos)
                    prev_count = self.scan_count
                    angular_speed = 0.0
                    if box_pos is not None:
                        x, y, heading = self.last_position
                        diff = normalizeAnglePIPI(math.atan2(box_pos[1] - y, box_pos[0] - x) - heading)
                        if math.hypot(x - box_pos[0], y - box_pos[1]) > 0.5:
                            angular_speed = diff  # turn to goal in 1s
                    self.send_speed_cmd(speed, angular_speed)

            self.send_speed_cmd(0.0, 0.0)  # or it should stop always??

    def catch_transporter(self):
        at_dist = 0.2  # TODO maybe we need to be closer??
        speed = 0.2
        angular_speed = math.radians(45)
        self.send_hand_cmd(HAND_TRAVEL)
        self.wait(timedelta(seconds=3))
        self.go_straight(1.0)  # wait closer to expected trajectory

        while min_dist(self.last_scan[270:-270]) > at_dist:
            center_width = 100
            left = min_dist(self.last_scan[811//2+center_width//2:-270])
            right = min_dist(self.last_scan[270:811//2-center_width//2])
            center = min_dist(self.last_scan[811//2-center_width//2:811//2+center_width//2])
            if self.verbose:
                print('%.2f\t%.2f\t%.2f' % (left, center, right))
            if center <= left and center <= right:
                self.send_speed_cmd(speed, 0.0)
            elif left < right:
                self.send_speed_cmd(speed, angular_speed)
            else:
                self.send_speed_cmd(speed, -angular_speed)

            prev_count = self.scan_count
            while prev_count == self.scan_count:
                self.update()

        self.send_hand_cmd(HAND_DOWN)
        self.wait(timedelta(seconds=3))
        self.send_hand_cmd(HAND_UP)
        self.go_straight(-1.0)
        self.send_hand_cmd(HAND_TRAVEL)
        self.send_speed_cmd(0.0, 0.0)

    def wait_for_start(self):
        while self.buttons is None or not self.buttons['cable_in']:
            self.update()
        assert self.buttons is not None

        while self.buttons['cable_in']:
            self.update()

    def ver1(self):
        self.wait_for_start()
        for run in range(3):  # TODO 10min limit
            self.catch_transporter()
            self.approach_box(at_dist=0.2)
            self.drop_balls()
            self.wait(timedelta(seconds=3))
            self.go_straight(-0.5)
            self.turn(math.radians(180))

    def play(self):
        try:
            self.raise_exception_on_stop = True
#            self.ver0()
            self.ver1()
        except EmergencyStopException:
            print('!!!Emergency STOP!!!')
            self.raise_exception_on_stop = False
            self.bus.publish('desired_speed', [0.0, 0.0])
            self.wait(timedelta(seconds=1))

    def start(self):
        pass

    def request_stop(self):
        self.bus.shutdown()

    def join(self):
        pass


if __name__ == "__main__":
    from osgar.logger import LogWriter, LogReader
    import argparse

    parser = argparse.ArgumentParser(description='Follow Me')
    subparsers = parser.add_subparsers(help='sub-command help', dest='command')
    subparsers.required = True
    parser_run = subparsers.add_parser('run', help='run on real HW')
    parser_run.add_argument('config', nargs='+', help='configuration file')
    parser_run.add_argument('--note', help='add description')

    parser_replay = subparsers.add_parser('replay', help='replay from logfile')
    parser_replay.add_argument('logfile', help='recorded log file')
    parser_replay.add_argument('--force', '-F', dest='force', action='store_true', help='force replay even for failing output asserts')
    parser_replay.add_argument('--config', nargs='+', help='force alternative configuration file')
    parser_replay.add_argument('--verbose', '-v', help="verbose mode", action='store_true')
    args = parser.parse_args()

    if args.command == 'replay':
        from replay import replay
        args.module = 'app'
        game = replay(args, application=SICKRobot2018)
        game.verbose = args.verbose
        game.play()

    elif args.command == 'run':
        log = LogWriter(prefix='eduro-', note=str(sys.argv))
        config = config_load(*args.config)
        log.write(0, bytes(str(config), 'ascii'))  # write configuration
        robot = Robot(config=config['robot'], logger=log, application=SICKRobot2018)
        game = robot.modules['app']  # TODO nicer reference
        robot.start()
        game.play()
        robot.finish()

# vim: expandtab sw=4 ts=4
