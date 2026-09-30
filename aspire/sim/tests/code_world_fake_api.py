"""Synthetic API fixture. Not a physics simulator or robot experiment."""
import numpy as np

class PublicAPI:
    def __init__(self, misses=0, missing_after_close=False):
        self.robot = np.array([0.1, 0.2, 0.5])
        self.quat = np.array([0.0, 1.0, 0.0, 0.0])
        self.bowl = np.array([0.1, 0.2, 0.3])
        self.plate = np.array([0.4, 0.1, 0.3])
        self.gripper = 1.0
        self.held = False
        self.closes = 0
        self.misses = misses
        self.missing_after_close = missing_after_close
        self.calls = []
        self.poses = []
        self.grasp_plans = 0

    def functions(self):
        return {name: getattr(self, name) for name in (
            'get_observation', 'segment_sam3_text_prompt', 'mask_to_world_points',
            'plan_grasp', 'decompose_transform', 'goto_pose',
            'open_gripper', 'close_gripper')}

    def get_observation(self):
        self.calls.append('get_observation')
        return {'robot_cartesian_pos': np.r_[self.robot, self.quat, self.gripper].tolist(),
                'agentview': {'images': {'rgb': np.zeros((2, 2, 3), dtype=np.uint8),
                                          'depth': np.ones((2, 2))},
                              'intrinsics': np.eye(3), 'pose_mat': np.eye(4)}}

    def segment_sam3_text_prompt(self, rgb, prompt):
        self.calls.append('segment_sam3_text_prompt')
        if 'bowl' in prompt and self.closes and self.missing_after_close:
            return []
        mask = np.zeros((2, 2), dtype=bool)
        mask[0, 0] = 'bowl' in prompt
        mask[1, 1] = 'plate' in prompt
        return [{'mask': mask, 'score': 0.99}]

    def mask_to_world_points(self, mask, *args):
        self.calls.append('mask_to_world_points')
        p = self.bowl if mask[0, 0] else self.plate
        return np.array([p.copy()] * 3)

    def plan_grasp(self, depth, intrinsics, mask):
        self.calls.append('plan_grasp')
        self.grasp_plans += 1
        pose = np.eye(4)
        pose[:3, :3] = np.diag([1, -1, -1] if self.grasp_plans == 1 else [-1, 1, -1])
        pose[:3, 3] = self.bowl
        return np.array([pose]), np.array([1.0])

    def decompose_transform(self, pose):
        return pose[:3, 3].copy(), np.array([0.0, 1.0, 0.0, 0.0] if pose[0, 0] == 1 else [0.0, 0.0, 1.0, 0.0])

    def goto_pose(self, pos, quat, z_approach=0):
        self.calls.append('goto_pose')
        self.poses.append({'quaternion': list(quat), 'close_count': self.closes})
        target = np.array(pos)
        if self.held:
            self.bowl += target - self.robot
        self.robot = target.copy()
        self.quat = np.array(quat)

    def open_gripper(self):
        self.calls.append('open_gripper')
        self.held = False
        self.gripper = 1.0

    def close_gripper(self):
        self.calls.append('close_gripper')
        self.closes += 1
        self.held = self.closes > self.misses
        self.gripper = 0.0

