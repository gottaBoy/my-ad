#!/usr/bin/env python3
"""Independent CARLA RPC/sensor gates; requires an idle, exclusively ticked world.

The CLI requires Docker on ARM64 and --allow-world-mutation. It does not load
maps, change weather, or control existing actors. It temporarily changes world
settings and, in sensors mode, creates/moves two sensors; in actors mode, it
creates one autopiloted vehicle, one AI walker and its controller, and uses
Traffic Manager. Global time advances cannot be undone; settings and owned
actors are restored/removed in finally. Concurrent ticking is a gate failure.

run_gate(..., api=FakeCarla) is for harness unit tests only: injected runs use
harness-test stage IDs/scopes and cannot produce a production stage PASS.
Version compatibility means exact client/server version-string equality, not
proof of identical binaries. Provenance is caller-supplied and hashed whole.
Timeout bounds each RPC and sensor wait, not the entire requested tick count.
"""

import argparse
import hashlib
import importlib
import importlib.machinery
import importlib.util
import json
import math
from pathlib import Path
import platform
import queue
import shutil
import struct
import sys
import tempfile
import threading
import time


_spec = importlib.util.spec_from_file_location(
    "carla_stage_report", Path(__file__).with_name("stage_report.py"))
stage_report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stage_report)

STAGES = {
    "rpc": ("carla-runtime-rpc",
            "Endpoint CARLA RPC/world/synchronous ticks and observed client architecture; "
            "server architecture/build, sensors and ROS/Autoware unverified"),
    "sensors": ("carla-runtime-sensors",
                "Endpoint CARLA RGB/LiDAR alignment/payloads and observed client architecture; "
                "server architecture/build, GPU/backend and ROS/Autoware unverified"),
    "actors": ("carla-runtime-actors",
               "Endpoint CARLA Traffic Manager vehicle motion, AI walker motion, and observed client architecture; "
               "server architecture/build, sensors and ROS/Autoware unverified"),
}
STEP_SECONDS = 0.05
WARMUP_TICKS = 10
WIDTH, HEIGHT = 320, 240
QUEUE_SIZE = 32
MAX_SENSOR_BYTES = 16 * 1024 * 1024
ACTOR_WARMUP_TICKS = 2
MIN_TRAVELLED_METERS = 0.1
SETTINGS_FIELDS = (
    "synchronous_mode", "no_rendering_mode", "fixed_delta_seconds", "substepping",
    "max_substep_delta_time", "max_substeps", "max_culling_distance",
    "deterministic_ragdolls", "tile_stream_distance", "actor_active_distance", "spectator_as_ego",
)


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _finite(value, name):
    _require(type(value) in (int, float) and math.isfinite(value), f"{name} must be finite")
    return value


def _integer(value, name, minimum=0):
    _require(type(value) is int and value >= minimum, f"{name} must be an integer >= {minimum}")
    return value


def _object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _constant(value):
    raise ValueError(f"non-finite JSON constant: {value}")


def read_provenance(path):
    data = json.loads(path.read_bytes(), object_pairs_hook=_object, parse_constant=_constant)
    _require(isinstance(data, dict), "provenance must be a JSON object")
    _require(type(data.get("schema_version")) is int and data["schema_version"] == 1,
             "provenance schema_version must be integer 1")
    sources = data.get("sources")
    _require(isinstance(sources, dict), "provenance sources must be an object")
    for name in ("carla", "ue"):
        source = sources.get(name)
        _require(isinstance(source, dict), f"missing provenance source: {name}")
        for field in ("location", "revision"):
            _require(isinstance(source.get(field), str) and source[field].strip(),
                     f"{name}.{field} must be nonempty")
    return {name: sources[name] for name in ("carla", "ue")}


def runtime_environment():
    result = {"docker": Path("/.dockerenv").is_file(), "machine": platform.machine(),
              "injected": False}
    _require(result["docker"] and result["machine"] in ("aarch64", "arm64"),
             "production CLI requires Docker and ARM64")
    return result


def _sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def snapshot_client_binary(work, carla_module=None):
    extension = carla_module if carla_module is not None else importlib.import_module("carla")
    filename = getattr(extension, "__file__", None)
    _require(isinstance(filename, str) and bool(filename), "carla.__file__ is missing")
    source = Path(filename).resolve()
    if source.name in ("__init__.py", "__init__.pyc"):
        extension = importlib.import_module("carla.libcarla")
        filename = getattr(extension, "__file__", None)
        _require(isinstance(filename, str) and bool(filename), "carla.libcarla.__file__ is missing")
        source = Path(filename).resolve()
    _require(source.is_file() and source.stat().st_size > 0
             and any(source.name.endswith(suffix) for suffix in importlib.machinery.EXTENSION_SUFFIXES),
             "loaded CARLA module must identify a nonempty native extension, not __init__.py")
    module_name = getattr(extension, "__name__", None)
    _require(isinstance(module_name, str) and bool(module_name), "loaded CARLA module name is missing")
    destination = work / source.name
    # Preserve the actual loaded extension after the disposable install disappears.
    shutil.copyfile(source, destination)
    checksum = _sha256_file(source)
    _require(_sha256_file(destination) == checksum, "client extension changed while copying evidence")
    return {"module": module_name, "loaded_path": str(source),
            "snapshot_path": str(destination), "sha256": checksum,
            "scope": "observed client extension only; endpoint/server build unverified"}


def validate_versions(client_version, server_version):
    _require(all(isinstance(value, str) and value.strip()
                 for value in (client_version, server_version)), "version strings must be nonempty")
    _require(client_version == server_version, "client/server version mismatch (policy: exact)")
    return {"policy": "exact", "client": client_version, "server": server_version}


def validate_snapshot(snapshot, previous=None, tick_frame=None):
    frame = _integer(snapshot.frame, "snapshot frame")
    stamp = snapshot.timestamp
    _integer(stamp.frame, "timestamp frame")
    _require(stamp.frame == frame, "snapshot/timestamp frame mismatch")
    elapsed = _finite(stamp.elapsed_seconds, "elapsed_seconds")
    delta = _finite(stamp.delta_seconds, "delta_seconds")
    _require(elapsed >= 0 and delta >= 0, "negative world time")
    if tick_frame is not None:
        _integer(tick_frame, "tick frame")
        _require(tick_frame == frame, "tick/snapshot frame mismatch")
    if previous is not None:
        _require(frame == previous["frame"] + 1, "world frames are not consecutive")
        _require(elapsed > previous["elapsed_seconds"], "world time is not strictly increasing")
        _require(abs(elapsed - previous["elapsed_seconds"] - STEP_SECONDS) <= 1e-4
                 and abs(delta - STEP_SECONDS) <= 1e-4, "world timestep differs from fixed delta")
    return {"frame": frame, "elapsed_seconds": elapsed, "delta_seconds": delta}


def _packet_frame(packet, snapshot):
    _integer(packet.frame, "sensor frame")
    _require(packet.frame == snapshot["frame"], "sensor/world frame mismatch")
    stamp = _finite(packet.timestamp, "sensor timestamp")
    _require(abs(stamp - snapshot["elapsed_seconds"]) <= 1e-4, "sensor/world timestamp mismatch")


def _payload(packet):
    _require(len(packet.raw_data) <= MAX_SENSOR_BYTES, "sensor payload exceeds size limit")
    return bytes(packet.raw_data)


def validate_image(data, width, height):
    _integer(width, "image width", 1)
    _integer(height, "image height", 1)
    _require((width, height) == (WIDTH, HEIGHT), "camera dimensions differ from requested dimensions")
    _require(len(data) == width * height * 4, "BGRA payload size mismatch")
    channels = (data[0::4], data[1::4], data[2::4])
    _require(any(min(channel) != max(channel) for channel in channels),
             "spatially constant RGB image (alpha ignored)")
    return {"width": width, "height": height, "bytes": len(data),
            "rgb_sha256": hashlib.sha256(b"".join(channels)).hexdigest(),
            "bgra_sha256": hashlib.sha256(data).hexdigest()}


def validate_lidar(data):
    _require(len(data) > 0 and len(data) % 16 == 0, "LiDAR payload must contain nonempty XYZI float32 points")
    max_radius_squared = 0.0
    for x, y, z, intensity in struct.iter_unpack("<ffff", data):
        _require(all(math.isfinite(value) for value in (x, y, z, intensity)), "non-finite LiDAR point")
        max_radius_squared = max(max_radius_squared, x * x + y * y + z * z)
    _require(max_radius_squared > 1e-12, "LiDAR contains only zero-range points")
    return {"points": len(data) // 16, "bytes": len(data),
            "format": "little-endian float32 XYZI", "sha256": hashlib.sha256(data).hexdigest()}


class AlignedQueue:
    def __init__(self):
        self.queue = queue.Queue(maxsize=QUEUE_SIZE)
        self.overflow = threading.Event()
        self.failure = None
        self.closed = threading.Event()
        self.stale = 0

    def __call__(self, packet):
        if self.failure is not None:
            # PythonCarla's callback thread is pooled; keep the first error
            # sticky because later callbacks may not be invoked after failure.
            return
        if self.overflow.is_set():
            self.failure = ValueError(
                "sensor queue overflow detected before packet enqueue")
            return
        if self.closed.is_set():
            return
        try:
            self.queue.put_nowait(packet)
        except queue.Full:
            self.overflow.set()
            self.failure = ValueError(
                f"sensor queue overflow while receiving frame {packet.frame}")

    def frame(self, expected, timeout):
        self._check_failure()
        deadline = time.monotonic() + timeout
        while True:
            _require(not self.overflow.is_set(), "sensor queue overflow")
            remaining = deadline - time.monotonic()
            _require(remaining > 0, f"sensor timeout waiting for frame {expected}")
            try:
                packet = self.queue.get(timeout=remaining)
            except queue.Empty as error:
                raise ValueError(f"sensor timeout waiting for frame {expected}") from error
            _integer(packet.frame, "sensor frame")
            if packet.frame < expected:
                self.stale += 1
                continue
            _require(packet.frame == expected, "sensor delivered a future/misaligned frame")
            _require(not self.overflow.is_set(), "sensor queue overflow")
            return packet

    def _check_failure(self):
        if self.failure is not None:
            raise self.failure
        if self.overflow.is_set():
            raise ValueError("sensor queue overflow")

    check_failure = _check_failure


def _settings_view(settings):
    result = {name: getattr(settings, name) for name in SETTINGS_FIELDS}
    for name, value in result.items():
        if value is not None and type(value) is not bool:
            _finite(value, name)
    return result


def _pose(api, base, yaw_offset, pitch):
    return api.Transform(
        api.Location(x=base.location.x, y=base.location.y, z=base.location.z + 2.5),
        api.Rotation(pitch=pitch, yaw=base.rotation.yaw + yaw_offset, roll=0.0))


def _location_xyz(location):
    return (float(location.x), float(location.y), float(location.z))


def _travelled_meters(start, end):
    x0, y0, z0 = _location_xyz(start)
    x1, y1, z1 = _location_xyz(end)
    return math.sqrt((x1 - x0) ** 2 + (y1 - y0) ** 2 + (z1 - z0) ** 2)


def _velocity_meters_per_second(velocity):
    return math.sqrt(float(velocity.x) ** 2 + float(velocity.y) ** 2 + float(velocity.z) ** 2)


def _blueprint_attribute(blueprint, name, value, type_id):
    _require(blueprint.has_attribute(name), f"{type_id} missing attribute {name}")
    blueprint.set_attribute(name, value)


def _spawn_sensors(api, world, owned, streams, raw):
    spawn_points = world.get_map().get_spawn_points()
    _require(bool(spawn_points), "sensors require map spawn points and visible geometry")
    base = spawn_points[0]
    attributes = {
        "camera": {"image_size_x": str(WIDTH), "image_size_y": str(HEIGHT), "fov": "90",
                   "sensor_tick": "0", "enable_postprocess_effects": "false"},
        "lidar": {"channels": "16", "range": "50", "points_per_second": "32000",
                  "rotation_frequency": "20", "upper_fov": "10", "lower_fov": "-30",
                  "sensor_tick": "0", "noise_stddev": "0", "dropoff_general_rate": "0"},
    }
    blueprints = world.get_blueprint_library()
    for name, type_id, pitch in (("camera", "sensor.camera.rgb", -15.0),
                                ("lidar", "sensor.lidar.ray_cast", 0.0)):
        blueprint = blueprints.find(type_id)
        for key, value in attributes[name].items():
            _blueprint_attribute(blueprint, key, value, type_id)
        actor = world.spawn_actor(blueprint, _pose(api, base, 0, pitch))
        _require(actor is not None, f"could not spawn {type_id}")
        owned.append(actor)
        raw["owned_actor_ids"].append(actor.id)
        stream = AlignedQueue()
        streams[name] = stream
        actor.listen(stream)
    raw["sensor_attributes"] = attributes
    raw["camera_motion"] = "owned sensor yaw sweep: 30*sin(measured_tick/10) degrees"
    return base


def _pick_vehicle_blueprint(blueprints):
    candidates = [blueprint for blueprint in blueprints.filter("vehicle.*")
                  if blueprint.id.startswith("vehicle.tesla.model3")]
    candidates = candidates or list(blueprints.filter("vehicle.*"))
    _require(bool(candidates), "map has no vehicle blueprints")
    return candidates[0]


def _pick_walker_blueprint(blueprints):
    candidates = [blueprint for blueprint in blueprints.filter("walker.pedestrian.*")
                  if blueprint.id.startswith("walker.pedestrian.0001")]
    candidates = candidates or list(blueprints.filter("walker.pedestrian.*"))
    _require(bool(candidates), "map has no walker blueprints")
    return candidates[0]


def _spawn_actors(api, client, world, owned, raw):
    blueprints = world.get_blueprint_library()
    spawn_points = world.get_map().get_spawn_points()
    _require(bool(spawn_points), "actors mode requires map vehicle spawn points")
    vehicle_point = spawn_points[0]
    vehicle_blueprint = _pick_vehicle_blueprint(blueprints)
    for attribute, value in (("role_name", "lavapipe_actor_gate"),):
        if vehicle_blueprint.has_attribute(attribute):
            vehicle_blueprint.set_attribute(attribute, value)
    vehicle = world.spawn_actor(
        vehicle_blueprint,
        api.Transform(vehicle_point.location, vehicle_point.rotation))
    _require(vehicle is not None, "could not spawn Traffic Manager vehicle")
    owned.append(vehicle)
    raw["owned_actor_ids"].append(vehicle.id)

    traffic_manager = client.get_trafficmanager()
    traffic_manager_port = traffic_manager.get_port()
    traffic_manager.set_synchronous_mode(True)
    traffic_manager.set_random_device_seed(1729)
    _require(vehicle.set_autopilot(True, traffic_manager_port) is not False,
             "Traffic Manager did not accept vehicle registration")

    origin = world.get_random_location_from_navigation()
    _require(origin is not None, "walker navigation mesh is unavailable")
    destination = None
    for _ in range(20):
        candidate = world.get_random_location_from_navigation()
        candidate_distance = math.dist(_location_xyz(candidate), _location_xyz(origin)) \
            if candidate is not None else -1.0
        if candidate_distance >= 10.0:
            destination = candidate
            break
    _require(destination is not None, "walker navigation has no destination at least 10m away")

    walker_blueprint = _pick_walker_blueprint(blueprints)
    if walker_blueprint.has_attribute("is_invincible"):
        walker_blueprint.set_attribute("is_invincible", "false")
    walker_spawn = api.Location(x=origin.x, y=origin.y, z=origin.z + 1.0)
    walker = world.spawn_actor(walker_blueprint, api.Transform(walker_spawn, api.Rotation()))
    _require(walker is not None, "could not spawn walker")
    owned.append(walker)
    raw["owned_actor_ids"].append(walker.id)

    controller_blueprint = blueprints.find("controller.ai.walker")
    controller = world.spawn_actor(
        controller_blueprint, api.Transform(api.Location(), api.Rotation()), attach_to=walker)
    _require(controller is not None, "could not spawn walker AI controller")
    owned.append(controller)
    raw["owned_actor_ids"].append(controller.id)
    # CARLA registers the attachment with the episode on the next tick.
    world.tick(10.0)
    controller.start()
    _require(controller.go_to_location(destination) is not False,
             "walker AI controller rejected navigation destination")
    _require(controller.set_max_speed(1.4) is not False,
             "walker AI controller rejected maximum speed")

    raw["actors"] = {
        "vehicle_type_id": vehicle_blueprint.id,
        "walker_type_id": walker_blueprint.id,
        "traffic_manager_port": traffic_manager_port,
        "traffic_manager_synchronous": True,
        "traffic_manager_seed": 1729,
        "walker_origin": _location_xyz(origin),
        "walker_destination": _location_xyz(destination),
    }
    return vehicle, walker, traffic_manager


def run_gate(*, mode, run_dir, provenance, host="127.0.0.1", port=2000, ticks=100,
             timeout=10.0, allow_world_mutation=False, api=None):
    """Validate rpc, sensors, or actors against an exclusive endpoint world.

    Injected APIs are test-only and always use separate harness-test scopes.
    """
    _require(mode in STAGES, "mode must be rpc, sensors or actors")
    run_dir, provenance = Path(run_dir).resolve(), Path(provenance).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    output = run_dir / "stage-report.json"
    _require(not output.exists() and not output.is_symlink(), "refusing to overwrite existing stage report")
    work = Path(tempfile.mkdtemp(prefix="runtime-checks-", dir=run_dir))
    work.chmod(0o755)
    injected = api is not None
    stage_id, scope = STAGES[mode]
    if injected:
        stage_id, scope = f"harness-test.{stage_id}", f"HARNESS TEST ONLY; {scope}"
    required = ["environment", "provenance", "permission", "handshake", "world", "sync-settings", "ticks"]
    if mode == "sensors":
        required += ["sensor-setup", "alignment", "camera", "lidar"]
    if mode == "actors":
        required += ["actor-setup", "vehicle-motion", "walker-motion"]
    required += ["cleanup"]
    checks = {name: "MISSING" for name in required}
    sources, owned, streams, controllers = {}, [], {}, []
    world = original = None
    traffic_manager = vehicle = walker = None
    settings_attempted = False
    provenance_snapshot = work / "provenance.json"
    provenance_sha256 = None
    raw = {"mode": mode, "test_only": injected, "errors": [], "ticks": [], "warmup": [],
           "samples": [], "owned_actor_ids": [], "cleanup": [],
           "endpoint": {"host": str(host), "port": str(port), "server_architecture": "unverified"},
           "provenance_policy": "caller-supplied sources; server binary/build binding unverified"}
    evidence = {"provenance-file": provenance, "evaluator": Path(__file__).resolve(),
                "stage-report-writer": Path(stage_report.__file__).resolve()}
    for filename in ("client-install.log", "wheel.sha256", "client-install.command.json"):
        path = run_dir / filename
        if path.exists() or path.is_symlink():
            evidence[f"client-artifact.{filename}"] = path
    command = [sys.executable, str(Path(__file__).resolve()), "--mode", mode, "--host", str(host),
               "--port", str(port), "--ticks", str(ticks), "--timeout", str(timeout),
               "--run-dir", str(run_dir), "--provenance", str(provenance)]
    if allow_world_mutation:
        command.append("--allow-world-mutation")
    active = "environment"
    try:
        _require(isinstance(host, str) and bool(host.strip()), "host must be nonempty")
        _require(type(port) is int and 1 <= port <= 65535, "port must be in 1..65535")
        _require(type(ticks) is int and (2 if mode == "sensors" else 1) <= ticks <= 10000,
                 "ticks must be in 1..10000 (sensors/actors need at least 2)")
        _require(type(timeout) in (int, float) and 0 < timeout <= 120, "timeout must be in (0, 120]")
        raw["environment"] = ({"injected": True, "native_validation": False}
                              if injected else runtime_environment())
        raw["requested_ticks"] = ticks
        raw["fixed_delta_seconds"] = STEP_SECONDS
        checks[active] = "PASS"
        active = "provenance"
        provenance_bytes = provenance.read_bytes()
        provenance_snapshot.write_bytes(provenance_bytes)
        provenance_sha256 = hashlib.sha256(provenance_bytes).hexdigest()
        evidence["provenance-file"] = provenance_snapshot
        raw["provenance"] = {"provided_path": str(provenance),
                             "snapshot_path": str(provenance_snapshot),
                             "sha256": provenance_sha256}
        sources = read_provenance(provenance_snapshot)
        checks[active] = "PASS"
        active = "permission"
        _require(allow_world_mutation is True,
                 "--allow-world-mutation is required; use an idle world with no other tick owner")
        checks[active] = "PASS"
        active = "handshake"
        api = api if injected else importlib.import_module("carla")
        if injected:
            raw["client_libcarla"] = {"injected": True, "native_binary_inspected": False}
        else:
            raw["client_libcarla"] = snapshot_client_binary(work, api)
            evidence["client-libcarla"] = Path(raw["client_libcarla"]["snapshot_path"])
        client = api.Client(host, port)
        client.set_timeout(timeout)
        raw["versions"] = validate_versions(client.get_client_version(), client.get_server_version())
        checks[active] = "PASS"
        active = "world"
        world = client.get_world()
        raw["map"] = world.get_map().name
        _require(isinstance(raw["map"], str) and raw["map"].strip(), "world map name is missing")
        raw["initial_snapshot"] = validate_snapshot(world.get_snapshot())
        checks[active] = "PASS"
        active = "sync-settings"
        original = world.get_settings()
        desired = world.get_settings()
        _require(original is not desired, "world settings must be independent snapshots")
        raw["original_settings"] = _settings_view(original)
        if desired.substepping:
            _require(desired.max_substep_delta_time * desired.max_substeps >= STEP_SECONDS,
                     "existing physics substeps cannot accommodate 0.05 seconds")
        desired.synchronous_mode = True
        desired.fixed_delta_seconds = STEP_SECONDS
        desired.no_rendering_mode = mode != "sensors"
        settings_attempted = True
        world.apply_settings(desired, timeout)
        _require(world.get_settings() == desired, "synchronous world settings were not applied")
        raw["applied_settings"] = _settings_view(desired)
        checks[active] = "PASS"
        if mode == "sensors":
            active = "sensor-setup"
            base = _spawn_sensors(api, world, owned, streams, raw)
            checks[active] = "PASS"
        if mode == "actors":
            active = "actor-setup"
            vehicle, walker, traffic_manager = _spawn_actors(api, client, world, owned, raw)
            controllers.append(owned[-1])
            checks[active] = "PASS"
        previous = validate_snapshot(world.get_snapshot())
        raw["tick_baseline"] = previous
        warmup = WARMUP_TICKS if mode == "sensors" else ACTOR_WARMUP_TICKS if mode == "actors" else 0
        rgb_hashes = set()
        vehicle_start = walker_start = None
        vehicle_max_speed = walker_max_speed = 0.0
        for index in range(warmup + ticks):
            active = "ticks"
            if index and index % 100 == 0:
                print(f"tick progress mode={mode} index={index}/{warmup + ticks} "
                      f"frame={previous['frame']}", flush=True)
            for stream in streams.values():
                stream.check_failure()
            if mode == "sensors" and index >= warmup:
                yaw = 30.0 * math.sin((index - warmup + 1) / 10.0)
                owned[0].set_transform(_pose(api, base, yaw, -15.0))
                owned[1].set_transform(_pose(api, base, yaw, 0.0))
            frame = world.tick(timeout)
            current = validate_snapshot(world.get_snapshot(), previous, frame)
            previous = current
            if index < warmup:
                raw["warmup"].append(current)
                continue
            raw["ticks"].append(current)
            if mode == "rpc":
                continue
            if mode == "actors":
                vehicle_location = vehicle.get_location()
                walker_location = walker.get_location()
                vehicle_speed = _velocity_meters_per_second(vehicle.get_velocity())
                walker_speed = _velocity_meters_per_second(walker.get_velocity())
                if vehicle_start is None:
                    vehicle_start, walker_start = vehicle_location, walker_location
                vehicle_max_speed = max(vehicle_max_speed, vehicle_speed)
                walker_max_speed = max(walker_max_speed, walker_speed)
                raw.setdefault("actor_samples", []).append({
                    "frame": frame,
                    "vehicle_location": _location_xyz(vehicle_location),
                    "vehicle_speed": vehicle_speed,
                    "walker_location": _location_xyz(walker_location),
                    "walker_speed": walker_speed,
                })
                continue
            active = "alignment"
            camera = streams["camera"].frame(frame, timeout)
            lidar = streams["lidar"].frame(frame, timeout)
            _packet_frame(camera, current)
            _packet_frame(lidar, current)
            camera_data, lidar_data = _payload(camera), _payload(lidar)
            if not raw["samples"]:
                for name, data, suffix in (("camera", camera_data, "bgra"), ("lidar", lidar_data, "xyzi")):
                    path = work / f"{name}-first.{suffix}"
                    path.write_bytes(data)
                    evidence[f"{name}-raw"] = path
                raw["raw_sample_frame"] = frame
            active = "camera"
            camera_metrics = validate_image(camera_data, camera.width, camera.height)
            rgb_hashes.add(camera_metrics["rgb_sha256"])
            active = "lidar"
            lidar_metrics = validate_lidar(lidar_data)
            raw["samples"].append({"frame": frame, "timestamp": current["elapsed_seconds"],
                                   "camera": camera_metrics, "lidar": lidar_metrics})
        checks["ticks"] = "PASS"
        if mode == "actors":
            vehicle_travelled = _travelled_meters(vehicle_start, vehicle.get_location())
            walker_travelled = _travelled_meters(walker_start, walker.get_location())
            raw["actors"].update({
                "vehicle_travelled_meters": vehicle_travelled,
                "vehicle_max_speed_mps": vehicle_max_speed,
                "walker_travelled_meters": walker_travelled,
                "walker_max_speed_mps": walker_max_speed,
                "minimum_required_travel_meters": MIN_TRAVELLED_METERS,
            })
            active = "vehicle-motion"
            _require(vehicle_travelled >= MIN_TRAVELLED_METERS,
                     "Traffic Manager vehicle did not move")
            _require(vehicle_max_speed > 0.0, "Traffic Manager vehicle velocity remained zero")
            checks[active] = "PASS"
            active = "walker-motion"
            _require(walker_travelled >= MIN_TRAVELLED_METERS,
                     "AI walker did not move")
            # CARLA's official walker smoke test treats displacement as the
            # motion signal; walker velocity reporting can remain zero on cooked
            # builds even while navigation moves the actor.
            checks[active] = "PASS"
        if mode == "sensors":
            active = "alignment"
            _require(not any(stream.overflow.is_set() for stream in streams.values()), "sensor queue overflow")
            checks["alignment"] = checks["lidar"] = "PASS"
            active = "camera"
            _require(len(rgb_hashes) >= 2, "RGB images do not change across measured frames")
            raw["distinct_rgb_frames"] = len(rgb_hashes)
            checks["camera"] = "PASS"
    except (Exception, KeyboardInterrupt) as error:
        checks[active] = "FAIL"
        raw["errors"].append(f"{active}: {type(error).__name__}: {error}")
    finally:
        cleanup_errors = []
        for controller in controllers:
            try:
                controller.stop()
            except Exception as error:
                cleanup_errors.append(f"stop controller {controller.id}: {error}")
        if traffic_manager is not None:
            try:
                traffic_manager.set_synchronous_mode(False)
            except Exception as error:
                cleanup_errors.append(f"stop Traffic Manager synchronous mode: {error}")
        if vehicle is not None:
            try:
                vehicle.set_autopilot(False, traffic_manager.get_port())
            except Exception as error:
                cleanup_errors.append(f"disable vehicle autopilot: {error}")
        for stream in streams.values():
            stream.closed.set()
        for actor in reversed(owned):
            try:
                if getattr(actor, "is_listening", False):
                    actor.stop()
            except Exception as error:
                cleanup_errors.append(f"stop actor {actor.id}: {error}")
            try:
                _require(actor.destroy() is True, f"destroy actor {actor.id} was not acknowledged")
                raw["cleanup"].append(f"destroyed owned actor {actor.id}")
            except Exception as error:
                cleanup_errors.append(f"destroy actor {actor.id}: {error}")
        if settings_attempted:
            try:
                world.apply_settings(original, timeout)
                _require(world.get_settings() == original, "restored world settings differ from original")
                raw["restored_settings"] = _settings_view(world.get_settings())
                raw["cleanup"].append("restored original world settings")
            except Exception as error:
                cleanup_errors.append(f"restore settings: {error}")
        raw["errors"].extend(cleanup_errors)
        checks["cleanup"] = "FAIL" if cleanup_errors else "PASS"
        raw["queue_stale_packets"] = {name: stream.stale for name, stream in streams.items()}
    if provenance_sha256 is not None:
        for label, path in (("source", provenance), ("snapshot", provenance_snapshot)):
            try:
                _require(_sha256_file(path) == provenance_sha256,
                         f"provenance {label} changed during gate")
            except (OSError, ValueError) as error:
                checks["provenance"] = "FAIL"
                raw["errors"].append(f"provenance {label} evidence: {error}")
    if "client-libcarla" in evidence:
        try:
            binary = raw["client_libcarla"]
            _require(_sha256_file(Path(binary["loaded_path"])) == binary["sha256"],
                     "client extension changed during gate")
        except (OSError, ValueError) as error:
            checks["handshake"] = "FAIL"
            raw["errors"].append(f"client extension evidence: {error}")
    raw["checks"] = checks
    result_path = work / "runtime-result.json"
    result_path.write_text(json.dumps(raw, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    evidence.update({name: result_path for name in required})
    return stage_report.write_report(
        output, stage_id=stage_id, scope=scope, exit_code=0 if all(value == "PASS" for value in checks.values()) else 1,
        required_checks=required, checks=checks, evidence=evidence, sources=sources, command=command,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=STAGES, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2000)
    parser.add_argument("--ticks", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--allow-world-mutation", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = run_gate(**vars(args))
    except (OSError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(f"{report['status']} {report['stage_id']} scope={report['scope']!r}")
    for error in report["errors"]:
        print(error, file=sys.stderr)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
