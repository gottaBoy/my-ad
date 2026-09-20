"""Harness-only tests: all CARLA clients, worlds and sensor data below are fakes."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/carla/check_carla_runtime.py"
spec = importlib.util.spec_from_file_location("check_carla_runtime", SCRIPT)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def image_bytes(frame, alpha=255):
    return bytes(value for index in range(gate.WIDTH * gate.HEIGHT)
                 for value in ((index + frame) % 256, index % 256, 120, alpha))


def snapshot(frame=100, elapsed=5.0, delta=0.05):
    return NS(frame=frame, timestamp=NS(frame=frame, elapsed_seconds=elapsed, delta_seconds=delta))


class FakeSettings(NS):
    pass


class FakeBlueprint:
    def __init__(self, type_id):
        self.id, self.attributes = type_id, {}

    def has_attribute(self, name):
        return True

    def set_attribute(self, name, value):
        self.attributes[name] = value


class FakeSensor:
    def __init__(self, world, blueprint, actor_id):
        self.world, self.blueprint, self.id = world, blueprint, actor_id
        self.callback = None
        self.is_listening = False
        self.destroyed = False
        self.transforms = []
        self.stop_error = False
        self.destroy_error = False

    def listen(self, callback):
        self.callback, self.is_listening = callback, True
        if self.world.listen_error:
            raise RuntimeError("fake listen failure")

    def set_transform(self, transform):
        self.transforms.append(transform)

    def stop(self):
        if self.stop_error:
            raise RuntimeError("fake stop failure")
        self.is_listening = False

    def destroy(self):
        if self.destroy_error:
            return False
        self.destroyed = True
        return True

    def emit(self):
        if not self.is_listening:
            return
        packet = NS(frame=self.world.frame, timestamp=self.world.elapsed)
        if self.blueprint.id == "sensor.camera.rgb":
            packet.width, packet.height = gate.WIDTH, gate.HEIGHT
            packet.raw_data = image_bytes(self.world.frame)
        else:
            packet.raw_data = struct.pack("<ffff", 1, 2, 3, 0.5)
        if self.world.packet_hook:
            packet = self.world.packet_hook(self, packet)
        if packet is not None:
            self.callback(packet)


class FakeWorld:
    def __init__(self):
        self.frame, self.elapsed = 100, 5.0
        self.settings = FakeSettings(
            synchronous_mode=False, no_rendering_mode=True, fixed_delta_seconds=None,
            substepping=True, max_substep_delta_time=0.01, max_substeps=10,
            max_culling_distance=0.0, deterministic_ragdolls=False,
            tile_stream_distance=3000.0, actor_active_distance=2000.0, spectator_as_ego=True)
        self.original = copy.deepcopy(self.settings)
        self.applied, self.sensors = [], []
        self.existing = NS(id=17, destroy=mock.Mock(), set_transform=mock.Mock())
        self.spawn_points = [NS(location=NS(x=1, y=2, z=3), rotation=NS(yaw=10))]
        self.spawn_error = False
        self.listen_error = False
        self.restore_error = False
        self.apply_error = False
        self.ignore_restore = False
        self.packet_hook = None
        self.tick_hook = None
        self.tick_calls = 0

    def get_map(self):
        return NS(name="HarnessOnlyMap", get_spawn_points=lambda: self.spawn_points)

    def get_snapshot(self):
        return snapshot(self.frame, self.elapsed)

    def get_settings(self):
        return copy.deepcopy(self.settings)

    def apply_settings(self, settings, timeout):
        self.applied.append(copy.deepcopy(settings))
        if len(self.applied) > 1 and self.restore_error:
            raise RuntimeError("fake restore failure")
        if not (len(self.applied) > 1 and self.ignore_restore):
            self.settings = copy.deepcopy(settings)
        if len(self.applied) == 1 and self.apply_error:
            raise RuntimeError("fake partial settings failure")

    def get_blueprint_library(self):
        return NS(find=FakeBlueprint)

    def spawn_actor(self, blueprint, transform):
        if self.spawn_error and self.sensors:
            raise RuntimeError("fake second spawn failure")
        actor = FakeSensor(self, blueprint, 1000 + len(self.sensors))
        self.sensors.append(actor)
        return actor

    def tick(self, timeout):
        self.tick_calls += 1
        self.frame += 1
        self.elapsed += gate.STEP_SECONDS
        if self.tick_hook:
            self.tick_hook(self)
        for actor in self.sensors:
            actor.emit()
        return self.frame


class FakeCarla:
    def __init__(self):
        self.world = FakeWorld()
        self.client_version = self.server_version = "harness-only-0.10.0"
        self.created_clients = []
        self.client = NS(
            set_timeout=mock.Mock(),
            get_client_version=lambda: self.client_version,
            get_server_version=lambda: self.server_version,
            get_world=lambda: self.world)

    def Client(self, host, port):
        self.created_clients.append((host, port))
        return self.client

    Location = staticmethod(lambda **values: NS(**values))
    Rotation = staticmethod(lambda **values: NS(**values))
    Transform = staticmethod(lambda location, rotation: NS(location=location, rotation=rotation))


class PureGateTest(unittest.TestCase):
    def test_versions_are_exact_and_nonempty(self):
        self.assertEqual("exact", gate.validate_versions("0.10.0", "0.10.0")["policy"])
        for client, server in (("0.10.0", "0.10.0-dirty"), ("", ""), (None, "0.10.0")):
            with self.subTest(client=client, server=server), self.assertRaises(ValueError):
                gate.validate_versions(client, server)

    def test_snapshot_requires_consecutive_frames_and_monotonic_fixed_time(self):
        previous = gate.validate_snapshot(snapshot())
        gate.validate_snapshot(snapshot(101, 5.05), previous, 101)
        for value in (snapshot(102, 5.05), snapshot(101, 5.0), snapshot(101, 5.1),
                      snapshot(101, float("nan")), snapshot(101, 5.05, 0.1)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                gate.validate_snapshot(value, previous, value.frame)
        with self.assertRaisesRegex(ValueError, "tick/snapshot"):
            gate.validate_snapshot(snapshot(), tick_frame=101)

    def test_image_checks_ignore_alpha_and_reject_uniform_color(self):
        with mock.patch.object(gate, "WIDTH", 2), mock.patch.object(gate, "HEIGHT", 2):
            a = gate.validate_image(image_bytes(1, 1), 2, 2)
            b = gate.validate_image(image_bytes(1, 255), 2, 2)
            self.assertEqual(a["rgb_sha256"], b["rgb_sha256"])
            for data in (b"", bytes([0, 0, 0, 255]) * 4, bytes([0, 0, 255, 255]) * 4):
                with self.subTest(data=data), self.assertRaises(ValueError):
                    gate.validate_image(data, 2, 2)
            with self.assertRaisesRegex(ValueError, "dimensions"):
                gate.validate_image(image_bytes(1), 1, 4)

    def test_lidar_requires_finite_nonempty_xyzi(self):
        self.assertEqual(1, gate.validate_lidar(struct.pack("<ffff", 1, 2, 3, 1))["points"])
        for data in (b"", b"bad", struct.pack("<ffff", 0, 0, 0, 1),
                     struct.pack("<ffff", float("nan"), 2, 3, 1),
                     struct.pack("<ffff", 1, 2, 3, float("inf"))):
            with self.subTest(data=data), self.assertRaises(ValueError):
                gate.validate_lidar(data)

    def test_queue_is_bounded_and_checks_exact_frames(self):
        stream = gate.AlignedQueue()
        stream(NS(frame=1))
        stream(NS(frame=2))
        self.assertEqual(2, stream.frame(2, 0.01).frame)
        self.assertEqual(1, stream.stale)
        with self.assertRaisesRegex(ValueError, "timeout"):
            stream.frame(3, 0.001)
        stream(NS(frame=4))
        with self.assertRaisesRegex(ValueError, "future"):
            stream.frame(3, 0.01)
        full = gate.AlignedQueue()
        for frame in range(gate.QUEUE_SIZE + 1):
            full(NS(frame=frame))
        self.assertEqual(gate.QUEUE_SIZE, full.queue.qsize())
        with self.assertRaisesRegex(ValueError, "overflow"):
            full.frame(0, 0.01)


class RuntimeGateHarnessTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run_dir = self.root / "run"
        self.provenance = self.root / "provenance.json"
        self.provenance.write_text(json.dumps({
            "schema_version": 1,
            "sources": {name: {"location": f"harness-only/{name}", "revision": "fake-test-revision"}
                        for name in ("carla", "ue")},
            "test_only": True, "additional": {"allowed": True},
        }), encoding="utf-8")
        self.api = FakeCarla()
        for name, value in (("WIDTH", 4), ("HEIGHT", 3)):
            patcher = mock.patch.object(gate, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_gate(self, mode="rpc", **overrides):
        values = dict(mode=mode, run_dir=self.run_dir, provenance=self.provenance,
                      ticks=3, timeout=0.01, allow_world_mutation=True, api=self.api)
        values.update(overrides)
        report = gate.run_gate(**values)
        raw_path = self.run_dir / report["evidence"]["cleanup"]["path"]
        self.raw = json.loads(raw_path.read_text())
        return report

    def assert_restored(self):
        self.assertEqual(self.api.world.original, self.api.world.settings)
        self.api.world.existing.destroy.assert_not_called()
        self.api.world.existing.set_transform.assert_not_called()
        self.assertTrue(all(sensor.destroyed for sensor in self.api.world.sensors))

    def test_rpc_fake_pass_is_test_only_and_restores_settings(self):
        report = self.run_gate()
        self.assertEqual("PASS", report["status"])
        self.assertEqual("harness-test.carla-runtime-rpc", report["stage_id"])
        self.assertTrue(report["scope"].startswith("HARNESS TEST ONLY"))
        self.assertTrue(self.raw["test_only"])
        self.assertEqual("unverified", self.raw["endpoint"]["server_architecture"])
        self.assertIn("binding unverified", self.raw["provenance_policy"])
        self.assertIn("server architecture/build", report["scope"])
        self.assertEqual(3, len(self.raw["ticks"]))
        self.assertEqual(3, self.api.world.tick_calls)
        self.assertEqual([], self.api.world.sensors)
        self.assert_restored()
        gate.stage_report.validate_report(self.run_dir / "stage-report.json",
                                         stage_id=report["stage_id"], scope=report["scope"])
        with self.assertRaises(gate.stage_report.ReportError):
            gate.stage_report.validate_report(self.run_dir / "stage-report.json",
                                             stage_id=gate.STAGES["rpc"][0], scope=gate.STAGES["rpc"][1])

    def test_sensors_align_realistic_fake_payloads_and_write_raw_evidence(self):
        report = self.run_gate("sensors")
        self.assertEqual("PASS", report["status"])
        self.assertEqual("harness-test.carla-runtime-sensors", report["stage_id"])
        self.assertEqual(gate.WARMUP_TICKS, len(self.raw["warmup"]))
        self.assertEqual(3, len(self.raw["samples"]))
        self.assertEqual(gate.WARMUP_TICKS + 3, self.api.world.tick_calls)
        self.assertGreater(self.raw["distinct_rgb_frames"], 1)
        self.assertFalse(self.raw["applied_settings"]["no_rendering_mode"])
        self.assertTrue(self.raw["restored_settings"]["no_rendering_mode"])
        for name in ("camera-raw", "lidar-raw"):
            path = self.run_dir / report["evidence"][name]["path"]
            self.assertGreater(path.stat().st_size, 0)
        self.assertEqual(gate.WIDTH * gate.HEIGHT * 4,
                         (self.run_dir / report["evidence"]["camera-raw"]["path"]).stat().st_size)
        self.assertTrue(all(len(sensor.transforms) == 3 for sensor in self.api.world.sensors))
        self.assert_restored()

    def test_permission_is_required_before_connecting_or_modifying_world(self):
        report = self.run_gate(allow_world_mutation=False)
        self.assertEqual("FAIL", report["status"])
        self.assertEqual("FAIL", report["checks"]["permission"])
        self.assertEqual([], self.api.created_clients)
        self.assertEqual([], self.api.world.applied)
        self.assert_restored()

    def test_version_mismatch_does_not_modify_settings(self):
        self.api.server_version += "-other"
        report = self.run_gate()
        self.assertEqual("FAIL", report["checks"]["handshake"])
        self.assertEqual([], self.api.world.applied)
        self.assertEqual(0, self.api.world.tick_calls)

    def test_frame_gap_fails_and_restores_world(self):
        self.api.world.tick_hook = lambda world: setattr(world, "frame", world.frame + 1)
        report = self.run_gate()
        self.assertEqual("FAIL", report["checks"]["ticks"])
        self.assertIn("consecutive", " ".join(self.raw["errors"]))
        self.assert_restored()

    def test_timeout_fails_and_restores_world(self):
        self.api.world.tick_hook = mock.Mock(side_effect=RuntimeError("fake RPC timeout"))
        report = self.run_gate()
        self.assertEqual("FAIL", report["status"])
        self.assertIn("timeout", " ".join(self.raw["errors"]))
        self.assert_restored()

    def test_partial_settings_application_is_restored(self):
        self.api.world.apply_error = True
        report = self.run_gate()
        self.assertEqual("FAIL", report["checks"]["sync-settings"])
        self.assertEqual(2, len(self.api.world.applied))
        self.assert_restored()

    def test_missing_spawn_points_is_failure_not_fabricated_scene(self):
        self.api.world.spawn_points = []
        report = self.run_gate("sensors")
        self.assertEqual("FAIL", report["checks"]["sensor-setup"])
        self.assertEqual([], self.api.world.sensors)
        self.assert_restored()

    def test_partial_actor_setup_and_listen_failures_cleanup_owned_actors(self):
        self.api.world.spawn_error = True
        report = self.run_gate("sensors")
        self.assertEqual("FAIL", report["status"])
        self.assertEqual(1, len(self.api.world.sensors))
        self.assert_restored()

    def test_listen_failure_still_destroys_created_actor(self):
        self.api.world.listen_error = True
        report = self.run_gate("sensors")
        self.assertEqual("FAIL", report["status"])
        self.assert_restored()

    def test_sensor_frame_mismatch_and_timeout_are_failures(self):
        def packet_hook(sensor, packet):
            if sensor.blueprint.id == "sensor.camera.rgb":
                packet.frame += 1
            return packet
        self.api.world.packet_hook = packet_hook
        report = self.run_gate("sensors")
        self.assertEqual("FAIL", report["checks"]["alignment"])
        self.assert_restored()

    def test_absent_sensor_stream_times_out_without_hanging(self):
        self.api.world.packet_hook = lambda sensor, packet: None
        report = self.run_gate("sensors")
        self.assertEqual("FAIL", report["checks"]["alignment"])
        self.assertIn("timeout", " ".join(self.raw["errors"]))
        self.assert_restored()

    def test_sensor_timestamp_must_match_world_snapshot(self):
        def packet_hook(sensor, packet):
            packet.timestamp += 1
            return packet
        self.api.world.packet_hook = packet_hook
        report = self.run_gate("sensors")
        self.assertEqual("FAIL", report["checks"]["alignment"])
        self.assert_restored()

    def test_constant_images_fail_even_when_alpha_changes(self):
        def packet_hook(sensor, packet):
            if sensor.blueprint.id == "sensor.camera.rgb":
                packet.raw_data = image_bytes(1, packet.frame % 256)
            return packet
        self.api.world.packet_hook = packet_hook
        report = self.run_gate("sensors")
        self.assertEqual("FAIL", report["checks"]["camera"])
        self.assertIn("do not change", " ".join(self.raw["errors"]))
        self.assert_restored()

    def test_empty_lidar_fails_and_keeps_raw_failure_evidence(self):
        def packet_hook(sensor, packet):
            if sensor.blueprint.id == "sensor.lidar.ray_cast":
                packet.raw_data = b""
            return packet
        self.api.world.packet_hook = packet_hook
        report = self.run_gate("sensors")
        self.assertEqual("FAIL", report["checks"]["lidar"])
        self.assertIn("lidar-raw", report["evidence"])
        self.assert_restored()

    def test_destroy_failure_invalidates_otherwise_passing_gate(self):
        def packet_hook(sensor, packet):
            sensor.destroy_error = True
            return packet
        self.api.world.packet_hook = packet_hook
        report = self.run_gate("sensors")
        self.assertEqual("FAIL", report["status"])
        self.assertEqual("FAIL", report["checks"]["cleanup"])
        self.assertEqual(self.api.world.original, self.api.world.settings)

    def test_stop_failure_still_attempts_destroy_and_restore(self):
        def packet_hook(sensor, packet):
            sensor.stop_error = True
            return packet
        self.api.world.packet_hook = packet_hook
        report = self.run_gate("sensors")
        self.assertEqual("FAIL", report["checks"]["cleanup"])
        self.assert_restored()

    def test_restore_failure_invalidates_otherwise_passing_rpc(self):
        self.api.world.restore_error = True
        report = self.run_gate()
        self.assertEqual("PASS", report["checks"]["ticks"])
        self.assertEqual("FAIL", report["status"])
        self.assertEqual("FAIL", report["checks"]["cleanup"])

    def test_ignored_restore_is_detected(self):
        self.api.world.ignore_restore = True
        report = self.run_gate()
        self.assertEqual("FAIL", report["checks"]["cleanup"])

    def test_keyboard_interrupt_restores_settings_and_writes_fail(self):
        self.api.world.tick_hook = mock.Mock(side_effect=KeyboardInterrupt())
        report = self.run_gate()
        self.assertEqual("FAIL", report["status"])
        self.assertIn("KeyboardInterrupt", " ".join(self.raw["errors"]))
        self.assert_restored()

    def test_provenance_is_snapshotted_and_hashed_whole(self):
        original_bytes = self.provenance.read_bytes()
        report = self.run_gate()
        snapshot_path = (self.run_dir / report["evidence"]["provenance-file"]["path"]).resolve()
        self.assertNotEqual(self.provenance, snapshot_path)
        self.assertEqual(original_bytes, snapshot_path.read_bytes())
        self.assertEqual(hashlib.sha256(original_bytes).hexdigest(),
                         report["evidence"]["provenance-file"]["sha256"])
        self.assertEqual(report["sources"], json.loads(snapshot_path.read_bytes())["sources"])
        self.assertEqual(str(self.provenance), self.raw["provenance"]["provided_path"])
        self.provenance.write_text(self.provenance.read_text() + "\n", encoding="utf-8")
        gate.stage_report.validate_report(self.run_dir / "stage-report.json",
                                         stage_id=report["stage_id"], scope=report["scope"])
        snapshot_path.write_bytes(original_bytes + b"\n")
        with self.assertRaisesRegex(gate.stage_report.ReportError, "SHA256 mismatch"):
            gate.stage_report.validate_report(self.run_dir / "stage-report.json",
                                             stage_id=report["stage_id"], scope=report["scope"])

    def test_mid_run_provenance_rewrite_fails_and_keeps_original_source_snapshot(self):
        original_bytes = self.provenance.read_bytes()
        def rewrite_provenance(world):
            changed = json.loads(original_bytes)
            changed["sources"]["ue"]["revision"] = "different-harness-test-revision"
            self.provenance.write_text(json.dumps(changed), encoding="utf-8")
        self.api.world.tick_hook = rewrite_provenance
        report = self.run_gate()
        self.assertEqual("PASS", report["checks"]["ticks"])
        self.assertEqual("FAIL", report["checks"]["provenance"])
        self.assertEqual("FAIL", report["status"])
        self.assertIn("provenance source changed during gate", " ".join(self.raw["errors"]))
        snapshot_path = self.run_dir / report["evidence"]["provenance-file"]["path"]
        self.assertEqual(original_bytes, snapshot_path.read_bytes())
        self.assertEqual(json.loads(original_bytes)["sources"], report["sources"])
        self.assert_restored()

    def test_mid_run_provenance_snapshot_rewrite_fails(self):
        def rewrite_snapshot(world):
            path = next(self.run_dir.glob("runtime-checks-*/provenance.json"))
            path.write_bytes(path.read_bytes() + b"\n")
        self.api.world.tick_hook = rewrite_snapshot
        report = self.run_gate()
        self.assertEqual("FAIL", report["status"])
        self.assertEqual("FAIL", report["checks"]["provenance"])
        self.assertIn("provenance snapshot changed during gate", " ".join(self.raw["errors"]))
        self.assert_restored()

    def test_mid_run_provenance_source_deletion_fails(self):
        self.api.world.tick_hook = lambda world: self.provenance.unlink(missing_ok=True)
        report = self.run_gate()
        self.assertEqual("FAIL", report["status"])
        self.assertEqual("FAIL", report["checks"]["provenance"])
        self.assertIn("provenance source evidence", " ".join(self.raw["errors"]))
        self.assertTrue((self.run_dir / report["evidence"]["provenance-file"]["path"]).is_file())
        self.assert_restored()

    def test_invalid_provenance_blocks_client_and_still_writes_report(self):
        self.provenance.write_text('{"schema_version":true,"sources":{}}', encoding="utf-8")
        report = self.run_gate()
        self.assertEqual("FAIL", report["checks"]["provenance"])
        self.assertEqual([], self.api.created_clients)
        self.assertTrue((self.run_dir / "stage-report.json").is_file())

    def test_real_path_rejects_non_docker_or_non_arm_without_importing_carla(self):
        with mock.patch.object(gate, "runtime_environment", side_effect=ValueError("production CLI requires Docker and ARM64")):
            with mock.patch.object(gate.importlib, "import_module") as importer:
                report = self.run_gate(api=None)
        self.assertEqual("carla-runtime-rpc", report["stage_id"])
        self.assertEqual("FAIL", report["checks"]["environment"])
        importer.assert_not_called()

    def extension_fixture(self):
        directory = self.root / "fake-wheel-install" / "carla"
        directory.mkdir(parents=True)
        initializer = directory / "__init__.py"
        initializer.write_text("# harness-only package fixture\n", encoding="utf-8")
        extension = directory / "libcarla.cpython-310-aarch64-linux-gnu.so"
        extension.write_bytes(b"harness-only binary fixture; never imported or executed\n")
        return initializer, extension

    def test_extension_snapshot_records_binary_not_initializer_and_survives_install_removal(self):
        initializer, extension = self.extension_fixture()
        work = self.root / "evidence"
        work.mkdir()
        original = extension.read_bytes()
        with mock.patch.object(gate.importlib, "import_module", side_effect=[
            NS(__file__=str(initializer), __name__="carla"),
            NS(__file__=str(extension), __name__="carla.libcarla"),
        ]) as importer:
            record = gate.snapshot_client_binary(work)
        self.assertEqual([mock.call("carla"), mock.call("carla.libcarla")], importer.call_args_list)
        self.assertEqual("carla.libcarla", record["module"])
        self.assertEqual(str(extension), record["loaded_path"])
        self.assertNotEqual(str(initializer), record["loaded_path"])
        self.assertEqual(hashlib.sha256(original).hexdigest(), record["sha256"])
        self.assertIn("server build unverified", record["scope"])
        extension.unlink()
        self.assertEqual(original, Path(record["snapshot_path"]).read_bytes())

    def test_top_level_native_extension_never_attempts_package_fallback(self):
        _, extension = self.extension_fixture()
        top_level = self.root / "carla.cpython-310-aarch64-linux-gnu.so"
        top_level.write_bytes(extension.read_bytes())
        module = NS(__file__=str(top_level), __name__="carla")
        work = self.root / "evidence"
        work.mkdir()
        with mock.patch.object(gate.importlib, "import_module", return_value=module) as importer:
            record = gate.snapshot_client_binary(work)
        importer.assert_called_once_with("carla")
        self.assertEqual("carla", record["module"])
        self.assertEqual(str(top_level), record["loaded_path"])
        self.assertEqual(top_level.read_bytes(), Path(record["snapshot_path"]).read_bytes())
        with mock.patch.object(gate.importlib, "import_module", side_effect=AssertionError("unexpected import")):
            loaded = gate.snapshot_client_binary(work, module)
        self.assertEqual(record, loaded)

    def test_package_fallback_rejects_missing_or_non_native_submodule(self):
        initializer, _ = self.extension_fixture()
        package = NS(__file__=str(initializer), __name__="carla")
        work = self.root / "evidence"
        work.mkdir()
        for result in (
            ModuleNotFoundError("no carla.libcarla"),
            NS(__file__=str(initializer), __name__="carla.libcarla"),
            NS(__file__=None, __name__="carla.libcarla"),
        ):
            with self.subTest(result=result):
                with mock.patch.object(gate.importlib, "import_module", side_effect=[package, result]):
                    with self.assertRaises((ModuleNotFoundError, ValueError)):
                        gate.snapshot_client_binary(work)
        self.assertEqual([], list(work.iterdir()))

    def test_non_package_python_module_is_not_used_or_silently_fallen_back(self):
        source = self.root / "carla.py"
        source.write_text("# not a native extension or package\n", encoding="utf-8")
        work = self.root / "evidence"
        work.mkdir()
        with mock.patch.object(gate.importlib, "import_module",
                               return_value=NS(__file__=str(source), __name__="carla")) as importer:
            with self.assertRaises(ValueError):
                gate.snapshot_client_binary(work)
        importer.assert_called_once_with("carla")

    def test_extension_snapshot_rejects_missing_empty_or_python_module_file(self):
        initializer, extension = self.extension_fixture()
        empty = self.root / "empty.so"
        empty.write_bytes(b"")
        work = self.root / "evidence"
        work.mkdir()
        for filename in (None, str(initializer), str(self.root / "missing.so"), str(empty)):
            with self.subTest(filename=filename):
                with mock.patch.object(gate.importlib, "import_module", return_value=NS(__file__=filename)):
                    with self.assertRaises(ValueError):
                        gate.snapshot_client_binary(work)
        self.assertTrue(extension.is_file())

    def test_injected_gate_never_imports_or_requires_native_extension(self):
        with mock.patch.object(gate.importlib, "import_module", side_effect=AssertionError("unexpected import")):
            report = self.run_gate()
        self.assertEqual("PASS", report["status"])
        self.assertTrue(report["stage_id"].startswith("harness-test."))
        self.assertNotIn("client-libcarla", report["evidence"])
        self.assertEqual({"injected": True, "native_binary_inspected": False}, self.raw["client_libcarla"])

    def test_production_path_records_binary_even_when_no_endpoint_rpc_runs(self):
        initializer, extension = self.extension_fixture()
        package = NS(__file__=str(initializer), __name__="carla",
                     Client=mock.Mock(side_effect=RuntimeError("harness test stops before any RPC")))
        with mock.patch.object(gate, "runtime_environment", return_value={
            "docker": True, "machine": "aarch64", "injected": False,
        }), mock.patch.object(gate.importlib, "import_module",
                              side_effect=[package, NS(__file__=str(extension), __name__="carla.libcarla")]):
            report = self.run_gate(api=None)
        self.assertEqual("FAIL", report["status"])
        self.assertEqual("FAIL", report["checks"]["handshake"])
        self.assertEqual(str(extension), self.raw["client_libcarla"]["loaded_path"])
        self.assertEqual(self.raw["client_libcarla"]["sha256"],
                         report["evidence"]["client-libcarla"]["sha256"])
        snapshot_path = (self.run_dir / report["evidence"]["client-libcarla"]["path"]).resolve()
        self.assertNotEqual(extension, snapshot_path)
        self.assertEqual(extension.read_bytes(), snapshot_path.read_bytes())
        self.assertEqual([], self.api.created_clients)
        self.assertEqual([], self.api.world.applied)

    def test_client_extension_changes_are_detected_before_finalizing_failure_evidence(self):
        initializer, extension = self.extension_fixture()
        def stop_before_rpc(host, port):
            extension.write_bytes(b"changed fixture; no RPC performed\n")
            raise RuntimeError("harness test stops before any RPC")
        with mock.patch.object(gate, "runtime_environment", return_value={
            "docker": True, "machine": "aarch64", "injected": False,
        }), mock.patch.object(gate.importlib, "import_module",
                              side_effect=[NS(__file__=str(initializer), __name__="carla", Client=stop_before_rpc),
                                           NS(__file__=str(extension), __name__="carla.libcarla")]):
            report = self.run_gate(api=None)
        self.assertEqual("FAIL", report["status"])
        self.assertIn("client extension changed during gate", " ".join(self.raw["errors"]))
        self.assertEqual([], self.api.created_clients)

    def test_finished_client_install_artifacts_are_hashed_but_active_log_is_excluded(self):
        self.run_dir.mkdir()
        for name in ("client-install.log", "wheel.sha256", "client-install.command.json"):
            (self.run_dir / name).write_text("harness-only completed artifact\n", encoding="utf-8")
        active_log = self.run_dir / "runtime.log"
        active_log.write_text("still open\n", encoding="utf-8")
        report = self.run_gate()
        for name in ("client-install.log", "wheel.sha256", "client-install.command.json"):
            self.assertIn(f"client-artifact.{name}", report["evidence"])
        self.assertNotIn("client-artifact.runtime.log", report["evidence"])
        active_log.write_text("appended after report\n", encoding="utf-8")
        gate.stage_report.validate_report(self.run_dir / "stage-report.json",
                                         stage_id=report["stage_id"], scope=report["scope"])
        (self.run_dir / "client-install.log").write_text("changed completed log\n", encoding="utf-8")
        with self.assertRaisesRegex(gate.stage_report.ReportError, "SHA256 mismatch"):
            gate.stage_report.validate_report(self.run_dir / "stage-report.json",
                                             stage_id=report["stage_id"], scope=report["scope"])

    def test_environment_check_requires_both_docker_and_arm64(self):
        for docker, machine, allowed in ((True, "aarch64", True), (False, "aarch64", False),
                                         (True, "x86_64", False)):
            with self.subTest(docker=docker, machine=machine):
                with mock.patch.object(gate.Path, "is_file", return_value=docker):
                    with mock.patch.object(gate.platform, "machine", return_value=machine):
                        if allowed:
                            self.assertTrue(gate.runtime_environment()["docker"])
                        else:
                            with self.assertRaises(ValueError):
                                gate.runtime_environment()

    def test_invalid_timeouts_write_fail_and_never_touch_world(self):
        report = self.run_gate(timeout=float("nan"))
        self.assertEqual("FAIL", report["status"])
        self.assertEqual([], self.api.created_clients)
        self.assertTrue((self.run_dir / "stage-report.json").is_file())

    def test_existing_report_is_not_overwritten(self):
        self.run_gate()
        original = (self.run_dir / "stage-report.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "overwrite"):
            self.run_gate()
        self.assertEqual(original, (self.run_dir / "stage-report.json").read_bytes())

    def test_cli_has_no_test_or_environment_bypass_flag(self):
        with mock.patch("sys.stderr"):
            with self.assertRaises(SystemExit) as error:
                gate.main(["--mode", "rpc", "--run-dir", str(self.run_dir),
                           "--provenance", str(self.provenance), "--skip-environment-check"])
        self.assertEqual(2, error.exception.code)
        with mock.patch.object(gate, "run_gate", return_value={
            "status": "FAIL", "stage_id": "carla-runtime-rpc", "scope": "test", "errors": [],
        }) as runner, mock.patch("sys.stdout"):
            self.assertEqual(1, gate.main(["--mode", "rpc", "--run-dir", str(self.run_dir),
                                          "--provenance", str(self.provenance)]))
        self.assertNotIn("api", runner.call_args.kwargs)
        self.assertFalse(runner.call_args.kwargs["allow_world_mutation"])
        self.assertEqual(100, runner.call_args.kwargs["ticks"])


if __name__ == "__main__":
    unittest.main()
