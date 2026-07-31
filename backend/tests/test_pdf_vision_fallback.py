"""
The image-only PDF path.

The failure this guards against is silent: a deck exported as one image per
slide extracts to an empty string, and every stage after it - ontology,
personas, report - is generated from that empty string without an error.
"""

import re

import fitz
import pytest

from app.utils.file_parser import (
    ILLUSTRATION_VISION_PROMPT,
    MIN_IMAGE_SIDE_PT,
    PAGE_TEXT_MIN_CHARS,
    PAGE_VISION_PROMPT,
    FileParser,
    renumber_image_markers,
)


def _pdf_with_text(path, body):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), body, fontsize=11)
    doc.save(str(path))
    doc.close()


def _pdf_image_only(path, pages=2):
    """A PDF whose pages carry no text layer at all - a rendered deck."""
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page()
        page.draw_rect(fitz.Rect(50, 50, 300, 300), fill=(0.2, 0.4, 0.9))
    doc.save(str(path))
    doc.close()


class _FakeVision:
    """Stands in for LLMClient.for_vision(); records what it was sent."""

    model = 'fake-vlm'

    def __init__(self, reply='SLIDE TEXT: Launch the new blend.'):
        self.reply = reply
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return self.reply


@pytest.fixture
def fake_vision(monkeypatch):
    client = _FakeVision()
    monkeypatch.setattr(FileParser, '_vision_client', staticmethod(lambda: client))
    return client


def test_text_pdf_makes_no_vision_call(tmp_path, fake_vision):
    """The existing path must stay free: a real text layer never hits the LLM."""
    pdf = tmp_path / 'brief.pdf'
    body = 'Campaign brief for the new blend. ' * 6
    _pdf_with_text(pdf, body)

    text = FileParser.extract_text(str(pdf))

    assert 'Campaign brief' in text
    assert fake_vision.calls == []


def test_image_only_pdf_is_read_by_the_vision_model(tmp_path, fake_vision):
    pdf = tmp_path / 'deck.pdf'
    _pdf_image_only(pdf, pages=2)

    text = FileParser.extract_text(str(pdf))

    assert text.count('SLIDE TEXT: Launch the new blend.') == 2
    assert len(fake_vision.calls) == 2

    # The image has to arrive as an image content block, not as a stray string.
    content = fake_vision.calls[0][0]['content']
    kinds = [block['type'] for block in content]
    assert kinds == ['text', 'image_url']
    assert content[1]['image_url']['url'].startswith('data:image/png;base64,')


def test_image_only_pdf_without_vision_still_extracts_nothing(tmp_path, monkeypatch):
    """No vision configured: the old behaviour, so the upload guard can fire."""
    monkeypatch.setattr(FileParser, '_vision_client', staticmethod(lambda: None))
    pdf = tmp_path / 'deck.pdf'
    _pdf_image_only(pdf, pages=2)

    assert FileParser.extract_text(str(pdf)).strip() == ''


def test_one_failed_page_does_not_lose_the_others(tmp_path, monkeypatch):
    """A single bad page is worth losing; the other pages are not."""

    class _FlakyVision(_FakeVision):
        def chat(self, messages, **kwargs):
            self.calls.append(messages)
            if len(self.calls) == 1:
                raise RuntimeError('provider rejected the image')
            return self.reply

    client = _FlakyVision()
    monkeypatch.setattr(FileParser, '_vision_client', staticmethod(lambda: client))
    pdf = tmp_path / 'deck.pdf'
    _pdf_image_only(pdf, pages=3)

    text = FileParser.extract_text(str(pdf))

    assert len(client.calls) == 3
    assert text.count('SLIDE TEXT') == 2


def test_page_budget_caps_the_number_of_calls(tmp_path, fake_vision, monkeypatch):
    from app.config import Config

    monkeypatch.setattr(Config, 'VISION_PDF_MAX_PAGES', 2)
    pdf = tmp_path / 'deck.pdf'
    _pdf_image_only(pdf, pages=5)

    FileParser.extract_text(str(pdf))

    assert len(fake_vision.calls) == 2


def test_the_prompt_asks_for_inline_markers_with_captions():
    # The whole point of the marker is that an image is described where it sits,
    # with its own caption, rather than in a block at the end of the page.
    assert "([image1]:" in PAGE_VISION_PROMPT
    assert "Caption:" in PAGE_VISION_PROMPT
    assert "never invent one" in PAGE_VISION_PROMPT


def test_image_markers_are_renumbered_across_pages(tmp_path, monkeypatch):
    """Every page comes back numbered from 1; the document must not be."""

    class _TwoImagesPerPage(_FakeVision):
        def chat(self, messages, **kwargs):
            self.calls.append(messages)
            return ('Headline. ([image1]: a driver at dusk. Caption: "Go further") '
                    'Body copy. ([image2]: a bar chart of range in km)')

    client = _TwoImagesPerPage()
    monkeypatch.setattr(FileParser, '_vision_client', staticmethod(lambda: client))
    pdf = tmp_path / 'deck.pdf'
    _pdf_image_only(pdf, pages=3)

    text = FileParser.extract_text(str(pdf))

    assert [f'[image{n}]' for n in range(1, 7)] == re.findall(r'\[image\d+\]', text)
    # The caption stays attached to the image it belongs to.
    assert '([image5]: a driver at dusk. Caption: "Go further")' in text


@pytest.mark.parametrize("text,start,expected,used", [
    # Renumbered in order of first appearance, not by the number the model used.
    ("([image1]: a) then ([image2]: b)", 4, "([image4]: a) then ([image5]: b)", 2),
    # A repeat reference to one image keeps its new number.
    ("[image1] ... see [image1] again", 7, "[image7] ... see [image7] again", 1),
    # Spelling the model might use on the way in, one spelling on the way out.
    ("[Image 2] and [IMAGE3]", 1, "[image1] and [image2]", 2),
    # A page with no images must not consume a number.
    ("plain transcribed text", 3, "plain transcribed text", 0),
])
def test_renumber_image_markers(text, start, expected, used):
    assert renumber_image_markers(text, start) == (expected, used)


def test_a_page_of_boilerplate_counts_as_imageless(tmp_path, fake_vision):
    """A rendered slide still carries a page number; that is not a text layer."""
    pdf = tmp_path / 'deck.pdf'
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 800), '3')  # footer only
    doc.save(str(pdf))
    doc.close()

    FileParser.extract_text(str(pdf))

    assert len(fake_vision.calls) == 1, (
        f'a page holding under {PAGE_TEXT_MIN_CHARS} chars must go to the model'
    )


# ---- pages that have BOTH a text layer and artwork --------------------------
# The failure this guards against: a brief laid out with a real text layer that
# carries the campaign's creative as placed images. The text-layer path used to
# claim the page and the artwork was never looked at, so the headline burned
# into the hero banner never reached the graph.

def _pixel_image(width_px=400, height_px=200):
    """A tiny PNG, so the fixtures carry a real raster rather than a drawing."""
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, width_px, height_px))
    pix.set_rect(pix.irect, (200, 40, 60))
    return pix.tobytes("png")


def _pdf_text_plus_image(path, placed_rect, body=None):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), body or 'Campaign brief for the new blend. ' * 6, fontsize=11)
    page.insert_image(placed_rect, stream=_pixel_image())
    doc.save(str(path))
    doc.close()


def test_a_text_page_with_a_hero_image_is_sent_for_description(tmp_path, fake_vision):
    pdf = tmp_path / 'brief.pdf'
    _pdf_text_plus_image(pdf, fitz.Rect(72, 200, 520, 420))

    text = FileParser.extract_text(str(pdf))

    assert len(fake_vision.calls) == 1
    # The text layer is kept - it is the better copy - and the description is
    # appended to it rather than replacing it.
    assert 'Campaign brief' in text
    assert 'SLIDE TEXT: Launch the new blend.' in text
    # And it is the narrower prompt, not the transcribe-everything one.
    assert fake_vision.calls[0][0]['content'][0]['text'] is ILLUSTRATION_VISION_PROMPT


def test_small_icons_do_not_buy_a_vision_call(tmp_path, fake_vision):
    """UI chrome in a social mockup lands at ~10pt; it is not artwork."""
    pdf = tmp_path / 'brief.pdf'
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), 'Campaign brief for the new blend. ' * 6, fontsize=11)
    for x in (300, 340, 380):
        page.insert_image(fitz.Rect(x, 450, x + 10, 460), stream=_pixel_image())
    doc.save(str(pdf))
    doc.close()

    FileParser.extract_text(str(pdf))

    assert fake_vision.calls == []


def test_a_letterhead_logo_on_every_page_does_not_buy_a_call_per_page(tmp_path, fake_vision):
    """Over the per-image floor, under the page-share floor: still not artwork."""
    pdf = tmp_path / 'brief.pdf'
    side = MIN_IMAGE_SIDE_PT + 6
    doc = fitz.open()
    for _ in range(4):
        page = doc.new_page()
        page.insert_text((72, 100), 'Campaign brief for the new blend. ' * 6, fontsize=11)
        page.insert_image(fitz.Rect(72, 40, 72 + side, 40 + side), stream=_pixel_image())
    doc.save(str(pdf))
    doc.close()

    FileParser.extract_text(str(pdf))

    assert fake_vision.calls == []


def test_markers_are_numbered_across_both_kinds_of_page(tmp_path, monkeypatch):
    """One counter, document order - an image-only page and an illustrated one."""

    class _OneImage(_FakeVision):
        def chat(self, messages, **kwargs):
            self.calls.append(messages)
            return '([image1]: a red hatchback on a neon grid)'

    client = _OneImage()
    monkeypatch.setattr(FileParser, '_vision_client', staticmethod(lambda: client))

    pdf = tmp_path / 'mixed.pdf'
    doc = fitz.open()
    blind = doc.new_page()                       # page 1: no text layer
    blind.draw_rect(fitz.Rect(50, 50, 300, 300), fill=(0.2, 0.4, 0.9))
    rich = doc.new_page()                        # page 2: text layer + artwork
    rich.insert_text((72, 100), 'Campaign brief for the new blend. ' * 6, fontsize=11)
    rich.insert_image(fitz.Rect(72, 200, 520, 420), stream=_pixel_image())
    doc.save(str(pdf))
    doc.close()

    text = FileParser.extract_text(str(pdf))

    assert len(client.calls) == 2
    assert re.findall(r'\[image\d+\]', text) == ['[image1]', '[image2]']


def test_the_illustration_prompt_does_not_ask_for_a_transcription():
    # Pointed at a page whose text layer is already good, a transcription would
    # produce a second, worse copy of text we already have exactly.
    assert 'do NOT transcribe the page' in ILLUSTRATION_VISION_PROMPT
    assert '([image1]:' in ILLUSTRATION_VISION_PROMPT
    # Text baked into the artwork is the exception - the text layer never had it.
    assert 'Transcribe any text that is part of the image itself' in ILLUSTRATION_VISION_PROMPT
    # A flat panel carrying copy is content, not a background wash.
    assert 'fill when it carries text' in ILLUSTRATION_VISION_PROMPT


# ---- configuration ----------------------------------------------------------
# A vision endpoint on its own host with no key of its own fails at parse time,
# not at startup: the pages are skipped with a warning and the brief extracts to
# nothing. validate() has to catch it, on the same rule as the chatbot key.

def _vision_key_errors(monkeypatch, **attrs):
    from app.config import Config

    for name, value in attrs.items():
        monkeypatch.setattr(Config, name, value)
    return [e for e in Config.validate() if e.startswith('VISION_LLM_API_KEY')]


def test_inherited_endpoint_does_not_report_a_missing_vision_key(monkeypatch):
    # Nothing VISION_* configured: the endpoint and the key are inherited, so a
    # missing key is already reported once as LLM_API_KEY.
    errors = _vision_key_errors(
        monkeypatch,
        LLM_BASE_URL='https://api.openai.com/v1',
        LLM_API_KEY=None,
        VISION_LLM_BASE_URL='https://api.openai.com/v1',
        VISION_LLM_API_KEY=None,
    )

    assert errors == []


def test_separate_vision_endpoint_without_its_own_key_is_reported(monkeypatch):
    errors = _vision_key_errors(
        monkeypatch,
        LLM_BASE_URL='https://api.openai.com/v1',
        LLM_API_KEY='agent-key',
        VISION_LLM_BASE_URL='https://integrate.api.nvidia.com/v1',
        VISION_LLM_API_KEY=None,
    )

    assert len(errors) == 1


def test_separate_vision_endpoint_with_its_own_key_is_accepted(monkeypatch):
    errors = _vision_key_errors(
        monkeypatch,
        LLM_BASE_URL='https://api.openai.com/v1',
        LLM_API_KEY='agent-key',
        VISION_LLM_BASE_URL='https://integrate.api.nvidia.com/v1',
        VISION_LLM_API_KEY='vision-key',
    )

    assert errors == []
