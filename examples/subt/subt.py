"""
  SubT Challenge Version 1
"""
import sys
import os.path
import math
from datetime import timedelta
from collections import defaultdict

from osgar.explore import follow_wall_angle
from osgar.lib.mathex import normalizeAnglePIPI


def min_dist(laser_data):
    if len(laser_data) > 0:
        # remove ultra near reflections and unlimited values == 0
        laser_data = [x if x > 10 else 10000 for x in laser_data]
        return min(laser_data)/1000.0
    return 0


def distance(pose1, pose2):
    return math.hypot(pose1[0] - pose2[0], pose1[1] - pose2[1])


class SubTChallenge:
    def __init__(self, config, bus):
        self.bus = bus
        self.start_pose = None
        self.traveled_dist = 0.0
        self.time = None
        self.max_speed = 1.0  # TODO load from config
        self.max_angular_speed = math.radians(45)

        self.last_position = (0, 0, 0)  # proper should be None, but we really start from zero
        self.xyz = (0, 0, 0)  # 3D position for mapping artifacts
        self.yaw, self.pitch, self.roll = 0, 0, 0
        self.is_moving = None  # unknown
        self.scan = None  # I should use class Node instead
        self.stat = defaultdict(int)
        self.artifact_xyz = None
        self.artifact_data = None
        self.artifact_detected = False

        self.use_right_wall = config.get('right_wall', True)

    def send_speed_cmd(self, speed, angular_speed):
        return self.bus.publish('desired_speed', [round(speed*1000), round(math.degrees(angular_speed)*100)])

    def go_straight(self, how_far):
        print(self.time, "go_straight %.1f" % how_far, self.last_position)
        start_pose = self.last_position
        if how_far >= 0:
            self.send_speed_cmd(self.max_speed, 0.0)
        else:
            self.send_speed_cmd(-self.max_speed, 0.0)
        while distance(start_pose, self.last_position) < abs(how_far):
            self.update()
        self.send_speed_cmd(0.0, 0.0)

    def turn(self, angle, with_stop=True, speed=0.0):
        print(self.time, "turn %.1f" % math.degrees(angle))
        start_pose = self.last_position
        if angle >= 0:
            self.send_speed_cmd(speed, self.max_angular_speed)
        else:
            self.send_speed_cmd(speed, -self.max_angular_speed)
        while abs(normalizeAnglePIPI(start_pose[2] - self.last_position[2])) < abs(angle):
            self.update()
        if with_stop:
            self.send_speed_cmd(0.0, 0.0)
            start_time = self.time
            while self.time - start_time < timedelta(seconds=2):
                self.update()
                if not self.is_moving:
                    break
            print(self.time, 'stop at', self.time - start_time)

    def follow_wall(self, radius, right_wall=False, timeout=timedelta(hours=3), dist_limit=None, stop_on_artf=False):
        start_dist = self.traveled_dist
        start_time = self.time
        desired_speed = 1.0
        while self.time - start_time < timeout:
            if self.update() == 'scan':
                size = len(self.scan)
                dist = min_dist(self.scan[size//3:2*size//3])
                if dist < 2.0:
                    desired_speed = 1.0 * (dist - 0.4) / 1.6
                else:
                    desired_speed = 2.0
                desired_angular_speed = 0.7 * follow_wall_angle(self.scan, radius=radius, right_wall=right_wall)
#                print(self.time, 'desired_angular_speed\t%.1f\t%.3f' % (math.degrees(desired_angular_speed), dist))
                if desired_angular_speed is None:
                    desired_angular_speed = 0.0
                self.send_speed_cmd(desired_speed, desired_angular_speed)
            if dist_limit is not None:
                if dist_limit < self.traveled_dist - start_dist:
                    print('Distance limit reached! At', self.traveled_dist, self.traveled_dist - start_dist)
                    break
            if stop_on_artf and self.artifact_detected:
                break
        return self.traveled_dist - start_dist

    def update(self):
        packet = self.bus.listen()
        if packet is not None:
#            print('SubT', packet)
            timestamp, channel, data = packet
            if self.time is None or int(self.time.seconds)//60 != int(timestamp.seconds)//60:
                print(timestamp, '(%.1f %.1f %.1f)' % self.xyz, sorted(self.stat.items()))
                self.stat.clear()

            self.time = timestamp
            self.stat[channel] += 1
            if channel == 'pose2d':
                x, y, heading = data
                pose = (x/1000.0, y/1000.0, math.radians(heading/100.0))
                if self.last_position is not None:
                    self.is_moving = (self.last_position != pose)
                    dist = math.hypot(pose[0] - self.last_position[0], pose[1] - self.last_position[1])
                else:
                    dist = 0.0
                self.last_position = pose
                if self.start_pose is None:
                    self.start_pose = pose
                self.traveled_dist += dist
                x, y, z = self.xyz
                x += math.cos(self.pitch) * math.cos(self.yaw) * dist
                y += math.cos(self.pitch) * math.sin(self.yaw) * dist
                z += math.sin(self.pitch) * dist
                self.bus.publish('pose2d', [round(x*1000), round(y*1000),
                                            round(math.degrees(self.yaw)*100)])
                self.xyz = x, y, z
            elif channel == 'scan':
                self.scan = data
            elif channel == 'rot':
                self.yaw, self.pitch, self.roll = [math.radians(x/100) for x in data]
            elif channel == 'artf':
                self.artifact_data, deg_100th, dist_mm = data
                x, y, z = self.xyz
                angle, dist = self.yaw + math.radians(deg_100th/100.0), dist_mm/1000.0
                self.artifact_xyz = x + math.cos(angle) * dist, y + math.sin(angle) * dist, z
                self.artifact_detected = True
            return channel

    def wait(self, dt):  # TODO refactor to some common class
        if self.time is None:
            self.update()
        start_time = self.time
        while self.time - start_time < dt:
            self.update()

    def play(self):
        print("SubT Challenge Ver1!")
        self.go_straight(9.0)  # go to the tunnel entrance
        dist = self.follow_wall(radius = 1.5, right_wall=self.use_right_wall, stop_on_artf=True,
                                timeout=timedelta(hours=3))
        print("Going HOME")
        self.turn(math.radians(90), speed=-0.1)  # it is safer to turn and see the wall + slowly backup
        self.turn(math.radians(90), speed=-0.1)
        self.follow_wall(radius = 1.5, right_wall=not self.use_right_wall, timeout=timedelta(hours=3), dist_limit=dist+5)
        if self.artifact_xyz is not None:
            x, y, z = self.artifact_xyz
            self.bus.publish('artf_xyz', [self.artifact_data, round(x*1000), round(y*1000), round(z*1000)])
        self.send_speed_cmd(0, 0)
        self.wait(timedelta(seconds=68))

    def start(self):
        pass

    def request_stop(self):
        self.bus.shutdown()

    def join(self):
        pass


if __name__ == "__main__":
    import argparse
    from osgar.lib.config import load as config_load
    from osgar.record import Recorder
    from osgar.logger import LogWriter, LogReader

    parser = argparse.ArgumentParser(description='SubT Challenge')
    subparsers = parser.add_subparsers(help='sub-command help', dest='command')
    subparsers.required = True
    parser_run = subparsers.add_parser('run', help='run on real HW')
    parser_run.add_argument('config', nargs='+', help='configuration file')
    parser_run.add_argument('--note', help='add description')

    parser_replay = subparsers.add_parser('replay', help='replay from logfile')
    parser_replay.add_argument('logfile', help='recorded log file')
    parser_replay.add_argument('--force', '-F', dest='force', action='store_true', help='force replay even for failing output asserts')
    parser_replay.add_argument('--config', nargs='+', help='force alternative configuration file')
    args = parser.parse_args()

    if args.command == 'replay':
        from osgar.replay import replay
        args.module = 'app'
        game = replay(args, application=SubTChallenge)
        game.play()

    elif args.command == 'run':
        # support simultaneously multiple platforms
        prefix = os.path.basename(args.config[0]).split('.')[0] + '-'
        log = LogWriter(prefix=prefix, note=str(sys.argv))
        config = config_load(*args.config)
        log.write(0, bytes(str(config), 'ascii'))  # write configuration
        robot = Recorder(config=config['robot'], logger=log, application=SubTChallenge)
        game = robot.modules['app']  # TODO nicer reference
        robot.start()
        game.play()
        robot.finish()

# vim: expandtab sw=4 ts=4
