"""Dependency-free, bounded SVG charts for Mrx."""

from __future__ import annotations

import math
from dataclasses import dataclass
from html import escape
from typing import Any, Mapping, Sequence


class ChartError(ValueError):
    """Raised when chart input is invalid or exceeds configured limits."""


@dataclass(frozen=True)
class ChartResult:
    kind: str
    title: str
    svg: str
    points: int


def generate_chart(
    labels: Sequence[str],
    values: Sequence[float],
    *,
    kind: str = "bar",
    title: str = "Chart",
    width: int = 720,
    height: int = 420,
) -> ChartResult:
    """Generate a safe inline SVG bar, line, or pie chart."""
    if kind not in {"bar", "line", "pie"}:
        raise ChartError("kind must be bar, line, or pie")
    if not labels or len(labels) != len(values):
        raise ChartError("labels and values must be non-empty and have equal length")
    if len(labels) > 100:
        raise ChartError("charts are limited to 100 points")
    if not 320 <= width <= 1920 or not 240 <= height <= 1080:
        raise ChartError("chart dimensions are outside allowed bounds")

    clean_labels = [str(label)[:100] for label in labels]
    clean_values = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ChartError("chart values must be numbers")
        number = float(value)
        if not math.isfinite(number):
            raise ChartError("chart values must be finite")
        clean_values.append(number)
    if kind == "pie" and (any(value < 0 for value in clean_values) or sum(clean_values) <= 0):
        raise ChartError("pie values must be non-negative with a positive total")

    if kind == "pie":
        body = _pie(clean_labels, clean_values, width, height)
    else:
        body = _cartesian(clean_labels, clean_values, width, height, kind)
    safe_title = escape(str(title)[:200])
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" role="img" '
        f'aria-label="{safe_title}" viewBox="0 0 {width} {height}">'
        "<style>text{font-family:system-ui,sans-serif;fill:#dce6f2}"
        ".axis{stroke:#789;stroke-width:1}.grid{stroke:#345;stroke-width:1}"
        ".bar{fill:#48a9ff}.line{fill:none;stroke:#64d8a3;stroke-width:3}"
        '.point{fill:#e9b949}</style><rect width="100%" height="100%" fill="#101820"/>'
        f'<text x="{width / 2:.1f}" y="28" text-anchor="middle" font-size="20">'
        f"{safe_title}</text>{body}</svg>"
    )
    return ChartResult(kind, str(title)[:200], svg, len(clean_values))


def chart_tool(arguments: Mapping[str, Any]) -> ChartResult:
    """ToolRegistry adapter accepting JSON-compatible chart arguments."""
    labels = arguments.get("labels")
    values = arguments.get("values")
    if not isinstance(labels, Sequence) or isinstance(labels, (str, bytes)):
        raise ChartError("labels must be a sequence")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise ChartError("values must be a sequence")
    return generate_chart(
        labels,
        values,
        kind=arguments.get("kind", "bar"),
        title=arguments.get("title", "Chart"),
        width=arguments.get("width", 720),
        height=arguments.get("height", 420),
    )


def _cartesian(labels, values, width, height, kind):  # noqa: ANN001
    left, top, right, bottom = 55, 50, width - 20, height - 55
    plot_width, plot_height = right - left, bottom - top
    low = min(0.0, min(values))
    high = max(0.0, max(values))
    span = high - low or 1.0

    def y(value):
        return top + (high - value) / span * plot_height

    baseline = y(0)
    parts = [
        f'<line class="axis" x1="{left}" y1="{top}" x2="{left}" y2="{bottom}"/>',
        f'<line class="axis" x1="{left}" y1="{baseline:.2f}" x2="{right}" y2="{baseline:.2f}"/>',
    ]
    step = plot_width / len(values)
    points = []
    for index, (label, value) in enumerate(zip(labels, values, strict=True)):
        x = left + (index + 0.5) * step
        value_y = y(value)
        if kind == "bar":
            bar_y = min(value_y, baseline)
            bar_height = max(1.0, abs(baseline - value_y))
            parts.append(
                f'<rect class="bar" x="{x - step * 0.35:.2f}" y="{bar_y:.2f}" '
                f'width="{step * 0.7:.2f}" height="{bar_height:.2f}" rx="3"/>'
            )
        points.append(f"{x:.2f},{value_y:.2f}")
        parts.append(
            f'<text x="{x:.2f}" y="{bottom + 20}" text-anchor="middle" font-size="11">'
            f"{escape(label)}</text>"
        )
        parts.append(
            f'<text x="{x:.2f}" y="{value_y - 7:.2f}" text-anchor="middle" font-size="10">'
            f"{value:g}</text>"
        )
    if kind == "line":
        parts.append(f'<polyline class="line" points="{" ".join(points)}"/>')
        parts.extend(
            f'<circle class="point" cx="{point.split(",")[0]}" cy="{point.split(",")[1]}" r="4"/>'
            for point in points
        )
    return "".join(parts)


def _pie(labels, values, width, height):  # noqa: ANN001
    colors = ("#48a9ff", "#64d8a3", "#e9b949", "#ef6f6c", "#a78bfa", "#f472b6")
    total = sum(values)
    cx, cy = width * 0.38, height * 0.55
    radius = min(width, height) * 0.3
    angle = -math.pi / 2
    parts = []
    for index, (label, value) in enumerate(zip(labels, values, strict=True)):
        next_angle = angle + 2 * math.pi * value / total
        x1, y1 = cx + radius * math.cos(angle), cy + radius * math.sin(angle)
        x2, y2 = cx + radius * math.cos(next_angle), cy + radius * math.sin(next_angle)
        large = 1 if next_angle - angle > math.pi else 0
        color = colors[index % len(colors)]
        if value == total:
            parts.append(f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{radius:.2f}" fill="{color}"/>')
        elif value > 0:
            parts.append(
                f'<path d="M {cx:.2f} {cy:.2f} L {x1:.2f} {y1:.2f} '
                f'A {radius:.2f} {radius:.2f} 0 {large} 1 {x2:.2f} {y2:.2f} Z" '
                f'fill="{color}"/>'
            )
        legend_y = 60 + index * 22
        parts.append(
            f'<rect x="{width * 0.72:.2f}" y="{legend_y - 11}" '
            f'width="12" height="12" fill="{color}"/>'
        )
        parts.append(
            f'<text x="{width * 0.72 + 18:.2f}" y="{legend_y}" font-size="12">'
            f"{escape(label)}: {value:g}</text>"
        )
        angle = next_angle
    return "".join(parts)
