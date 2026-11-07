"""Waymax, Flax and Optax operations, imported only after the CPU guard."""

import csv
from pathlib import Path

from flax import linen as nn
from flax import traverse_util
from flax.training import train_state
import jax
import jax.numpy as jnp
import numpy as np
import optax
import tensorflow as tf

from waymax import config as waymax_config
from waymax import datatypes, dynamics, env
from waymax.agents import expert
from waymax.dataloader import womd_dataloader, womd_factories

from lab_support import sha256, write_json


FEATURE_SCHEMA = "ego-neighbors-roadgraph-current-v1"


class TinyPolicy(nn.Module):
    hidden_units: int

    @nn.compact
    def __call__(self, features):
        hidden = nn.tanh(nn.Dense(self.hidden_units)(features))
        return nn.tanh(nn.Dense(2)(hidden))


def load_scenarios(data_file, config):
    dataset_config = waymax_config.DatasetConfig(
        path=str(data_file), repeat=1, num_shards=1,
        max_num_objects=config["max_num_objects"],
        max_num_rg_points=config["max_num_rg_points"],
        include_sdc_paths=True, num_paths=config["num_paths"],
        num_points_per_path=config["num_points_per_path"],
        distributed=False,
    )
    dataset = tf.data.TFRecordDataset([str(data_file)], num_parallel_reads=1)
    scenes = []
    # Parse only selected records; avoid the upstream AUTOTUNE prefetch pipeline.
    for raw in dataset.take(config["num_scenarios"]).as_numpy_iterator():
        parsed = womd_dataloader.preprocess_serialized_womd_data(raw, dataset_config)
        parsed = {key: np.asarray(value) for key, value in parsed.items()}
        scene = womd_factories.simulator_state_from_womd_dict(parsed, include_sdc_paths=True)
        scene.validate()
        if int(np.asarray(scene.object_metadata.is_sdc).sum()) != 1:
            raise ValueError("selected object subset must contain exactly one SDC")
        if scene.log_trajectory.num_timesteps < config["init_steps"] + config["rollout_steps"]:
            raise ValueError("insufficient trajectory frames")
        scenes.append(scene)
    if len(scenes) != config["num_scenarios"]:
        raise ValueError("not enough distinct fixture scenarios")
    return scenes


@jax.jit
def features_from_state(state):
    """Use current simulated objects and a static map, never future log labels."""
    observation = datatypes.sdc_observation_from_state(
        state, obs_num_steps=1, roadgraph_top_k=16,
    )
    trajectory = observation.trajectory
    ego = jnp.argmax(observation.is_ego[0])
    xy = trajectory.xy[0, :, 0, :]
    velocity = trajectory.vel_xy[0, :, 0, :]
    valid = trajectory.valid[0, :, 0]
    ego_features = jnp.array([
        velocity[ego, 0] / 20.0, velocity[ego, 1] / 20.0,
        trajectory.length[0, ego, 0] / 10.0,
        trajectory.width[0, ego, 0] / 10.0,
    ])
    other_valid = valid & ~observation.is_ego[0]
    distance = jnp.sum(xy**2, axis=-1)
    _, indices = jax.lax.top_k(-jnp.where(other_valid, distance, 1e12), k=4)
    neighbor_features = jnp.concatenate([
        xy[indices] / 50.0,
        (velocity[indices] - velocity[ego]) / 20.0,
        other_valid[indices, None].astype(jnp.float32),
    ], axis=-1)
    neighbor_features = jnp.where(other_valid[indices, None], neighbor_features, 0.0)
    roadgraph = observation.roadgraph_static_points
    map_features = jnp.concatenate([
        roadgraph.xy[0] / 50.0, roadgraph.dir_xy[0],
        roadgraph.types[0, :, None] / 20.0,
        roadgraph.valid[0, :, None].astype(jnp.float32),
    ], axis=-1)
    map_features = jnp.where(roadgraph.valid[0, :, None], map_features, 0.0)
    return jnp.clip(jnp.concatenate([
        ego_features, neighbor_features.reshape(-1), map_features.reshape(-1),
    ]), -10.0, 10.0)


class Lab:
    def __init__(self, config):
        self.config = config
        self.dynamics = dynamics.InvertibleBicycleModel()
        self.environment = env.BaseEnvironment(
            dynamics_model=self.dynamics,
            config=waymax_config.EnvironmentConfig(
                max_num_objects=config["max_num_objects"],
                init_steps=config["init_steps"],
                controlled_object=waymax_config.ObjectType.SDC,
                metrics=waymax_config.MetricsConfig(metrics_to_run=tuple(config["metrics"])),
            ),
        )
        self.step = jax.jit(self.environment.step)
        self.expert = jax.jit(lambda state: expert.infer_expert_action(state, self.dynamics))
        self.metrics = jax.jit(self.environment.metrics)
        self.action_scale = np.asarray(self.dynamics.action_spec().maximum, dtype=np.float32)

    def rollout(self, scene, policy, directory, predict=None):
        directory.mkdir(exist_ok=False)
        state = self.environment.reset(scene)
        ego = int(np.argmax(np.asarray(state.object_metadata.is_sdc)))
        initial = state
        features, targets, label_valid, positions, rows = [], [], [], [], []
        for step in range(self.config["rollout_steps"]):
            if bool(state.is_done):
                raise ValueError("episode ended before the configured rollout")
            if not bool(state.current_sim_trajectory.valid[ego, 0]):
                raise ValueError("SDC is invalid before the requested transition")
            feature = features_from_state(state)
            if not np.isfinite(np.asarray(feature)).all():
                raise ValueError("nonfinite policy observation")
            if policy == "expert":
                action = self.expert(state)
                targets.append(np.asarray(action.data[ego]) / self.action_scale)
                label_valid.append(bool(np.asarray(action.valid[ego, 0])))
            else:
                if policy == "brake":
                    command = jnp.array([-2.0, 0.0], dtype=jnp.float32)
                elif policy == "learned" and predict is not None:
                    command = predict(feature) * jnp.asarray(self.action_scale)
                else:
                    raise ValueError(f"unknown policy: {policy}")
                data = jnp.zeros((self.config["max_num_objects"], 2), dtype=jnp.float32)
                data = data.at[ego].set(command)
                action = datatypes.Action(
                    data=data, valid=state.object_metadata.is_sdc[:, None],
                )
            features.append(np.asarray(feature))
            state = self.step(state, action)
            if not bool(state.current_sim_trajectory.valid[ego, 0]):
                raise ValueError("SDC became invalid after the requested transition")
            xy = np.asarray(state.current_sim_trajectory.xy[ego, 0])
            if not np.isfinite(xy).all():
                raise ValueError("nonfinite simulated ego position")
            positions.append(xy)
            row = {
                "step": step + 1, "timestep": int(state.timestep),
                "x_m": float(xy[0]), "y_m": float(xy[1]),
                "accel_m_s2": float(action.data[ego, 0]),
                "steering_curvature_m_inv": float(action.data[ego, 1]),
            }
            for name, result in self.metrics(state).items():
                is_valid = bool(np.asarray(result.valid[ego]))
                value = float(np.asarray(result.value[ego])) if is_valid else None
                if value is not None and not np.isfinite(value):
                    raise ValueError(f"nonfinite valid metric: {name}")
                row[name] = value
            rows.append(row)
        summaries = {}
        for name in self.config["metrics"]:
            valid_values = [row[name] for row in rows if row[name] is not None]
            summaries[name] = {
                "valid_frames": len(valid_values),
                "mean": float(np.mean(valid_values)) if valid_values else None,
                "max": float(np.max(valid_values)) if valid_values else None,
            }
        summary = {
            "policy": policy, "steps": len(rows), "ego_index": ego,
            "initial_timestep": int(initial.timestep),
            "final_timestep": int(state.timestep),
            "is_done": bool(state.is_done), "metrics": summaries,
            "scope": "SDC-only state simulation; NPCs use log fallback; not a safety acceptance",
        }
        with (directory / "trajectory.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        write_json(directory / "summary.json", summary)
        np.savez_compressed(
            directory / "arrays.npz",
            features=np.asarray(features, dtype=np.float32),
            targets=np.asarray(targets, dtype=np.float32),
            label_valid=np.asarray(label_valid, dtype=bool),
            xy=np.asarray(positions, dtype=np.float32),
        )
        print(f"rollout {directory.name}: {policy}, {len(rows)} steps", flush=True)
        return {
            "summary": summary, "initial": initial, "final": state,
            "features": np.asarray(features), "targets": np.asarray(targets),
            "label_valid": np.asarray(label_valid), "xy": np.asarray(positions),
        }


def train_policy(config, expert_rollout, run, train_record, eval_record):
    features = expert_rollout["features"][expert_rollout["label_valid"]]
    targets = expert_rollout["targets"][expert_rollout["label_valid"]]
    if features.shape[0] < 16 or targets.shape != (features.shape[0], 2):
        raise ValueError("at least 16 valid expert samples are required")
    mean = features.mean(axis=0).astype(np.float32)
    scale = np.maximum(features.std(axis=0), 0.05).astype(np.float32)
    inputs = jnp.asarray((features - mean) / scale)
    labels = jnp.asarray(targets)
    model = TinyPolicy(config["hidden_units"])
    params = model.init(jax.random.PRNGKey(config["seed"]), inputs)["params"]
    state = train_state.TrainState.create(
        apply_fn=model.apply, params=params, tx=optax.adam(config["learning_rate"]),
    )

    def loss(params):
        predicted = model.apply({"params": params}, inputs)
        return jnp.mean((predicted - labels) ** 2)

    @jax.jit
    def update(current):
        value, gradients = jax.value_and_grad(loss)(current.params)
        return current.apply_gradients(grads=gradients), value

    initial_loss = float(loss(state.params))
    history = [{"update": 0, "loss": initial_loss}]
    for index in range(config["training_updates"]):
        state, value = update(state)
        if (index + 1) % 25 == 0:
            history.append({"update": index + 1, "loss": float(loss(state.params))})
    final_loss = float(loss(state.params))
    if not np.isfinite(final_loss) or not final_loss < initial_loss:
        raise ValueError("training did not produce a finite lower loss")
    if history[-1]["update"] != config["training_updates"]:
        history.append({"update": config["training_updates"], "loss": final_loss})
    arrays = {
        "feature_mean": mean, "feature_scale": scale,
        **{"params/" + "/".join(key): np.asarray(value)
           for key, value in traverse_util.flatten_dict(state.params).items()},
    }
    checkpoint = run / "checkpoint.npz"
    np.savez_compressed(checkpoint, **arrays)
    np.savez_compressed(run / "training-samples.npz", features=features, targets=targets)
    report = {
        "status": "PASS", "algorithm": "tiny supervised behavior cloning, not RL",
        "feature_schema": FEATURE_SCHEMA, "feature_dimension": int(features.shape[1]),
        "training_samples": int(features.shape[0]),
        "training_record": train_record, "evaluation_record": eval_record,
        "updates": config["training_updates"], "seed": config["seed"],
        "initial_loss": initial_loss, "final_loss": final_loss,
        "loss_history": history,
        "checkpoint": checkpoint.name, "checkpoint_sha256": sha256(checkpoint),
        "scope": "One training scene and one held-out fixture scene; no generalization claim",
    }
    write_json(run / "training.json", report)
    return make_predictor(model, state.params, mean, scale), report


def make_predictor(model, params, mean, scale):
    @jax.jit
    def predict(features):
        return model.apply(
            {"params": params},
            (features - jnp.asarray(mean)) / jnp.asarray(scale),
        )
    return predict


def load_predictor(config, run):
    import json
    report = json.loads((run / "training.json").read_text())
    if report["feature_schema"] != FEATURE_SCHEMA:
        raise ValueError("checkpoint feature schema mismatch")
    checkpoint = run / report["checkpoint"]
    if (checkpoint.is_symlink() or checkpoint.name != "checkpoint.npz"
            or checkpoint.resolve().parent != run.resolve()
            or checkpoint.stat().st_size > 1024 * 1024):
        raise ValueError("unexpected checkpoint path")
    if sha256(checkpoint) != report["checkpoint_sha256"]:
        raise ValueError("checkpoint SHA256 mismatch")
    with np.load(checkpoint, allow_pickle=False) as archive:
        mean, scale = archive["feature_mean"], archive["feature_scale"]
        flat = {
            tuple(key[len("params/"):].split("/")): jnp.asarray(archive[key])
            for key in archive.files if key.startswith("params/")
        }
    model = TinyPolicy(config["hidden_units"])
    params = traverse_util.unflatten_dict(flat)
    expected = model.init(
        jax.random.PRNGKey(config["seed"]),
        jnp.zeros((report["feature_dimension"],), dtype=jnp.float32),
    )["params"]
    expected_flat = traverse_util.flatten_dict(expected)
    if set(flat) != set(expected_flat) or any(
        flat[key].shape != expected_flat[key].shape for key in flat
    ):
        raise ValueError("checkpoint parameter schema mismatch")
    if (mean.shape != (report["feature_dimension"],) or scale.shape != mean.shape
            or not np.isfinite(mean).all() or not np.isfinite(scale).all()
            or not np.all(scale > 0)
            or any(not np.isfinite(np.asarray(value)).all() for value in flat.values())):
        raise ValueError("checkpoint normalization or parameters are invalid")
    return make_predictor(model, params, mean, scale)


def check_future_observation_independence(lab, scene):
    state = lab.environment.reset(scene)
    future = jnp.arange(state.log_trajectory.num_timesteps) > state.timestep
    changed_log = state.log_trajectory.replace(
        x=jnp.where(future, state.log_trajectory.x + 1000, state.log_trajectory.x),
        y=jnp.where(future, state.log_trajectory.y - 1000, state.log_trajectory.y),
    )
    original = np.asarray(features_from_state(state))
    changed = np.asarray(features_from_state(state.replace(log_trajectory=changed_log)))
    if not np.array_equal(original, changed):
        raise ValueError("policy observation depends on perturbed future log positions")
    return {
        "status": "PASS", "feature_schema": FEATURE_SCHEMA,
        "feature_dimension": int(original.size),
        "max_feature_difference": float(np.max(np.abs(original - changed))),
        "intervention": "Change future logged x/y by +1000/-1000 metres, keep current sim/map fixed",
        "scope": "This intervention only; not proof of every possible information leak",
    }


def plot_rollouts(scene, rollouts, output):
    import matplotlib.pyplot as plt

    figure, axis = plt.subplots(figsize=(10, 6))
    roadgraph = scene.roadgraph_points
    valid = np.asarray(roadgraph.valid)
    points = np.asarray(roadgraph.xy)[valid]
    center = np.asarray(rollouts[0][1]["initial"].current_sim_trajectory.xy[
        rollouts[0][1]["summary"]["ego_index"], 0,
    ])
    local = points[np.linalg.norm(points - center, axis=-1) < 120]
    stride = max(1, len(local) // 4000)
    axis.scatter(local[::stride, 0], local[::stride, 1],
                 s=2, color="#b7bdc5", alpha=0.7, label="Static map")
    colors = ("#16845b", "#c24940", "#2768b1")
    for (name, result), color in zip(rollouts, colors):
        axis.plot(result["xy"][:, 0], result["xy"][:, 1], color=color,
                  linewidth=2, label=name)
    axis.set_aspect("equal", adjustable="datalim")
    axis.set_xlabel("Global x (m)")
    axis.set_ylabel("Global y (m)")
    axis.set_title("Waymax CPU fixture: SDC trajectories")
    axis.legend(loc="best")
    axis.grid(alpha=0.15)
    figure.tight_layout()
    figure.savefig(output, dpi=120)
    plt.close(figure)
