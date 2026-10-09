"""Template: drive with a learned policy (spec 0022).

`raceforge train rl` trains a policy in the simulator (PPO) and writes a params YAML that
contains the policy as ONNX (`policy_onnx_b64`). Deploy this controller together with that YAML.
The observation and action mapping is the same code the simulator used for training
(raceforge.control.policy_io).
On the car it needs `onnxruntime` (pip install onnxruntime).
"""

import base64
from typing import Any

from raceforge.control import Command, Controller, ControllerParams, Observation
from raceforge.control.policy_io import PolicyIO, action_to_command, observation_vector
from raceforge.control.types import RobotInfo


class OnnxPolicyParams(ControllerParams):
    policy_onnx_b64: str = ""  # written by `raceforge train rl`
    lidar_max_m: float = 4.0
    reverse_factor: float = 0.5
    max_steer_rate_rad_s: float = 3.0


class OnnxPolicy(Controller[OnnxPolicyParams]):
    Params = OnnxPolicyParams

    def setup(self, info: RobotInfo) -> None:
        if not self.params.policy_onnx_b64:
            raise ValueError("no policy in the params: train one with `raceforge train rl`")
        import onnxruntime as ort  # pyright: ignore[reportMissingImports]

        model = base64.b64decode(self.params.policy_onnx_b64)
        self.session: Any = ort.InferenceSession(model, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        self.info = info
        p = self.params
        self.cfg = PolicyIO(p.lidar_max_m, p.reverse_factor, p.max_steer_rate_rad_s)
        self.last = (0.0, 0.0)
        self.steer = 0.0

    def step(self, obs: Observation) -> Command:
        import numpy as np

        vec = observation_vector(obs, self.info, self.last, self.cfg)
        out = self.session.run(None, {self.input_name: np.asarray([vec], dtype=np.float32)})[0][0]
        action = (max(-1.0, min(1.0, float(out[0]))), max(-1.0, min(1.0, float(out[1]))))
        cmd = action_to_command(action, self.info, self.steer, obs.dt_s or 0.02, self.cfg)
        self.steer, self.last = cmd.steering_rad, action
        return cmd
