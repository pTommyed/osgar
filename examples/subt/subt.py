"""
  SubT Challenge Version 1
"""
import gc
import sys
import os.path
import math
from datetime import timedelta
from collections import defaultdict

import numpy as np

from osgar.explore import follow_wall_angle
from osgar.lib.mathex import normalizeAnglePIPI
from osgar.lib import quaternion

from local_planner import LocalPlanner


VIRTUAL_WORLD = False  # TODO more suitable parametrization, real robots now look only for red artifacts

RADIUS = 0.6  # 0.9  # 1.0

# Return with artifact configuration
#SEARCH_TIME_BEGIN = timedelta(minutes=1)
#SEARCH_TIME_END = timedelta(minutes=5)

# No artifacts configuration
SEARCH_TIME_BEGIN = timedelta(minutes=4)
SEARCH_TIME_END = timedelta(minutes=4)

RETURN_TIMEOUT = SEARCH_TIME_END + timedelta(minutes=10)  # ??


TRACE_STEP = 0.5  # meters in 3D


def min_dist(laser_data):
    if len(laser_data) > 0:
        # remove ultra near reflections and unlimited values == 0
        laser_data = [x if x > 10 else 10000 for x in laser_data]
        return min(laser_data)/1000.0
    return 0


def distance(pose1, pose2):
    return math.hypot(pose1[0] - pose2[0], pose1[1] - pose2[1])

def distance3D(xyz1, xyz2, weights=[1.0, 1.0, 1.0]):
    return math.sqrt(sum([w * (a-b)**2 for a, b, w in zip(xyz1, xyz2, weights)]))


class Trace:
    def __init__(self, step=TRACE_STEP):
        self.trace = [(0, 0, 0)]  # traveled 3D positions
        self.step = step

    def update_trace(self, pos_xyz):
        if distance3D(self.trace[-1], pos_xyz) >= self.step:
            self.trace.append(pos_xyz)

    def prune(self, radius=None):
        # use short-cuts and remove all cycles
        if radius is None:
            radius = self.step

        pruned = Trace(step=self.step)
        open_end = 1
        while open_end < len(self.trace):
            best = open_end
            for i, xyz in enumerate(self.trace[open_end:], start=open_end):
                if distance3D(xyz, pruned.trace[-1]) < radius:
                    best = i
            pruned.update_trace(self.trace[best])
            open_end = best + 1
        self.trace = pruned.trace


    def where_to(self, xyz, max_target_distance):
        # looking for a target point within max_target_distance nearest to the start
        for _ in range(8):
            for target in self.trace:
                if distance3D(target, xyz, [1.0, 1.0, 0.2]) < max_target_distance:
                    return target
            # if the robot deviated too far from the trajectory, we need to look for more distant target points
            max_target_distance *= 1.5
        # robot is crazy far from the trajectory
        assert(False)


class Collision(Exception):
    pass


class SubTChallenge:
    def __init__(self, config, bus):
        self.bus = bus
        self.start_pose = None
        self.traveled_dist = 0.0
        self.time = None
        self.max_speed = 0.3 #0.5  #1.0  # TODO load from config
        self.max_angular_speed = math.radians(45)

        self.last_position = (0, 0, 0)  # proper should be None, but we really start from zero
        self.xyz = (0, 0, 0)  # 3D position for mapping artifacts
        self.xyz_quat = [0, 0, 0]
        self.orientation = quaternion.identity()
        self.yaw, self.pitch, self.roll = 0, 0, 0
        self.is_moving = None  # unknown
        self.scan = None  # I should use class Node instead
        self.stat = defaultdict(int)
        self.voltage = []
        self.artifacts = []
        self.trace = Trace()
        self.collision_detector_enabled = False
        self.sim_time_sec = 0

        self.use_right_wall = config.get('right_wall', True)

        self.local_planner = None  # hack LocalPlanner()

    def send_speed_cmd(self, speed, angular_speed):
        success = self.bus.publish('desired_speed', [round(speed*1000), round(math.degrees(angular_speed)*100)])
        # Corresponds to gc.disable() in __main__. See a comment there for more details.
        gc.collect()
        return success

    def maybe_remember_artifact(self, artifact_data, artifact_xyz):
        for _, (x, y, z) in self.artifacts:
            if distance3D((x, y, z), artifact_xyz) < 4.0:
                return
        self.artifacts.append((artifact_data, artifact_xyz))

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

    def go_safely(self, desired_direction):
        if self.local_planner is None:
            safe_direction = desired_direction
        else:
            safety, safe_direction = self.local_planner.recommend(desired_direction)
        desired_angular_speed = 1.2 * safe_direction
        size = len(self.scan)
        dist = min_dist(self.scan[size//3:2*size//3])
        if dist < 0.75:  # 2.0:
#            desired_speed = self.max_speed * (1.2/2.0) * (dist - 0.4) / 1.6
            desired_speed = self.max_speed * (dist - 0.2) / 0.55
        else:
            desired_speed = self.max_speed  # was 2.0
        '''
        desired_angular_speed = 0.7 * safe_direction
        T = math.pi / 2
        desired_speed = 2.0 * (0.8 - min(T, abs(desired_angular_speed)) / T)
        '''
        self.send_speed_cmd(desired_speed, desired_angular_speed)

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

    def stop(self):
        self.send_speed_cmd(0.0, 0.0)
        start_time = self.time
        while self.time - start_time < timedelta(seconds=20):
            self.update()
            if not self.is_moving:
                break
        print(self.time, 'stop at', self.time - start_time, self.is_moving)

    def follow_wall(self, radius, right_wall=False, timeout=timedelta(hours=3), dist_limit=None, stop_on_artf_count=None,
            search_since=None):
        start_dist = self.traveled_dist
        start_time = self.sim_time_sec
        desired_speed = 1.0
        artf_count_before_start = 0
        while self.sim_time_sec - start_time < timeout.total_seconds():
            try:
                if self.update() == 'scan':
                    desired_direction = follow_wall_angle(self.scan, radius=radius, right_wall=right_wall)
                    self.go_safely(desired_direction)
                if dist_limit is not None:
                    if dist_limit < self.traveled_dist - start_dist:
                        print('Distance limit reached! At', self.traveled_dist, self.traveled_dist - start_dist)
                        break
                if search_since is not None and self.sim_time_sec < search_since.total_seconds():
                    artf_count_before_start = len(self.artifacts)
                if stop_on_artf_count is not None and stop_on_artf_count + artf_count_before_start <= len(self.artifacts):
                    break
            except Collision:
                assert not self.collision_detector_enabled  # collision disables further notification
                before_stop = self.xyz
                self.stop()
                after_stop = self.xyz
                print("Pose Jump:", before_stop, after_stop)
                self.xyz = before_stop
                self.go_straight(-1)
                self.stop()
                if right_wall:
                    turn_angle = math.pi / 2
                else:
                    turn_angle = -math.pi / 2
                self.turn(turn_angle, with_stop=True)
                self.go_straight(1.5)
                self.stop()
                self.turn(-turn_angle, with_stop=True)
                self.go_straight(1.5)
                self.stop()
                self.collision_detector_enabled = True
        return self.traveled_dist - start_dist

    def return_home(self):
        HOME_THRESHOLD = 5.0
        SHORTCUT_RADIUS = 2.3
        MAX_TARGET_DISTANCE = 5.0
        assert(MAX_TARGET_DISTANCE > SHORTCUT_RADIUS) # Because otherwise we could end up with a target point more distant from home than the robot.
        self.trace.prune(SHORTCUT_RADIUS)
        while distance3D(self.xyz, (0, 0, 0)) > HOME_THRESHOLD:
            if self.update() == 'scan':
                target_x, target_y = self.trace.where_to(self.xyz, MAX_TARGET_DISTANCE)[:2]
                x, y = self.xyz[:2]
                desired_direction = math.atan2(target_y - y, target_x - x) - self.yaw
                self.go_safely(desired_direction)

    def update(self):
        packet = self.bus.listen()
        if packet is not None:
#            print('SubT', packet)
            timestamp, channel, data = packet
            if self.time is None or int(self.time.seconds)//60 != int(timestamp.seconds)//60:
                print(timestamp, '(%.1f %.1f %.1f)' % self.xyz, sorted(self.stat.items()))
                print(timestamp, list(('%.1f' % (v/100)) for v in self.voltage))
                self.stat.clear()

            self.time = timestamp

            if not VIRTUAL_WORLD:
                self.sim_time_sec = self.time.total_seconds()

            self.stat[channel] += 1
            if channel == 'pose2d':
                x, y, heading = data
                pose = (x/1000.0, y/1000.0, math.radians(heading/100.0))
                if self.last_position is not None:
                    self.is_moving = (self.last_position != pose)
                    dist = math.hypot(pose[0] - self.last_position[0], pose[1] - self.last_position[1])
                    direction = ((pose[0] - self.last_position[0]) * math.cos(self.last_position[2]) +
                                 (pose[1] - self.last_position[1]) * math.sin(self.last_position[2]))
                    if direction < 0:
                        dist = -dist
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
                self.trace.update_trace(self.xyz)
                # pose3d
                dist3d = quaternion.rotate_vector([dist, 0, 0], self.orientation)
                self.xyz_quat = [a+b for a, b in zip(self.xyz_quat, dist3d)]
                self.bus.publish('pose3d', [self.xyz_quat, self.orientation])
            elif channel == 'scan':
                self.scan = data
                if self.local_planner is not None:
                    self.local_planner.update(data)
            elif channel == 'rot':
                self.yaw, self.pitch, self.roll = [math.radians(x/100) for x in data]
            elif channel == 'orientation':
                self.orientation = data
            elif channel == 'sim_time_sec':
                self.sim_time_sec = data
            elif channel == 'acc':
                acc = [x/1000.0 for x in data]
                gacc = np.matrix([[0., 0., 9.80]])  # Gravitational acceleration.
                cos_pitch = math.cos(self.pitch)
                sin_pitch = math.sin(self.pitch)
                # TODO: Once roll is correct, incorporate it here too.
                egacc = np.matrix([  # Expected gravitational acceleration given known pitch.
                    [ cos_pitch, 0., sin_pitch],
                    [        0., 1., 0.],
                    [-sin_pitch, 0., cos_pitch]
                ]) * gacc.T
                cacc = np.asarray(acc) - egacc.T  # Corrected acceleration (without gravitational acceleration).
                magnitude = math.hypot(cacc[0, 0], cacc[0, 1])
                if magnitude > 12.0:
                    print(self.time, 'Collision!', acc, 'reported:', self.collision_detector_enabled)
                    if self.collision_detector_enabled:
                        self.collision_detector_enabled = False
                        raise Collision()
            elif channel == 'artf':
                artifact_data, deg_100th, dist_mm = data
                x, y, z = self.xyz
                angle, dist = self.yaw + math.radians(deg_100th/100.0), dist_mm/1000.0
                ax = x + math.cos(angle) * dist
                ay = y + math.sin(angle) * dist
                az = z
                self.maybe_remember_artifact(artifact_data, (ax, ay, az))
            elif channel == 'voltage':
                self.voltage = data
            return channel

    def wait(self, dt):  # TODO refactor to some common class
        if self.time is None:
            self.update()
        start_time = self.time
        while self.time - start_time < dt:
            self.update()

#############################################
    def play(self):
        print("SubT Challenge Ver1!")
        self.go_straight(2.5)  # go to the tunnel entrance
        dist = self.follow_wall(radius=RADIUS, right_wall=self.use_right_wall, stop_on_artf_count=1,
                                search_since=SEARCH_TIME_BEGIN,
                                timeout=SEARCH_TIME_END)
        print("Going HOME")
        self.turn(math.radians(90), speed=-0.1)  # it is safer to turn and see the wall + slowly backup
        self.turn(math.radians(90), speed=-0.1)
        self.follow_wall(radius=RADIUS, right_wall=not self.use_right_wall, timeout=RETURN_TIMEOUT, dist_limit=dist+1)
        if self.artifacts:
            self.bus.publish('artf_xyz', [[artifact_data, round(x*1000), round(y*1000), round(z*1000)]
                                          for artifact_data, (x, y, z) in self.artifacts])
        self.send_speed_cmd(0, 0)
        self.wait(timedelta(seconds=3))
#############################################

    def play_ver2(self):
        print("SubT Challenge Ver2!")
        self.go_straight(1.0)  # go to the tunnel entrance (used to be 9m)
        self.collision_detector_enabled = True
        self.follow_wall(radius = 0.5, right_wall=self.use_right_wall,
                            timeout=timedelta(minutes=12, seconds=0))
        self.collision_detector_enabled = False

        print("Artifacts:", self.artifacts)

        print("Going HOME")
        self.return_home()

        self.send_speed_cmd(0, 0)

        if self.artifacts:
            self.bus.publish('artf_xyz', [[artifact_data, round(x*1000), round(y*1000), round(z*1000)] 
                                          for artifact_data, (x, y, z) in self.artifacts])

        self.wait(timedelta(seconds=30))

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
        # To reduce latency spikes as described in https://morepypy.blogspot.com/2019/01/pypy-for-low-latency-systems.html.
        # Increased latency leads to uncontrolled behavior and robot either missing turns or hitting walls.
        # Disabled garbage collection needs to be paired with gc.collect() at place(s) that are not time sensitive.
        gc.disable()

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
