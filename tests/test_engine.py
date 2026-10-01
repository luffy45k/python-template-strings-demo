from __future__ import annotations

import sqlite3

import pytest

from template_strings_demo import (
    FailFirstAttempt,
    Interpolation,
    RenderError,
    TemplateEngine,
    build_template,
    parameterize_sql,
    render,
    render_html,
    template,
)


def test_compat_template_preserves_pep_750_shape():
    value = template("Hello {name!r}, score={score:.1f}", name="Ada", score=9.25)

    assert value.strings == ("Hello ", ", score=", "")
    assert value.values == ("Ada", 9.25)
    assert value.interpolations[0].expression == "name"
    assert value.interpolations[0].conversion == "r"
    assert value.interpolations[1].format_spec == ".1f"


def test_build_template_and_iteration_skip_empty_strings():
    interpolation = Interpolation(42, "answer")
    value = build_template(interpolation, "!")

    assert list(value) == [interpolation, "!"]
    assert value.strings == ("", "!")


def test_render_honors_conversion_and_formatting():
    value = template("{name!r} owes ${amount:,.2f}", name="Ada", amount=1234.5)
    assert render(value) == "'Ada' owes $1,234.50"


def test_html_escapes_only_dynamic_values():
    value = template("<strong>{unsafe}</strong>", unsafe='"<script>&')
    assert render_html(value) == "<strong>&quot;&lt;script&gt;&amp;</strong>"


def test_sql_values_never_enter_query_text():
    attack = "x'; DROP TABLE users; --"
    query, params = parameterize_sql(
        template("SELECT name FROM users WHERE name = {name}", name=attack)
    )
    assert query == "SELECT name FROM users WHERE name = ?"
    assert params == (attack,)
    assert attack not in query

    database = sqlite3.connect(":memory:")
    database.execute("CREATE TABLE users (name TEXT)")
    database.execute("INSERT INTO users VALUES (?)", (attack,))
    assert database.execute(query, params).fetchone() == (attack,)


def test_sql_rejects_formatting_and_invalid_placeholder():
    with pytest.raises(ValueError, match="format specs"):
        parameterize_sql(template("SELECT {value:.2f}", value=1.2))
    with pytest.raises(ValueError, match="non-empty"):
        parameterize_sql(template("SELECT {value}", value=1), "")


def test_transient_failure_is_retried_and_reported():
    value = template("Hello {name}", name="Ada")
    report = TemplateEngine(chaos=FailFirstAttempt("name"), max_retries=1).render(value)

    assert report.text == "Hello Ada"
    assert report.interpolations == 1
    assert report.retries == 1
    assert report.recovered == 1
    assert report.failures == 0


def test_strict_and_degraded_failure_modes():
    def broken(_):
        raise RuntimeError("broken processor")

    value = template("Hello {name}", name="Ada")
    with pytest.raises(RenderError, match="name"):
        TemplateEngine(broken, max_retries=2).render(value)

    report = TemplateEngine(broken, max_retries=1, strict=False).render(value)
    assert report.text == "Hello [render-error:name]"
    assert report.retries == 1
    assert report.failures == 1


def test_validation_errors_are_clear():
    with pytest.raises(TypeError, match="source"):
        template(42)  # type: ignore[arg-type]
    with pytest.raises(KeyError, match="missing"):
        template("Hello {name}")
    with pytest.raises(ValueError, match="anonymous"):
        template("Hello {}", name="Ada")
    with pytest.raises(TypeError, match="expects a Template"):
        TemplateEngine().render("not structured")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="non-negative"):
        TemplateEngine(max_retries=-1)


def test_nested_format_spec_is_resolved():
    value = template("Value: {number:.{precision}f}", number=3.14159, precision=3)
    assert value.interpolations[0].format_spec == ".3f"
    assert render(value) == "Value: 3.142"
