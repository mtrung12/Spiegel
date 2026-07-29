"""The report outline is fixed: same title, same five sections, every run."""
import re

from app.services.report_agent import (
    PLAN_USER_PROMPT_TEMPLATE,
    REPORT_SECTION_BRIEFS,
    REPORT_SECTION_IDS,
    SECTION_SYSTEM_PROMPT_TEMPLATE,
    _fixed_sections,
)
from app.utils.locale import set_locale


def _placeholders(template: str) -> set:
    return set(re.findall(r"(?<!\{)\{(\w+)\}(?!\})", template))


def test_every_section_has_a_brief():
    assert set(REPORT_SECTION_BRIEFS) == set(REPORT_SECTION_IDS)


def test_sections_are_titled_and_briefed_in_every_locale():
    for locale, first_title in (("en", "Who saw it"), ("sk", "Kto to videl")):
        set_locale(locale)
        sections = _fixed_sections()
        assert [s.title for s in sections][0] == first_title
        # A missing locale key falls back to the key path itself
        assert all(s.title and "report.sections" not in s.title for s in sections)
        assert all(s.brief for s in sections)
    set_locale("en")


def test_section_prompt_formats_with_the_brief():
    section = _fixed_sections()[0]
    filled = SECTION_SYSTEM_PROMPT_TEMPLATE.format(
        **{k: "x" for k in _placeholders(SECTION_SYSTEM_PROMPT_TEMPLATE) - {"section_title", "section_brief"}},
        section_title=section.title,
        section_brief=section.brief,
    )
    assert section.title in filled
    assert "reach rate" in filled


def test_plan_prompt_no_longer_asks_for_sections():
    filled = PLAN_USER_PROMPT_TEMPLATE.format(
        **{k: "x" for k in _placeholders(PLAN_USER_PROMPT_TEMPLATE)}
    )
    assert "section" not in filled.lower()
