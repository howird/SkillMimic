# Copyright (c) 2018-2022, NVIDIA Corporation
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice, this
#    list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# 3. Neither the name of the copyright holder nor the names of its
#    contributors may be used to endorse or promote products derived from
#    this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import torch
from torch import Tensor
from typing import Tuple
from enum import Enum

from utils import torch_utils
from utils.motion_data_handler import MotionDataHandler

from isaacgym import gymapi
from isaacgym import gymtorch
from isaacgym.torch_utils import *

from env.tasks.humanoid_object_task import HumanoidWholeBodyWithObject

TAR_ACTOR_ID = 1
TAR_FACING_ACTOR_ID = 2

class HRLScoringLayup(HumanoidWholeBodyWithObject):
    class StateInit(Enum):
        Default = 0
        Start = 1
        Random = 2
        Hybrid = 3

    def __init__(self, cfg, sim_params, physics_engine, device_type, device_id, headless):
        state_init = cfg["env"]["stateInit"]
        self._state_init = HRLScoringLayup.StateInit[state_init]

        # self.motion_id_test = 3 # easy/018pickle_run_015_025_004.pt
        
        # self._enable_task_obs = True #cfg["env"]["enableTaskObs"]
        
        # self.condition_size = 0
        self.goal_size = 4 + 1

        # self.cfg["env"]["numActions"] = 66

        self.motion_file = cfg['env']['motion_file']
        self.play_dataset = cfg['env']['playdataset']
        self.robot_type = cfg["env"]["asset"]["assetFileName"]
        self.reward_weights_default = cfg["env"]["rewardWeights"]
        self.save_images = cfg['env']['saveImages']
        self.init_vel = cfg['env']['initVel']
        self.ball_size = cfg['env']['ballSize']

        super().__init__(cfg=cfg,
                         sim_params=sim_params,
                         physics_engine=physics_engine,
                         device_type=device_type,
                         device_id=device_id,
                         headless=headless)
        # self._goal_position  = torch.tensor([2,-6], device=self.device, dtype=torch.float).repeat(self.num_envs, 1)
        
        self._load_motion(self.motion_file)

        self._goal_position = torch.zeros([self.num_envs, 2], device=self.device, dtype=torch.float)

        self.reached_target = torch.zeros(
            self.num_envs, device=self.device, dtype=torch.bool)

        self._termination_heights = torch.tensor(self.cfg["env"]["terminationHeight"], device=self.device, dtype=torch.float)

        # self.control_signal = 5 #默认直行

        
        return

    def get_task_obs_size(self):
        if (self._enable_task_obs):
            obs_size = self.goal_size
        else:
            obs_size = 0
        return obs_size

    def _load_motion(self, motion_file):
        self.skill_name = motion_file.split('/')[-1] #metric
        # self.max_episode_length = 800
        if self.cfg["env"]["episodeLength"] > 0:
            self.max_episode_length =  self.cfg["env"]["episodeLength"]

        self._motion_data = MotionDataHandler(motion_file, self.device, self._key_body_ids, self.cfg, self.num_envs, self.max_episode_length, self.reward_weights_default, self.init_vel)

        return

    def _reset_actors(self, env_ids):
        if self._state_init == HRLScoringLayup.StateInit.Start \
              or self._state_init == HRLScoringLayup.StateInit.Random:
            self._reset_random_ref_state_init(env_ids) #V1 Random Ref State Init (RRSI)
        else:
            assert(False), "Unsupported state initialization strategy: {:s}".format(str(self._state_init))

        super()._reset_actors(env_ids)

        return

    def _reset_humanoid(self, env_ids):
        self._humanoid_root_states[env_ids, 0:3] = self.init_root_pos[env_ids]
        self._humanoid_root_states[env_ids, 3:7] = self.init_root_rot[env_ids]
        self._humanoid_root_states[env_ids, 7:10] = self.init_root_pos_vel[env_ids]
        self._humanoid_root_states[env_ids, 10:13] = self.init_root_rot_vel[env_ids]
        
        self._dof_pos[env_ids] = self.init_dof_pos[env_ids]
        self._dof_vel[env_ids] = self.init_dof_pos_vel[env_ids]
        return


    def _reset_random_ref_state_init(self, env_ids): #Z11
        num_envs = env_ids.shape[0]

        motion_ids = self._motion_data.sample_motions(num_envs)
        motion_times = self._motion_data.sample_time(motion_ids)

        _, \
        self.init_root_pos[env_ids], self.init_root_rot[env_ids],  self.init_root_pos_vel[env_ids], self.init_root_rot_vel[env_ids], \
        self.init_dof_pos[env_ids], self.init_dof_pos_vel[env_ids], \
        self.init_obj_pos[env_ids], self.init_obj_pos_vel[env_ids], self.init_obj_rot[env_ids], self.init_obj_rot_vel[env_ids] \
            = self._motion_data.get_initial_state(env_ids, motion_ids, motion_times)

        # if self.show_motion_test == False:
        #     print('motionid:', self.hoi_data_dict[int(self.envid2motid[0])]['hoi_data_text'], \
        #         'motionlength:', self.hoi_data_dict[int(self.envid2motid[0])]['hoi_data'].shape[0]) #ZC
        #     self.show_motion_test = True

        return

    def _compute_reset(self):
        root_pos = self._humanoid_root_states[..., 0:3]
        self.reset_buf[:], self._terminate_buf[:] = compute_humanoid_reset(self.reset_buf, self.progress_buf,
                                                                           self._contact_forces,self._rigid_body_pos,self._target_states[..., 0:3],
                                                                            root_pos, self._goal_position,
                                                                            self.max_episode_length, self._enable_early_termination, self._termination_heights
                                                                            )
        return
    
    def _compute_reward(self, actions):
        root_pos = self._humanoid_root_states[..., 0:3]
        root_vel = self._humanoid_root_states[..., 7:10]
        ball_pos = self._target_states[..., 0:3]
        ball_vel = self._target_states[..., 7:10]
        self.rew_buf[:], self.reached_target = compute_scoring_reward(root_pos, root_vel, ball_pos, ball_vel, self._tar_contact_forces, self._goal_position, self.reached_target, self._rigid_body_pos, self._tar_contact_forces)
        # ball_pos_over_1p5_idx = ball_pos[:,2] > 1.5
        # ball_pos_over_1p5 = ball_pos[ball_pos_over_1p5_idx,2]
        # if any(self.reached_target):
        #     # print("hahahahh")
        #     a=0
        # print(root_pos[0, :2], self._goal_position)
        # print('reward:', f'{float(self.rew_buf[0]):.4}', end=' ')
        # print()
        # print('reward:', end=' ')
        # for r in self.rew_buf:
        #     print(f"{r.item()*1e5: 10.2f}", end=' ')
        # print()
        return

    def _reset_envs(self, env_ids):

        super()._reset_envs(env_ids)  

        if(len(env_ids)>0):
            n = len(env_ids)

            # self._goal_position  = torch.tensor([-4,6], device=self.device, dtype=torch.float).repeat(n, 1)

            # # Step 1: 生成[0, 1)范围的随机数据
            # random_radii = torch.rand([n], device=self.device, dtype=torch.float) * 6 + 2  # 半径 [2, 8]
            # random_angles = torch.rand([n], device=self.device, dtype=torch.float) * 2 * np.pi  # 角度 [0, 2π]
            # # Step 2: 转换为直角坐标
            # self._goal_position[env_ids, 0] = random_radii * torch.cos(random_angles)  # x 坐标
            # self._goal_position[env_ids, 1] = random_radii * torch.sin(random_angles)  # y 坐标

            # n = self.num_envs
            # # Generate spiral points
            # angles = torch.linspace(0, 2 * np.pi * n, n, device=self.device, dtype=torch.float) # Angles from 0 to 2πn
            # radii = torch.linspace(2, 8, n, device=self.device, dtype=torch.float) # Radii from 2 to 8

            # Convert to Cartesian coordinates
            # self._goal_position[env_ids, 0] = torch.rand(n).to("cuda")*5#radii[env_ids] * torch.cos(angles[env_ids]) # x coordinates
            # self._goal_position[env_ids, 1] = torch.rand(n).to("cuda")*5#radii[env_ids] * torch.sin(angles[env_ids]) # y coordinates

            d = torch.rand(n).to("cuda")*6 + 2
            theta = torch.rand(n).to("cuda")*torch.pi*2
            x = torch.sin(theta)*d
            y = torch.cos(theta)*d
            self._goal_position[env_ids, 0] = self._humanoid_root_states[env_ids, 0]+x
            self._goal_position[env_ids, 1] = self._humanoid_root_states[env_ids, 1]+y

            self.reached_target[env_ids] = False

        return

    def _compute_observations(self, env_ids=None):
        obs = self._compute_humanoid_obs(env_ids)

        obj_obs = self._compute_obj_obs(env_ids)

        if(self._enable_task_obs):
            task_obs = self._compute_task_obs(env_ids)
            obs = torch.cat([obs, obj_obs, task_obs], dim=-1)
        else:
            obs = torch.cat([obs, obj_obs], dim=-1)

        if (env_ids is None):
            self.obs_buf[:] = obs
        else:
            self.obs_buf[env_ids] = obs

        return
    
                
    def _compute_task_obs(self, env_ids=None):
        if (env_ids is None):
            root_pos = self._humanoid_root_states[..., 0:3]
            goal_pos = self._goal_position
            root_rot = self._humanoid_root_states[..., 3:7]
            reached_target = self.reached_target
        else:
            root_pos = self._humanoid_root_states[env_ids, 0:3]
            goal_pos = self._goal_position[env_ids]
            root_rot = self._humanoid_root_states[env_ids, 3:7]
            reached_target = self.reached_target[env_ids]

        obs = compute_heading_observations(root_pos, goal_pos, root_rot, reached_target)

        return obs

    def _draw_task(self):

        point_color = np.array([[1.0, 0.0, 0.0]], dtype=np.float32)  # Red for goal position

        self.gym.clear_lines(self.viewer)

        goal_positions = self._goal_position.cpu().numpy()

        for i, env_ptr in enumerate(self.envs):
            # Draw goal position as a small line segment (point)
            if self.reached_target[i]:
                point_color = np.array([[0.0, 1.0, 0.0]], dtype=np.float32)
            goal_pos = goal_positions[i]
            goal_verts = np.array([goal_pos[0]-0.25, goal_pos[1]-0.25, 2., goal_pos[0] + 0.25, goal_pos[1] + 0.25, 2.], dtype=np.float32)
            goal_verts = goal_verts.reshape([1, 6])
            self.gym.add_lines(self.viewer, env_ptr, goal_verts.shape[0], goal_verts, point_color)
            goal_verts = np.array([goal_pos[0]-0.25, goal_pos[1]+0.25, 2., goal_pos[0] + 0.25, goal_pos[1] - 0.25, 2.], dtype=np.float32)
            goal_verts = goal_verts.reshape([1, 6])
            self.gym.add_lines(self.viewer, env_ptr, goal_verts.shape[0], goal_verts, point_color)

        return

    def get_num_amp_obs(self):
        return 323 + len(self.cfg["env"]["keyBodies"])*3 + 6  #0
    
#####################################################################
###=========================jit functions=========================###
#####################################################################


# @torch.jit.script
def compute_heading_observations(root_pos, goal_pos, root_rot, reached_target):
    heading_rot = torch_utils.calc_heading_quat_inv(root_rot) #world: torch_utils.calc_heading_quat(root_rot)
    
    facing_dir = torch.zeros_like(root_pos)
    facing_dir[..., 0] = 1.0
    local_facing_dir = torch_utils.quat_rotate(heading_rot, facing_dir)  

    local_tar_pos = goal_pos - root_pos[..., 0:2]
    local_tar_pos_3d = torch.cat([local_tar_pos, torch.zeros_like(local_tar_pos[..., 0:1])], dim=-1)  # 扩展到3D向量
    local_tar_pos = quat_rotate(heading_rot, local_tar_pos_3d)
    local_tar_pos = local_tar_pos[..., 0:2]
    
    # Calculate relative angle in radians
    angle = torch.atan2(local_tar_pos[:, 1], local_tar_pos[:, 0]) - torch.atan2(local_facing_dir[:, 1], local_facing_dir[:, 0])
    angle = torch_utils.normalize_angle(angle)
    
    # Compute cosine and sine of the angle
    cos_angle = torch.cos(angle)
    sin_angle = torch.sin(angle)

    goal_angle = torch.stack([cos_angle, sin_angle], dim=-1)

    obs = torch.cat([local_tar_pos, goal_angle, reached_target.float().unsqueeze(dim=-1)], dim=-1) #world: goal_pos - root_pos[..., :2]
    # obs = torch.cat([local_tar_pos, goal_angle], dim=-1) #world: goal_pos - root_pos[..., :2]

    return obs

# # @torch.jit.script
# def compute_scoring_reward(root_pos, root_vel, ball_pos, ball_vel, ball_contact, goal_pos, reached_target):

#     distance_to_goal = torch.norm(root_pos[:, :2] - goal_pos, dim=-1)
#     position_reward = torch.exp(-distance_to_goal)
#     at_target = distance_to_goal < 0.5
#     reached_target = reached_target | at_target
#     distance_reward = torch.where(reached_target, torch.tensor(0.3, device=root_pos.device), position_reward)
#     # print(position_reward[0].item())

#     # 摔倒惩罚
#     fall_threshold = torch.tensor(0.6, device=root_pos.device)
#     fall_penalty = torch.where(root_pos[..., 2] > fall_threshold, torch.tensor(1.0, device=root_pos.device), torch.tensor(0.1, device=root_pos.device))

#     # # 掉球惩罚
#     # distance_to_ball = torch.norm(root_pos - ball_pos, dim=-1)
#     # drop_ball_threshold = torch.tensor(1.2, device=root_pos.device)
#     # drop_ball_penalty = torch.where(distance_to_ball < drop_ball_threshold, torch.tensor(1.0, device=root_pos.device), torch.tensor(0.1, device=root_pos.device))

#     ball_height_reward = torch.where(reached_target, torch.exp(-torch.abs(ball_pos[:, 2]-2.)), torch.tensor(0., device=root_pos.device))#torch.exp(-torch.abs(ball_pos[:, 2]-2.5))

#     # #still punish
#     v = torch.norm(root_vel,dim=-1)
#     v_penalty = torch.where(v < 0.5, torch.tensor(0., device=root_pos.device), torch.tensor(1., device=root_pos.device))

#     # 组合奖励
#     reward = v_penalty*fall_threshold*(distance_reward + ball_height_reward)#torch.exp(-torch.abs(ball_pos[:, 2]-2.))#position_reward*fall_penalty + ball_height_reward

#     return reward, reached_target

# @torch.jit.script
def compute_scoring_reward(root_pos, root_vel, ball_pos, ball_vel, ball_contact, goal_pos, reached_target, rigid_body_pos, tar_contact_forces):

    goal_pos_3d = torch.ones_like(ball_pos, device=root_pos.device)
    goal_pos_3d[:,:2] = goal_pos
    goal_pos_3d[:,2] = 2.5

    distance_to_goal = torch.norm(ball_pos - goal_pos_3d, dim=-1)

    ball_landing_pos_xy = calculate_landing_position(ball_vel, ball_pos, 2.0)
    distance_to_goal_xy = torch.norm(ball_landing_pos_xy - goal_pos, dim=-1)
    # print(distance_to_goal_xy)
    ball_contact = torch.any(torch.abs(tar_contact_forces[..., :]) > 0.1, dim=-1)#.to(float)
    at_target = (distance_to_goal_xy < 0.3) & (ball_pos[:,2] > 2.) & (~ball_contact)
    reached_target = reached_target | at_target
    # if any(reached_target):
    #     print(reached_target)
    reached_reward = torch.where(reached_target, torch.tensor(1., device=root_pos.device), torch.tensor(0., device=root_pos.device))

    # position_reward
    # close_reward = torch.exp(-distance_to_goal*0.5)
    # far_reward = 1 - torch.exp(-distance_to_goal*0.5)
    # position_reward = torch.where(reached_target, far_reward, close_reward)
    position_reward = torch.exp(-distance_to_goal*0.5)

    # # fall_penalty
    # fall_threshold = torch.tensor(0.8, device=root_pos.device)
    # fall_penalty = torch.where(root_pos[..., 2] > fall_threshold, torch.tensor(1.0, device=root_pos.device), torch.tensor(0.1, device=root_pos.device))

    # ball_height_reward
    ball_height_reward = torch.exp(-torch.abs(ball_pos[:, 2]-2.5))#torch.where(reached_target, torch.exp(-torch.abs(ball_pos[:, 2]-2.)), torch.tensor(0., device=root_pos.device))#torch.exp(-torch.abs(ball_pos[:, 2]-2.5))

    # v_penalty
    v = torch.norm(root_vel,dim=-1)
    v_penalty = torch.where(v < 0.5, torch.tensor(0.1, device=root_pos.device), torch.tensor(1., device=root_pos.device))

    # 组合奖励
    reward = v_penalty*(position_reward + reached_reward + ball_height_reward*0.2)#torch.exp(-torch.abs(ball_pos[:, 2]-2.))#position_reward*fall_penalty + ball_height_reward
    # reward = reached_reward
    # print("position_reward",position_reward)
    # print("ball_height_reward*0.2",ball_height_reward*0.2)

    # if any(reached_target):
    #     # print("hahahahh")
    #     reached_ball_pos = ball_pos[reached_target]
    #     reached_goal_pos_3d = goal_pos_3d[reached_target]
    #     reached_reward_only = reward[reached_target]

    return reward, reached_target


def calculate_landing_position(v0, position0, h):
    """
    计算篮球在降落至高度h时的xy坐标。

    :param v0: 初始速度的向量，形状为(batch, 3)。
    :param position0: 初始位置的向量，形状为(batch, 3)。
    :param h: 目标高度。
    :return: 在高度h时的xy坐标，形状为(batch, 2)。如果篮球无法达到高度h，则返回False。
    """
    g = 9.8 # 重力加速度，单位 m/s^2

    # 计算篮球的最大高度
    h_max = position0[:, 2] + v0[:, 2]**2 / (2 * g)

    # 检查篮球是否能达到高度h
    h = torch.where(h_max < h, torch.tensor(0., device=v0.device), h)

    # 计算达到高度h所需的时间t
    t = (torch.sqrt(v0[:, 2]**2 + 2 * g * (position0[:, 2] - h)) - v0[:, 2]) / g

    # 计算x和y坐标
    x = position0[:, 0] + v0[:, 0] * t
    y = position0[:, 1] + v0[:, 1] * t

    x = torch.where(h_max < h, torch.tensor(100., device=v0.device), x)
    y = torch.where(h_max < h, torch.tensor(100., device=v0.device), y)

    # 组合x和y坐标
    xy_positions = torch.stack((x, y), dim=1)

    

    return xy_positions

# @torch.jit.script
def compute_humanoid_reset(reset_buf, progress_buf, contact_buf, rigid_body_pos, ball_pos, root_pos, goal_pos,
                           max_episode_length, enable_early_termination, termination_heights):
    # type: (Tensor, Tensor, Tensor, Tensor, Tensor, Tensor, Tensor, float, bool, Tensor) -> Tuple[Tensor, Tensor]
    
    terminated = torch.zeros_like(reset_buf)
    # distance_to_goal = torch.norm(root_pos[:, :2] - goal_pos, dim=-1) 
    # terminated = torch.where(distance_to_goal < 0.45, torch.ones_like(reset_buf), terminated)
    # if(terminated[0]==1):
    #     print("stop————————————————————————————————————————————————————————————————————————————————————————————————————————————————————————————")

    # contact_body_ids = [0,1,2,5,6,9,10,11,12,13,14,15,16,17,34,35,36]
    # body_contact_buf = contact_buf[:, contact_body_ids, :].clone()
    # body_contact = torch.all(torch.abs(body_contact_buf) < 0.1, dim=-1)
    # body_contact = torch.all(body_contact, dim=-1).to(float) # =1 when no contact happens to the body


    if (enable_early_termination):
        has_fallen = root_pos[..., 2] < termination_heights
        has_fallen *= (progress_buf > 1) # 本质就是 与
        terminated = torch.where(has_fallen, torch.ones_like(reset_buf), terminated)

        # distance_to_goal = torch.norm(root_pos[:, :2] - goal_pos, dim=-1)

        # lhand_pos = rigid_body_pos[:, 18, :]
        # lhand_ball_distance = torch.norm(lhand_pos-ball_pos,dim=-1)
        # terminated = torch.where((lhand_ball_distance<0.3) & (distance_to_goal > 0.5), torch.ones_like(reset_buf), terminated)

    # reset = torch.where(progress_buf >= envid2episode_lengths-1, torch.ones_like(reset_buf), terminated) #ZC

    reset = torch.where(progress_buf >= max_episode_length -1, torch.ones_like(reset_buf), terminated)
    # reset = torch.zeros_like(reset_buf) #ZC300

    return reset, terminated