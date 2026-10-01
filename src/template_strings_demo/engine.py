"""Rendering, escaping, SQL parameterization, and recovery utilities."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Callable, Optional, Tuple

from .model import Interpolation, Template

InterpolationProcessor = Callable[[Interpolation], str]
ChaosHook = Callable[[Interpolation, int], None]


class RenderError(RuntimeError):
    """Raised when an interpolation cannot be rendered in strict mode."""


@dataclass(frozen=True)
class RenderReport:
    """Rendered output and observability counters for one render operation."""

    text: str
    interpolations: int
    retries: int
    recovered: int
    failures: int


def apply_conversion(value: object, conversion: Optional[str]) -> object:
    """Apply the standard f-string conversion flags recorded by PEP 750."""
    if conversion is None:
        return value
    if conversion == "s":
        return str(value)
    if conversion == "r":
        return repr(value)
    if conversion == "a":
        return ascii(value)
    raise ValueError(f"unsupported conversion: {conversion!r}")


def plain(interpolation: Interpolation) -> str:
    """Render an interpolation with f-string-equivalent semantics."""
    value = apply_conversion(interpolation.value, interpolation.conversion)
    return format(value, interpolation.format_spec)


def html(interpolation: Interpolation) -> str:
    """Render and HTML-escape an interpolated value.

    Literal template fragments remain trusted markup; only dynamic values are escaped.
    """
    return escape(plain(interpolation), quote=True)


class TemplateEngine:
    """Render templates with bounded retry and explicit failure behavior."""

    def __init__(
        self,
        processor: InterpolationProcessor = plain,
        *,
        max_retries: int = 1,
        strict: bool = True,
        placeholder: str = "[render-error:{expression}]",
        chaos: Optional[ChaosHook] = None,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        self.processor = processor
        self.max_retries = max_retries
        self.strict = strict
        self.placeholder = placeholder
        self.chaos = chaos

    def render(self, value: Template) -> RenderReport:
        """Render *value* and return output plus recovery metrics."""
        if not isinstance(value, Template):
            raise TypeError("TemplateEngine.render() expects a Template (or native t-string)")

        output: list[str] = []
        retries = recovered = failures = count = 0
        for item in value:
            if isinstance(item, str):
                output.append(item)
                continue

            count += 1
            last_error: Optional[Exception] = None
            for attempt in range(self.max_retries + 1):
                try:
                    if self.chaos is not None:
                        self.chaos(item, attempt)
                    output.append(self.processor(item))
                    if attempt:
                        retries += attempt
                        recovered += 1
                    break
                except Exception as exc:  # processors are an extension boundary
                    last_error = exc
                    if attempt < self.max_retries:
                        continue
                    retries += attempt
                    failures += 1
                    if self.strict:
                        expression = item.expression or "<unknown>"
                        raise RenderError(
                            f"failed to render {expression!r} after {attempt + 1} attempt(s)"
                        ) from exc
                    output.append(self.placeholder.format(expression=item.expression or "unknown"))
            else:  # pragma: no cover - loop always exits by break or raise
                assert last_error is not None

        return RenderReport("".join(output), count, retries, recovered, failures)


class FailFirstAttempt:
    """Deterministic chaos hook that fails the first attempt of selected fields."""

    def __init__(self, *expressions: str) -> None:
        self.expressions = frozenset(expressions)

    def __call__(self, interpolation: Interpolation, attempt: int) -> None:
        if attempt == 0 and (not self.expressions or interpolation.expression in self.expressions):
            raise RuntimeError("injected transient rendering failure")


def render(value: Template) -> str:
    """Convenience function for ordinary rendering."""
    return TemplateEngine().render(value).text


def render_html(value: Template) -> str:
    """Convenience function for safe HTML interpolation."""
    return TemplateEngine(html).render(value).text


def parameterize_sql(value: Template, placeholder: str = "?") -> Tuple[str, Tuple[object, ...]]:
    """Turn a template into parameterized SQL without joining values into SQL text."""
    if not isinstance(value, Template):
        raise TypeError("parameterize_sql() expects a Template (or native t-string)")
    if not isinstance(placeholder, str) or not placeholder:
        raise ValueError("placeholder must be a non-empty string")

    sql: list[str] = []
    parameters: list[object] = []
    for item in value:
        if isinstance(item, str):
            sql.append(item)
        else:
            if item.conversion is not None or item.format_spec:
                raise ValueError("SQL interpolations cannot use conversions or format specs")
            sql.append(placeholder)
            parameters.append(item.value)
    return "".join(sql), tuple(parameters)
