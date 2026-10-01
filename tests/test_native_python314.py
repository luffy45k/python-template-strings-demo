"""Native syntax coverage kept parseable by pre-3.14 test runners."""

from __future__ import annotations

import pytest

from template_strings_demo import NATIVE_T_STRINGS, Template, render, render_html


@pytest.mark.skipif(not NATIVE_T_STRINGS, reason="native t-strings require Python 3.14+")
def test_python314_native_t_string_metadata_and_renderers():
    name = "<Ada>"
    score = 9.25
    native = eval(
        't"User {name!s} scored {score:.1f}"',
        {},
        {"name": name, "score": score},
    )

    assert isinstance(native, Template)
    assert native.strings == ("User ", " scored ", "")
    assert native.values == (name, score)
    assert native.interpolations[0].expression == "name"
    assert native.interpolations[0].conversion == "s"
    assert native.interpolations[1].format_spec == ".1f"
    assert render(native) == "User <Ada> scored 9.2"
    assert render_html(native) == "User &lt;Ada&gt; scored 9.2"
