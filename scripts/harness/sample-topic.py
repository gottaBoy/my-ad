#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from collections.abc import Mapping, Sequence
from typing import Any


SEQUENCE_SAMPLE_FIELDS = {
    "objects",
    "points",
    "signals",
    "transforms",
}


def summarize_value(value: Any, field_name: str = "", depth: int = 0) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value

    if field_name == "data":
        try:
            return {"length": len(value)}
        except TypeError:
            return {"type": type(value).__name__}

    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"length": len(value)}

    if isinstance(value, Mapping):
        if depth >= 4:
            return {"type": type(value).__name__, "count": len(value)}
        return {
            str(name): summarize_value(
                item,
                field_name=str(name),
                depth=depth + 1,
            )
            for name, item in value.items()
        }

    if isinstance(value, Sequence):
        summary: dict[str, Any] = {"count": len(value)}
        if len(value) > 0 and field_name in SEQUENCE_SAMPLE_FIELDS:
            summary["first"] = summarize_value(value[0], depth=depth + 1)
        return summary

    get_fields = getattr(value, "get_fields_and_field_types", None)
    if callable(get_fields):
        if depth >= 4:
            return {"type": type(value).__name__}
        result: dict[str, Any] = {}
        for name in get_fields():
            result[name] = summarize_value(
                getattr(value, name),
                field_name=name,
                depth=depth + 1,
            )
        return result

    return str(value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Observe one ROS 2 topic and print a bounded message summary."
    )
    parser.add_argument("--topic", required=True)
    parser.add_argument("--duration", type=float, default=5.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.duration <= 0:
        raise SystemExit("--duration must be greater than zero")

    import rclpy
    from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
    from rosidl_runtime_py.utilities import get_message

    rclpy.init()
    node = rclpy.create_node("my_ad_topic_learning_probe")
    try:
        discovery_deadline = time.monotonic() + args.duration
        topic_types: list[str] = []
        publisher_info = []
        while time.monotonic() < discovery_deadline:
            topic_map = dict(node.get_topic_names_and_types())
            topic_types = topic_map.get(args.topic, [])
            publisher_info = node.get_publishers_info_by_topic(args.topic)
            if topic_types and publisher_info:
                break
            rclpy.spin_once(node, timeout_sec=0.1)

        if not topic_types:
            print("sample_status=TOPIC_NOT_DISCOVERED")
            return 0
        if len(topic_types) != 1:
            print(f"sample_status=AMBIGUOUS_TYPES types={','.join(topic_types)}")
            return 0

        message_type_name = topic_types[0]
        try:
            message_type = get_message(message_type_name)
        except (AttributeError, ImportError, ModuleNotFoundError, ValueError) as exc:
            print(f"sample_status=TYPE_SUPPORT_UNAVAILABLE type={message_type_name}")
            print(f"sample_error={type(exc).__name__}: {exc}")
            return 0

        durability = DurabilityPolicy.VOLATILE
        if publisher_info and all(
            info.qos_profile.durability == DurabilityPolicy.TRANSIENT_LOCAL
            for info in publisher_info
        ):
            durability = DurabilityPolicy.TRANSIENT_LOCAL

        qos = QoSProfile(
            depth=10,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=durability,
        )
        messages: list[Any] = []
        receipt_times: list[float] = []

        def receive(message: Any) -> None:
            receipt_times.append(time.monotonic())
            if not messages:
                messages.append(message)

        subscription = node.create_subscription(
            message_type,
            args.topic,
            receive,
            qos,
        )
        deadline = time.monotonic() + args.duration
        while time.monotonic() < deadline:
            rclpy.spin_once(
                node,
                timeout_sec=min(0.1, max(0.0, deadline - time.monotonic())),
            )

        node.destroy_subscription(subscription)
        print(f"publisher_count={len(publisher_info)}")
        if not receipt_times:
            print(f"sample_status=NO_MESSAGE type={message_type_name}")
            print(f"observation_window_sec={args.duration:.3f}")
            return 0

        print(f"sample_status=RECEIVED type={message_type_name}")
        print(f"message_count={len(receipt_times)}")
        print(f"observation_window_sec={args.duration:.3f}")
        if len(receipt_times) > 1 and receipt_times[-1] > receipt_times[0]:
            rate = (len(receipt_times) - 1) / (
                receipt_times[-1] - receipt_times[0]
            )
            print(f"observed_rate_hz={rate:.3f}")
        else:
            print("observed_rate_hz=unknown")

        summary = summarize_value(messages[0])
        print(
            "sample_summary="
            + json.dumps(summary, ensure_ascii=True, separators=(",", ":"))
        )
        return 0
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
