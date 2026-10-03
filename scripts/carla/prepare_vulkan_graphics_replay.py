"""Generate a fail-closed isolated graphics PSO input from a GB10 driver-entry."""

import argparse
import json
import math
from pathlib import Path
import re
import sys

from analyze_vulkan_driver_entries import analyze, fields, render_pass_snapshot


def require(condition, message):
    if not condition:
        raise ValueError(message)


def integer(value, label, limit=0xffffffff):
    require(re.fullmatch(r"[0-9]+", value or "") is not None, f"invalid {label}")
    result = int(value)
    require(result <= limit, f"out of range {label}")
    return result


def items(value, prefix, required):
    tokens = value.split()
    require(tokens, f"invalid {prefix} record")
    start = 1 if "=" not in tokens[0] else 0
    require(all(token.count("=") == 1 for token in tokens[start:]),
            f"invalid {prefix} fields")
    pairs = dict(token.split("=", 1) for token in tokens[start:])
    if start:
        pairs[prefix] = tokens[0]
    allowed = set(required) | ({prefix} if prefix in pairs else set())
    require(len(pairs) == len(tokens) and set(pairs) == allowed,
            f"incomplete {prefix} record")
    return pairs


def numbers(value, count, label):
    parts = (value or "").split(",")
    require(len(parts) == count, f"incomplete {label}")
    return [integer(part, label) for part in parts]


def numeric_float(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"invalid {label}") from error
    require(math.isfinite(number) and abs(number) <= 1e6, f"out of range {label}")
    return f"{number:.9g}f" if number != int(number) else f"{int(number)}.0f"


def initializer(type_name, fields):
    return f"static const {type_name} captured_{type_name} = {{" + ", ".join(
        f".{name} = {value}" for name, value in fields.items()) + "};"


def graphics_fields(path):
    values = fields(path)
    for key in ("vertex", "assembly", "viewport", "raster", "multisample",
                "depth", "blend", "dynamic", "tessellation"):
        raw = key + " flags"
        if raw in values:
            values[key] = "flags=" + values.pop(raw)
    return values


def prepare(run_dir):
    report = analyze(run_dir)
    require(report["status"] == "CAPTURED_DRIVER_ENTRY"
            and len(report["unreturned"]) == 1, "exactly one driver entry required")
    call = report["unreturned"][0]
    require(call["kind"] == "graphics" and len(call["stages"]) == 2
            and [stage["stage"] for stage in call["stages"]] == [1, 16],
            "only vertex/fragment graphics input supported")
    render = call["render_pass_snapshot"]
    require(render["status"] == "complete" and render["api"] == "vkCreateRenderPass2KHR"
            and render["subpasses"] == 1 and render["dependencies"] == 0,
            "a complete single-subpass render-pass2 input is required")
    marker = graphics_fields(run_dir / call["marker"])
    require(marker.get("submitted_cache", marker.get("cache")) == marker.get("cache"),
            "intervened cache input is not an unmodified graphics replay")
    require(marker.get("root_pnext") == "0" and marker.get("base_present") == "0"
            and marker.get("subpass") == "0" and marker.get("replay_ready") == "0",
            "unsupported graphics root state")
    require(marker.get("cache_data") not in (None, "not_captured"),
            "exact captured cache policy required")
    for index in range(2):
        stage = items(marker[f"stage{index}_flags"], f"stage{index}_flags",
                      ("pnext", "specialization"))
        require(stage["pnext"] == stage["specialization"] == "0"
                and stage[f"stage{index}_flags"] == "0",
                "unsupported shader stage extension or flags")
    render_path = run_dir / render["file"]
    render_fields = dict(line.split("=", 1) for line in render_path.read_text().splitlines()[1:-1])
    require(not any("_ext" in key or "_unavailable" in key for key in render_fields)
            and render_fields["correlated_masks"] == "0"
            and render_fields["flags"] == "0", "unsupported render-pass extension")
    require(render_fields["subpass0_flags"] == "0"
            and render_fields["subpass0_bind_point"] == "0"
            and render_fields["subpass0_inputs"] == "0"
            and render_fields["subpass0_resolves"] == "0"
            and render_fields["subpass0_depth"] == "0"
            and render_fields["subpass0_preserves"] == "0"
            and render_fields.get("subpass0_view_mask") == "0",
            "unsupported render-pass subpass state")
    attachments = render["attachments"]
    colors = integer(render_fields["subpass0_colors"], "color count", 8)
    require(attachments == colors and 1 <= colors <= 8,
            "only color-only render passes are supported")
    render_required = {
        "api", "handle", "flags", "attachments", "subpasses", "dependencies",
        "correlated_masks", "subpass0_flags", "subpass0_bind_point",
        "subpass0_inputs", "subpass0_colors", "subpass0_resolves",
        "subpass0_depth", "subpass0_preserves", "subpass0_view_mask",
        "capture_complete",
        *(f"attachment{index}" for index in range(attachments)),
        *(f"subpass0_color{index}" for index in range(colors)),
    }
    require(set(render_fields) == render_required, "unexpected render-pass input field")
    layout_keys = [token.partition("=")[0] for token in marker["layout_state"].split()[1:]]
    layout = items(marker["layout_state"], "sets", layout_keys)
    require({"descriptor_hash", "bindless", "push_constant_ranges"} <= set(layout),
            "incomplete layout")
    require(layout["bindless"] == "0" and layout["push_constant_ranges"] == "0",
            "bindless or push constants are not supported")
    set_count = integer(layout["sets"], "set count", 8)
    require(set_count > 0, "descriptor sets required")
    expected_layout = {"sets", "descriptor_hash", "bindless", "push_constant_ranges"}
    for index in range(set_count):
        count = integer(layout.get(f"set{index}_bindings"), "bindings count", 64)
        expected_layout.add(f"set{index}_bindings")
        expected_layout.update(f"set{index}_binding{binding}" for binding in range(count))
    require(set(layout) == expected_layout, "unexpected descriptor layout field")
    require(not any(key.endswith("_unavailable") or key.endswith("_unavailable=1")
                    for key in marker), "incomplete graphics fixed state")

    lines = [
        "/* Generated from a validated same-run driver-entry. Not a full UE replay. */",
        f'#define CAPTURE_VS_ENTRY "{call["stages"][0]["entry"]}"',
        f'#define CAPTURE_PS_ENTRY "{call["stages"][1]["entry"]}"',
        f"#define CAPTURE_SET_COUNT {set_count}",
        f"#define CAPTURE_GRAPHICS_FLAGS {integer(marker['flags'], 'pipeline flags')}",
    ]
    for index in range(set_count):
        count = integer(layout[f"set{index}_bindings"], "bindings count", 64)
        values = []
        for binding in range(count):
            number, kind, total, stages, immutable = numbers(
                layout[f"set{index}_binding{binding}"], 5, "descriptor binding")
            require(immutable == 0 and total > 0 and total <= 1024,
                    "unsupported immutable sampler or descriptor count")
            values.append(f"{{.binding={number},.descriptorType=(VkDescriptorType){kind},"
                          f".descriptorCount={total},.stageFlags={stages}}}")
        if count:
            lines.append(f"static const VkDescriptorSetLayoutBinding cap_set{index}[] = {{"
                         + ",".join(values) + "};")
    lines.append("static const uint32_t cap_binding_counts[] = {"
                 + ",".join(layout[f"set{index}_bindings"] for index in range(set_count)) + "};")
    lines.append("static const VkDescriptorSetLayoutBinding *cap_set_bindings[] = {"
                 + ",".join(f"cap_set{index}" if int(layout[f"set{index}_bindings"]) else "NULL"
                            for index in range(set_count)) + "};")

    desc = ["{" + ",".join(f".{key}={value}" for key, value in zip(
        ("flags", "format", "samples", "loadOp", "storeOp", "stencilLoadOp",
         "stencilStoreOp", "initialLayout", "finalLayout"),
        numbers(render_fields[f"attachment{index}"], 9, "render attachment")))
        + ",.sType=VK_STRUCTURE_TYPE_ATTACHMENT_DESCRIPTION_2}"
        for index in range(attachments)]
    lines.append("static const VkAttachmentDescription2 cap_attachments[] = {"
                 + ",".join(desc) + "};")
    refs = []
    for index in range(colors):
        attachment, image_layout, aspect = numbers(
            render_fields[f"subpass0_color{index}"], 3, "color attachment reference")
        require(attachment < attachments, "out of range color attachment")
        refs.append("{.sType=VK_STRUCTURE_TYPE_ATTACHMENT_REFERENCE_2,"
                    f".attachment={attachment},.layout={image_layout},.aspectMask={aspect}}}")
    lines.append("static const VkAttachmentReference2 cap_colors[] = {"
                 + ",".join(refs) + "};")
    lines.append("static const VkSubpassDescription2 cap_subpass = {"
                 ".sType=VK_STRUCTURE_TYPE_SUBPASS_DESCRIPTION_2,"
                 f".pipelineBindPoint=VK_PIPELINE_BIND_POINT_GRAPHICS,.colorAttachmentCount={colors},"
                 ".pColorAttachments=cap_colors};")
    lines.append("static const VkRenderPassCreateInfo2 cap_renderpass = {"
                 ".sType=VK_STRUCTURE_TYPE_RENDER_PASS_CREATE_INFO_2,"
                 f".attachmentCount={attachments},.pAttachments=cap_attachments,"
                 ".subpassCount=1,.pSubpasses=&cap_subpass};")

    vertex = items(marker["vertex"], "vertex",
                   ("flags", "pnext", "bindings", "attributes"))
    require(vertex["pnext"] == "0", "unsupported vertex extension")
    vertex_bindings = integer(vertex["bindings"], "vertex bindings", 32)
    attributes = integer(vertex["attributes"], "vertex attributes", 32)
    for category, count, names, type_name in (
        ("binding", vertex_bindings, ("binding", "stride", "inputRate"),
         "VkVertexInputBindingDescription"),
        ("attribute", attributes, ("location", "binding", "format", "offset"),
         "VkVertexInputAttributeDescription"),
    ):
        if count:
            values = []
            for index in range(count):
                data = numbers(marker[f"{category}{index}"], len(names), category)
                values.append("{" + ",".join(
                    f".{name}={item}" for name, item in zip(names, data)) + "}")
            lines.append(f"static const {type_name} cap_{category}s[] = {{"
                         + ",".join(values) + "};")
    lines.append(initializer("VkPipelineVertexInputStateCreateInfo", {
        "sType": "VK_STRUCTURE_TYPE_PIPELINE_VERTEX_INPUT_STATE_CREATE_INFO",
        "flags": vertex["flags"],
        "vertexBindingDescriptionCount": vertex["bindings"],
        "pVertexBindingDescriptions": "cap_bindings" if vertex_bindings else "NULL",
        "vertexAttributeDescriptionCount": vertex["attributes"],
        "pVertexAttributeDescriptions": "cap_attributes" if attributes else "NULL",
    }))

    def state(key, required):
        value = items(marker[key], key, required)
        require(value["pnext"] == "0", f"unsupported {key} extension")
        return value

    assembly = state("assembly", ("flags", "pnext", "topology", "restart"))
    lines.append(initializer("VkPipelineInputAssemblyStateCreateInfo", {
        "sType": "VK_STRUCTURE_TYPE_PIPELINE_INPUT_ASSEMBLY_STATE_CREATE_INFO",
        "flags": assembly["flags"], "topology": assembly["topology"],
        "primitiveRestartEnable": assembly["restart"],
    }))
    require("tessellation" not in marker, "tessellation state not supported")
    viewport = state("viewport", ("flags", "pnext", "viewports", "scissors",
                                  "static_viewports", "static_scissors"))
    require(viewport["static_viewports"] == viewport["static_scissors"] == "0",
            "static viewport/scissor not supported")
    lines.append(initializer("VkPipelineViewportStateCreateInfo", {
        "sType": "VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO",
        "flags": viewport["flags"], "viewportCount": viewport["viewports"],
        "scissorCount": viewport["scissors"],
    }))
    raster = state("raster", ("flags", "pnext", "depth_clamp", "discard", "polygon",
                              "cull", "front", "depth_bias", "bias_constant",
                              "bias_clamp", "bias_slope", "line_width"))
    lines.append(initializer("VkPipelineRasterizationStateCreateInfo", {
        "sType": "VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO",
        "flags": raster["flags"], "depthClampEnable": raster["depth_clamp"],
        "rasterizerDiscardEnable": raster["discard"], "polygonMode": raster["polygon"],
        "cullMode": raster["cull"], "frontFace": raster["front"],
        "depthBiasEnable": raster["depth_bias"],
        "depthBiasConstantFactor": numeric_float(raster["bias_constant"], "bias"),
        "depthBiasClamp": numeric_float(raster["bias_clamp"], "bias"),
        "depthBiasSlopeFactor": numeric_float(raster["bias_slope"], "bias"),
        "lineWidth": numeric_float(raster["line_width"], "line width"),
    }))
    multi = state("multisample", ("flags", "pnext", "samples", "sample_shading",
                                 "min_sample_shading", "sample_mask",
                                 "alpha_to_coverage", "alpha_to_one"))
    require(multi["sample_mask"] == "0", "sample mask not supported")
    lines.append(initializer("VkPipelineMultisampleStateCreateInfo", {
        "sType": "VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO",
        "flags": multi["flags"], "rasterizationSamples": multi["samples"],
        "sampleShadingEnable": multi["sample_shading"],
        "minSampleShading": numeric_float(multi["min_sample_shading"], "sample shading"),
        "alphaToCoverageEnable": multi["alpha_to_coverage"],
        "alphaToOneEnable": multi["alpha_to_one"],
    }))
    depth = state("depth", ("flags", "pnext", "test", "write", "compare", "bounds",
                             "stencil", "min_bounds", "max_bounds"))
    stencil = []
    for index in range(2):
        values = numbers(marker[f"stencil{index}"], 7, "stencil state")
        stencil.append("{" + ",".join(f".{key}={value}" for key, value in zip(
            ("failOp", "passOp", "depthFailOp", "compareOp",
             "compareMask", "writeMask", "reference"), values)) + "}")
    lines.append(initializer("VkPipelineDepthStencilStateCreateInfo", {
        "sType": "VK_STRUCTURE_TYPE_PIPELINE_DEPTH_STENCIL_STATE_CREATE_INFO",
        "flags": depth["flags"], "depthTestEnable": depth["test"],
        "depthWriteEnable": depth["write"], "depthCompareOp": depth["compare"],
        "depthBoundsTestEnable": depth["bounds"], "stencilTestEnable": depth["stencil"],
        "front": stencil[0], "back": stencil[1],
        "minDepthBounds": numeric_float(depth["min_bounds"], "depth bounds"),
        "maxDepthBounds": numeric_float(depth["max_bounds"], "depth bounds"),
    }))
    blend = state("blend", ("flags", "pnext", "logic", "op", "attachments", "constants"))
    blend_count = integer(blend["attachments"], "blend attachments", 8)
    require(blend_count == colors, "blend and render-pass attachment counts differ")
    attachments_data = []
    for index in range(blend_count):
        values = numbers(marker[f"blend_attachment{index}"], 8, "blend attachment")
        attachments_data.append("{" + ",".join(f".{key}={value}" for key, value in zip(
            ("blendEnable", "srcColorBlendFactor", "dstColorBlendFactor", "colorBlendOp",
             "srcAlphaBlendFactor", "dstAlphaBlendFactor", "alphaBlendOp", "colorWriteMask"),
            values)) + "}")
    lines.append("static const VkPipelineColorBlendAttachmentState cap_blend_attachments[] = {"
                 + ",".join(attachments_data) + "};")
    constants = [numeric_float(value, "blend constant")
                 for value in blend["constants"].split(",")]
    require(len(constants) == 4, "invalid blend constants")
    lines.append(initializer("VkPipelineColorBlendStateCreateInfo", {
        "sType": "VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO",
        "flags": blend["flags"], "logicOpEnable": blend["logic"],
        "logicOp": blend["op"], "attachmentCount": blend["attachments"],
        "pAttachments": "cap_blend_attachments",
        "blendConstants": "{" + ",".join(constants) + "}",
    }))
    dynamic = state("dynamic", ("flags", "pnext", "states"))
    count = integer(dynamic["states"], "dynamic states", 32)
    states = [integer(marker[f"dynamic_state{index}"], "dynamic state")
              for index in range(count)]
    require(0 in states and 1 in states, "dynamic viewport/scissor required")
    lines.append("static const VkDynamicState cap_dynamic_states[] = {"
                 + ",".join(f"(VkDynamicState){value}" for value in states) + "};")
    lines.append(initializer("VkPipelineDynamicStateCreateInfo", {
        "sType": "VK_STRUCTURE_TYPE_PIPELINE_DYNAMIC_STATE_CREATE_INFO",
        "flags": dynamic["flags"], "dynamicStateCount": str(count),
        "pDynamicStates": "cap_dynamic_states",
    }))
    return report, "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--header", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        report, header = prepare(args.run_dir)
        args.header.write_text(header)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(f"PASS selected={report['unreturned'][0]['marker']}")
        return 0
    except (OSError, ValueError, KeyError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
