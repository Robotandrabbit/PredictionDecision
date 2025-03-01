import math
import numpy as np
import matplotlib.pyplot as plt

from nuplan.common.actor_state.ego_state import DynamicCarState, EgoState
from nuplan.planning.simulation.planner.project2.quintic_polynominal import QuinticPolynomial
from nuplan.planning.simulation.planner.project2.quartic_polynominal import QuarticPolynominal
from nuplan.planning.simulation.planner.project2.reference_line_provider import ReferenceLineProvider
from nuplan.common.actor_state.state_representation import StateVector2D, TimePoint
from nuplan.planning.simulation.planner.project2.frame_transform import cartesian2frenet

WEIGHT_PROGRESS = 1.0
WEIGHT_OFFSET = 5.0
WEIGHT_SMOOTH = 10.0

MAXIMUM_JERK = 1.5
MAXIMUM_PROGRESS = 120
MAXIMUM_OFFSET = 1.5

MAXIMUM_DECELERATION = 5.0
MAXIMUM_ACCELERATION = 5.0
class LatticePathPlanning:
    def __init__(self,
                 ego_state: EgoState,
                 reference_line_provider: ReferenceLineProvider,
                 horizon_time: TimePoint,
                 sampling_time: TimePoint) -> None:
        self.ego_state = ego_state
        self.reference_line_provider = reference_line_provider
        self.horizon_time = horizon_time
        self.sampling_time = sampling_time

    # 横向上：在 d，t 维度采样 
    def sample_lateral_end_state(self, init_frenet_state):
        end_d_candidates = np.array([-0.5, 0.0, 0.5])
        end_t_candidates = np.array([10.0, 20.0, 40.0])
        
        sampled_states = []
        for t in end_t_candidates:
            for d in end_d_candidates:
                # 基于自车当前位置采样
                state = np.array([init_frenet_state[1][0] + d, 0.0, 0.0, t])
                sampled_states.append(state)
        return sampled_states
    
    # 纵向上：在 v, t 维度采样
    def sample_lon_end_state(self, init_frenet_state, target_speed: float):
        end_states = []
        time_samples = []
        # time_samples.append(0.01)
        for i in range(1, 9):
            time_samples.append(i)
        
        for time in time_samples:
            v_upper = min(init_frenet_state[2][0] + MAXIMUM_DECELERATION * 1.0, target_speed) # 间隔时间 = 1.0s
            v_lower = max(init_frenet_state[2][0] - MAXIMUM_ACCELERATION * 1.0, 0.0)
            end_states.append([0.0, v_upper, 0.0, time])
            end_states.append([0.0, v_lower, 0.0, time])
            v_range = v_upper - v_lower
            num_of_mid_points = int(min(4, v_range / 1.0))
            if (num_of_mid_points > 0):
                velocity_seg = v_range / (num_of_mid_points + 1)
                for i in range(num_of_mid_points):
                    end_states.append([0.0, v_lower + velocity_seg * i, 0.0, time])
        
        return end_states
            

    def evaluate_trajectory(self, lon_trajectory:QuarticPolynominal, lat_trajectory:QuinticPolynomial) -> float:
        cost = 0.0
        # 1. longitudinal progress cost
        progress = lon_trajectory.get_point(lon_trajectory.get_time())
        if (progress < MAXIMUM_PROGRESS):
            cost += WEIGHT_PROGRESS * (MAXIMUM_PROGRESS - progress) / MAXIMUM_PROGRESS
        
        for t in np.arange(0.0, self.horizon_time.time_s, self.sampling_time.time_s):
            # 2. lateral smooth cost
            lateral_jerk = lat_trajectory.get_third_derivative(t)
            if (lateral_jerk > 1.0):
                cost += WEIGHT_SMOOTH * (lateral_jerk / MAXIMUM_JERK)
            
            # 3. offset cost
            lateral_offset = lat_trajectory.get_point(t)
            if (lateral_offset > 0.5):
                cost += WEIGHT_OFFSET * (lateral_offset - 0.5) / MAXIMUM_OFFSET
            
            # 4. TODO(wanghao): collision cost
        return cost

    def is_valid_lon_trajectory(self, lon_trajectory) -> bool:
        t = 0.0
        while (t < lon_trajectory.get_time()):
            velocity = lon_trajectory.get_first_derivative(t)
            accleration = lon_trajectory.get_second_derivative(t)
            if (velocity > 10.0 and velocity < 0.0):
                return False
            
            if (accleration > 5.0 and accleration < -5.0):
                return False
            
            t += 0.1
        return True

    def get_optimal_trajectory(self, lat_trajectories, lon_trajectories) -> list[QuinticPolynomial, QuarticPolynominal]:
        min_score = float('+inf')
        best_lon_trajectory = None
        best_lat_trajectory = None
        for lon_trajectory in lon_trajectories:
            # Check if lon_trajectory is valid.
            if (not self.is_valid_lon_trajectory(lon_trajectory)):
                continue
            for lat_trajectory in lat_trajectories:
                score = self.evaluate_trajectory(lon_trajectory, lat_trajectory)
                if (score < min_score):
                    min_score = score
                    best_lon_trajectory = lon_trajectory
                    best_lat_trajectory = lat_trajectory
        # Combine two 1d trajectories to one 2d trajectory
        return [best_lat_trajectory, best_lon_trajectory]


    def path_planning(self) -> tuple[float, float, float, float]:
        # calculate ego state lateral state
        cos_h = math.cos(self.ego_state.car_footprint.oriented_box.center.heading)
        sin_h = math.sin(self.ego_state.car_footprint.oriented_box.center.heading)

        init_cartesian_state = np.array([self.ego_state.car_footprint.oriented_box.center.x,
                                        self.ego_state.car_footprint.oriented_box.center.y,
                                        self.ego_state.dynamic_car_state.rear_axle_velocity_2d.magnitude() * cos_h,
                                        self.ego_state.dynamic_car_state.rear_axle_velocity_2d.magnitude() * sin_h,
                                        self.ego_state.dynamic_car_state.rear_axle_acceleration_2d.magnitude() * cos_h,
                                        self.ego_state.dynamic_car_state.rear_axle_acceleration_2d.magnitude() * sin_h])

        reference_line = np.array([self.reference_line_provider._x_of_reference_line,
                                   self.reference_line_provider._y_of_reference_line,
                                   self.reference_line_provider._heading_of_reference_line,
                                   self.reference_line_provider._kappa_of_reference_line,
                                   self.reference_line_provider._s_of_reference_line])
        
        init_frenet_state = cartesian2frenet([init_cartesian_state[0]],
                                             [init_cartesian_state[1]],
                                             [init_cartesian_state[2]],
                                             [init_cartesian_state[3]],
                                             [init_cartesian_state[4]],
                                             [init_cartesian_state[5]],
                                             reference_line[0],
                                             reference_line[1],
                                             reference_line[2],
                                             reference_line[3],
                                             reference_line[4])

               
        lat_trajectory, lon_trajectory = [], []
        # lateral(l + t) path planning (l_s, dl_s ,ddl_s, l_e, dl_e, ddl_e, time)
        end_lat_frenet_states = self.sample_lateral_end_state(init_frenet_state, )
        for end_lat_frenet_state in end_lat_frenet_states:
            print("l =", end_lat_frenet_state[0], "sampled_time =", end_lat_frenet_state[-1])
            lateral_curve = QuinticPolynomial(init_frenet_state[1][0], init_frenet_state[4][0], init_frenet_state[7][0],
                                              end_lat_frenet_state[0], end_lat_frenet_state[1], end_lat_frenet_state[2], end_lat_frenet_state[3])
            lat_trajectory.append(lateral_curve)

        # longitudinal(v + t) path planning (s_s, ds_s, dds_s, ds_e, dds_e, time)
        target_speed = 5
        end_lon_frenet_states = self.sample_lon_end_state(init_frenet_state, target_speed)
        for end_lon_frenet_state in end_lon_frenet_states:
            longitudinal_curve = QuarticPolynominal(init_frenet_state[0][0], init_frenet_state[2][0], init_frenet_state[6][0],
                                                    end_lon_frenet_state[1], end_lon_frenet_state[2], end_lon_frenet_state[3])
            lon_trajectory.append(longitudinal_curve)

        # get the optimal trajectory
        optimal_trajectory = self.get_optimal_trajectory(lat_trajectory, lon_trajectory)

        # s, v, l, time = [], [], [], []
        # print("horizon_time.time_s: ", self.horizon_time.time_s)
        # for t in np.arange(0.0, self.horizon_time.time_s, 0.1):
        #     s.append(optimal_trajectory[1].get_point(t))
        #     v.append(optimal_trajectory[1].get_first_derivative(t))
        #     l.append(optimal_trajectory[0].get_point(t))
        #     time.append(t)
        # plt.figure()
        # plt.axis("equal")
        # plt.plot(time, v)
        # plt.xlabel("Time[s]")
        # plt.ylabel("Velocity[m/s]")
        # # plt.plot(s[0], time[0], 'go')
        # plt.figure()
        # plt.axis("equal")
        # plt.plot(time, l)
        # plt.xlabel("Time[s]")
        # plt.ylabel("Lateral[m/s]")

        # get the optimal trajectory in frenet
        l = []
        dl = []
        ddl = []
        s = []
        for t in np.arange(0, self.horizon_time.time_s, self.sampling_time.time_s):
            if (t > optimal_trajectory[1].get_time()):
                break
            l.append(optimal_trajectory[0].get_point(t))
            dl.append(optimal_trajectory[0].get_first_derivative(t))
            ddl.append(optimal_trajectory[0].get_second_derivative(t))
            s.append(optimal_trajectory[1].get_point(t))
            # print("t, l, s:", t, l[-1], s[-1])
        
        return l, dl, ddl, s