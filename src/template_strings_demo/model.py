"""PEP 750 data model with a small compatibility layer for Python < 3.14."""

from __future__ import annotations

from dataclasses import dataclass
from string import Formatter
from typing import Any, Iterator, Mapping, Optional, Tuple, Union

try:  # Python 3.14+
    from string.templatelib import Interpolation, Template

    NATIVE_T_STRINGS = True
except ImportError:  # pragma: no cover - the native path runs on Python 3.14+
    NATIVE_T_STRINGS = False

    @dataclass(frozen=True)
    class Interpolation:  # type: ignore[no-redef]
        """Backport-compatible representation of a PEP 750 interpolation."""

        value: object
        expression: str = ""
        conversion: Optional[str] = None
        format_spec: str = ""

        def __post_init__(self) -> None:
            if self.conversion not in (None, "a", "r", "s"):
                raise ValueError("conversion must be one of None, 'a', 'r', or 's'")

    class Template:  # type: ignore[no-redef]
        """Minimal immutable implementation of ``string.templatelib.Template``."""

        __slots__ = ("_interpolations", "_strings")

        def __init__(self, *parts: Union[str, Interpolation]) -> None:
            strings = [""]
            interpolations = []
            for part in parts:
                if isinstance(part, str):
                    strings[-1] += part
                elif isinstance(part, Interpolation):
                    interpolations.append(part)
                    strings.append("")
                else:
                    raise TypeError("Template parts must be str or Interpolation instances")
            object.__setattr__(self, "_strings", tuple(strings))
            object.__setattr__(self, "_interpolations", tuple(interpolations))

        @property
        def strings(self) -> Tuple[str, ...]:
            return self._strings

        @property
        def interpolations(self) -> Tuple[Interpolation, ...]:
            return self._interpolations

        @property
        def values(self) -> Tuple[object, ...]:
            return tuple(item.value for item in self._interpolations)

        def __iter__(self) -> Iterator[Union[str, Interpolation]]:
            for index, interpolation in enumerate(self._interpolations):
                if self._strings[index]:
                    yield self._strings[index]
                yield interpolation
            if self._strings[-1]:
                yield self._strings[-1]

        def __repr__(self) -> str:
            return f"Template(strings={self.strings!r}, interpolations={self.interpolations!r})"


TemplatePart = Union[str, Interpolation]


def build_template(*parts: TemplatePart) -> Template:
    """Build a template programmatically on every supported Python version."""
    return Template(*parts)


def template(source: str, /, **values: Any) -> Template:
    """Create a template from a format-style string and named values.

    This helper lets Python 3.10–3.13 run the same processors as Python 3.14.
    It intentionally accepts named fields only; Python expressions still require
    native ``t\"...\"`` syntax on Python 3.14.
    """
    if not isinstance(source, str):
        raise TypeError("source must be a string")

    formatter = Formatter()
    parts: list[TemplatePart] = []
    for literal, field_name, format_spec, conversion in formatter.parse(source):
        if literal:
            parts.append(literal)
        if field_name is None:
            continue
        if not field_name:
            raise ValueError("anonymous fields are not supported; use a named field")
        try:
            value, _ = formatter.get_field(field_name, (), values)
            # Nested fields in a format spec are evaluated eagerly by native t-strings.
            if "{" in format_spec:
                resolved_spec = format_spec.format_map(_StrictMapping(values))
            else:
                resolved_spec = format_spec
        except (KeyError, AttributeError, IndexError) as exc:
            raise KeyError(f"missing value for template field {field_name!r}") from exc
        parts.append(Interpolation(value, field_name, conversion, resolved_spec))
    return build_template(*parts)


class _StrictMapping(dict):
    def __init__(self, values: Mapping[str, Any]) -> None:
        super().__init__(values)

    def __missing__(self, key: str) -> Any:
        raise KeyError(key)
