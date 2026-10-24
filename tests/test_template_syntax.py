"""Jinja syntax tests for every project template."""
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateSyntaxError

ROOT = Path(__file__).resolve().parents[1]


def test_all_templates_parse_without_syntax_errors():
    templates = ROOT / "app" / "templates"
    environment = Environment(loader=FileSystemLoader(str(templates)), undefined=StrictUndefined)
    errors = []
    count = 0
    for path in sorted(templates.rglob("*.html")):
        relative = path.relative_to(templates).as_posix()
        count += 1
        try:
            environment.get_template(relative)
        except TemplateSyntaxError as exc:
            errors.append(f"{relative}:{exc.lineno}: {exc.message}")
    assert count >= 20, f"Expected a substantive template set, found {count}"
    assert not errors, "\n".join(errors)
