"""
File parsing helpers.
Extracts text from PDF, Markdown and TXT files.

A PDF page only yields text when it has a text layer. A campaign brief is very
often a deck exported from Figma, Canva or Keynote, which is one flat image per
slide - ``page.get_text()`` returns nothing for every page of it. Those pages are
rendered and handed to a vision model instead, so the brief is not silently read
as an empty document.

A page can also be both. A brief laid out in InDesign or exported from a web
template has a real text layer *and* carries the campaign's actual creative as
placed images - the hero shot, the social mockups, the product render. Reading
only the text layer there silently drops the artwork the simulated audience is
supposed to be reacting to. Those pages keep their text layer and get a second,
narrower vision call that describes the imagery alone.
"""

import base64
import re
from pathlib import Path
from typing import List, Optional, Tuple

from .logger import get_logger

logger = get_logger('spiegel.file_parser')

# Below this many characters a page counts as having no text layer. A slide
# exported as one image still carries a page number or a footer, so "any text at
# all" is the wrong test - it would leave a picture of 200 words looking full.
PAGE_TEXT_MIN_CHARS = 40

# What counts as artwork worth a vision call on a page that already has text.
# Measured on where the image is *placed*, not its source pixel size: a 1200x600
# source scaled into a 10pt footer logo is still a footer logo.
#
# Both tests have to pass. The per-image floor drops UI chrome - the comment and
# upvote icons in a social mockup land at ~10pt. The page-area floor stops a
# letterhead logo on every page of a 40-page deck from buying 40 vision calls,
# since one small mark never reaches 3% of the page on its own.
MIN_IMAGE_SIDE_PT = 48        # ~0.67 inch
MIN_IMAGE_PAGE_SHARE = 0.03   # 3% of the page area, summed over qualifying images

# The model is reading a marketing deck, not a scanned book, so transcription
# alone loses most of the brief: the audience, the tone and the positioning live
# in the imagery and the layout. Ask for both, and forbid the commentary that
# would otherwise be read downstream as if the brief had said it.
#
# Each image is marked inline where it sits rather than described in a block at
# the end, so a caption stays attached to its own image and the entity
# extraction downstream sees the picture in the same place the reader would.
PAGE_VISION_PROMPT = """This is one page of a marketing campaign brief or creative deck.

Write out everything on the page as text, following the reading order of the layout.

- Transcribe ALL visible text verbatim - headlines, body copy, labels, footnotes,
  numbers in tables and charts.
- Where a photograph, illustration, product shot, chart or logo sits in that
  order, do not skip it and do not save it for the end. Put a marker inline at
  that exact position, in this form:

  ([image1]: <what the image shows, in detail - subject, setting, any people and
  what they are doing, the product, colours, mood; for a chart, its type, axes
  and plotted values>. Caption: "<the caption printed on the page, verbatim>")

  Number them [image1], [image2], ... down the page. Drop the `Caption:` part
  entirely when the page prints no caption for that image - never invent one.
- A decorative rule, a background wash or a plain block of colour is not an
  image. Skip those.

Output the page content only. No preamble, no summary, no commentary of your own,
and never guess at text that is too small or cut off - omit it instead."""

# The sibling prompt, for a page whose text layer was already extracted cleanly.
# Re-transcribing the body copy here would produce a second, worse copy of text
# we already have exactly, so this asks for the artwork alone. Text *inside* an
# image is the exception - it is baked into the pixels and the text layer never
# had it, which on a campaign brief is usually the headline creative itself.
ILLUSTRATION_VISION_PROMPT = """This is one page of a marketing campaign brief or creative deck.
Its body text has already been extracted separately - do NOT transcribe the page.

Describe only the pictures on the page: photographs, illustrations, product
shots, renders, social-post mockups, charts, diagrams and logos.

For each one, in the order they appear down the page:

([image1]: <what it shows, in detail - subject, setting, any people and what they
are doing, the product, colours, mood; for a chart, its type, axes and plotted
values; for a mockup of a social post or an ad, the platform it imitates and the
copy printed inside it, verbatim>. Caption: "<the caption printed beneath it on
the page, verbatim>")

Rules:
- Number them [image1], [image2], ... down the page.
- Transcribe any text that is part of the image itself - a headline burned into
  a banner, the copy inside a mocked-up post, labels on a chart. That text is not
  in the page's text layer and would otherwise be lost.
- Drop the `Caption:` part entirely when no caption is printed - never invent one.
- Skip separators, rules, and flat background fills. A panel is NOT a background
  fill when it carries text, a product, or a chart - describe those, however
  plain their styling.
- If the page turns out to have no real imagery, output nothing at all.

Output the markers only. No preamble, no summary, no commentary of your own."""


# Tolerant on the way in - the model writes [image2], [Image 2] or [IMAGE2] -
# and strict on the way out, so downstream only ever sees one spelling.
_IMAGE_MARKER = re.compile(r'\[\s*image\s*(\d+)\s*\]', re.IGNORECASE)


def renumber_image_markers(text: str, start: int) -> Tuple[str, int]:
    """
    Renumber one page's image markers into a document-wide sequence.

    Each page is its own vision call, so every page comes back numbered from
    [image1]. Left alone, a ten-page deck has ten [image1]s and a reference to
    one of them means nothing. Markers are renumbered in order of first
    appearance, and a repeat reference to the same image keeps its new number.

    Args:
        text: One page as the vision model returned it
        start: The next free image number in the document

    Returns:
        ``(renumbered text, how many distinct images this page used)``
    """
    mapping: dict = {}

    def replace(match: 're.Match') -> str:
        local = match.group(1)
        if local not in mapping:
            mapping[local] = start + len(mapping)
        return f"[image{mapping[local]}]"

    return _IMAGE_MARKER.sub(replace, text), len(mapping)


def _read_text_with_fallback(file_path: str) -> str:
    """
    Read a text file, sniffing the encoding when UTF-8 fails.

    Fallback chain:
    1. Try UTF-8 first
    2. Detect the encoding with charset_normalizer
    3. Fall back to chardet
    4. Last resort: UTF-8 with errors='replace'

    Args:
        file_path: Path to the file

    Returns:
        The decoded text
    """
    data = Path(file_path).read_bytes()
    
    # Try UTF-8 first
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        pass
    
    # Try detecting the encoding with charset_normalizer
    encoding = None
    try:
        from charset_normalizer import from_bytes
        best = from_bytes(data).best()
        if best and best.encoding:
            encoding = best.encoding
    except Exception:
        pass
    
    # Fall back to chardet
    if not encoding:
        try:
            import chardet
            result = chardet.detect(data)
            encoding = result.get('encoding') if result else None
        except Exception:
            pass
    
    # Last resort: UTF-8 with replacement characters
    if not encoding:
        encoding = 'utf-8'
    
    return data.decode(encoding, errors='replace')


class FileParser:
    """File parser."""
    
    SUPPORTED_EXTENSIONS = {'.pdf', '.md', '.markdown', '.txt'}
    
    @classmethod
    def extract_text(cls, file_path: str) -> str:
        """
        Extract text from a file.

        Args:
            file_path: Path to the file

        Returns:
            The extracted text
        """
        path = Path(file_path)
        
        if not path.exists():
            raise FileNotFoundError(f"file does not exist: {file_path}")
        
        suffix = path.suffix.lower()
        
        if suffix not in cls.SUPPORTED_EXTENSIONS:
            raise ValueError(f"unsupported file format: {suffix}")
        
        if suffix == '.pdf':
            return cls._extract_from_pdf(file_path)
        elif suffix in {'.md', '.markdown'}:
            return cls._extract_from_md(file_path)
        elif suffix == '.txt':
            return cls._extract_from_txt(file_path)
        
        raise ValueError(f"cannot handle file format: {suffix}")
    
    @staticmethod
    def _extract_from_pdf(file_path: str) -> str:
        """
        Extract text from a PDF, reading image-only pages with a vision model.

        Pages with a usable text layer are taken as-is - that path is unchanged
        and costs nothing. Only the pages that come back empty are rendered and
        sent to the model, so an ordinary text PDF makes no LLM calls at all.
        """
        try:
            import fitz  # PyMuPDF
        except ImportError:
            raise ImportError("PyMuPDF is required: pip install PyMuPDF")

        text_parts: List[str] = []
        # (slot, mode) for every page the vision model has to look at, in reading
        # order. 'page' means the page has no text layer and the model reads all
        # of it; 'illustration' means the text layer is good and only the artwork
        # is missing.
        vision_jobs: List[Tuple[int, str]] = []

        with fitz.open(file_path) as doc:
            for page in doc:
                text = page.get_text()
                slot = len(text_parts)
                if len(text.strip()) >= PAGE_TEXT_MIN_CHARS:
                    text_parts.append(text)
                    if FileParser._has_describable_images(page):
                        vision_jobs.append((slot, 'illustration'))
                else:
                    # Placeholder, so a page read by the model lands back in
                    # reading order rather than at the end of the document.
                    text_parts.append(text.strip())
                    vision_jobs.append((slot, 'page'))

            if vision_jobs:
                FileParser._read_image_pages(doc, vision_jobs, text_parts, file_path)

        return "\n\n".join(part for part in text_parts if part.strip())

    @staticmethod
    def _has_describable_images(page) -> bool:
        """
        Whether this page carries artwork worth a vision call.

        Judged on placed size, so a large source image scaled down to an icon is
        correctly ignored. See MIN_IMAGE_SIDE_PT / MIN_IMAGE_PAGE_SHARE for why
        both a per-image floor and a page-share floor are needed.
        """
        page_area = abs(page.rect.width * page.rect.height)
        if not page_area:
            return False

        covered = 0.0
        for info in page.get_images(full=True):
            # get_images can report images the page merely inherits; the rects
            # are what is actually painted on this page, and an inherited one
            # returns none.
            for rect in page.get_image_rects(info[0]):
                if min(rect.width, rect.height) < MIN_IMAGE_SIDE_PT:
                    continue
                covered += abs(rect.width * rect.height)

        return covered / page_area >= MIN_IMAGE_PAGE_SHARE

    @staticmethod
    def _read_image_pages(
        doc,
        jobs: List[Tuple[int, str]],
        text_parts: List[str],
        file_path: str,
    ) -> None:
        """
        Run the vision passes and fold the results into ``text_parts``.

        A 'page' job replaces the slot, because the page had no text layer to
        keep. An 'illustration' job appends to it, because the text layer is
        already the better copy of the body copy.
        """
        from ..config import Config

        budget = max(0, Config.VISION_PDF_MAX_PAGES)
        if len(jobs) > budget:
            logger.warning(
                "%s: %d pages need a vision read, reading the first %d "
                "(raise VISION_PDF_MAX_PAGES to read them all)",
                Path(file_path).name, len(jobs), budget,
            )
            jobs = jobs[:budget]

        client = FileParser._vision_client()
        if client is None:
            logger.warning(
                "%s: %d pages need a vision read and no vision model is "
                "configured; set VISION_LLM_* to read them",
                Path(file_path).name, len(jobs),
            )
            return

        blind = sum(1 for _, mode in jobs if mode == 'page')
        logger.info(
            "%s: vision read of %d pages with %s (%d image-only, %d illustrated)",
            Path(file_path).name, len(jobs), client.model, blind, len(jobs) - blind,
        )

        next_image = 1
        for index, mode in jobs:
            prompt = PAGE_VISION_PROMPT if mode == 'page' else ILLUSTRATION_VISION_PROMPT
            try:
                pixmap = doc[index].get_pixmap(dpi=Config.VISION_PDF_DPI)
                page_text = FileParser._describe_page(client, pixmap.tobytes("png"), prompt)
            except Exception as e:
                # One unreadable page is not worth losing the other 39.
                logger.warning(
                    "%s page %d: vision read failed: %s: %s",
                    Path(file_path).name, index + 1, type(e).__name__, str(e)[:120],
                )
                continue
            if not page_text:
                continue

            # Jobs are in document order, so the running counter makes
            # [image1], [image2], ... unique across the whole file.
            page_text, used = renumber_image_markers(page_text, next_image)
            next_image += used

            if mode == 'page':
                text_parts[index] = page_text
            else:
                text_parts[index] = f"{text_parts[index].rstrip()}\n\n{page_text}"

    @staticmethod
    def _vision_client():
        """The configured vision client, or None when it cannot be built."""
        from ..utils.llm_client import LLMClient

        try:
            return LLMClient.for_vision()
        except Exception as e:
            logger.warning("vision client unavailable: %s: %s", type(e).__name__, str(e)[:120])
            return None

    @staticmethod
    def _describe_page(
        client,
        png_bytes: bytes,
        prompt: str = PAGE_VISION_PROMPT,
    ) -> Optional[str]:
        """Send one rendered page to the vision model and return what it read."""
        from .pipeline_logger import llm_caller

        encoded = base64.b64encode(png_bytes).decode('ascii')
        with llm_caller('FileParser', 'pdf page image'):
            text = client.chat(
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{encoded}"},
                        },
                    ],
                }],
                temperature=0.0,
                max_tokens=4096,
            )
        return (text or '').strip() or None


    @staticmethod
    def _extract_from_md(file_path: str) -> str:
        """Extract text from Markdown, sniffing the encoding when needed."""
        return _read_text_with_fallback(file_path)
    
    @staticmethod
    def _extract_from_txt(file_path: str) -> str:
        """Extract text from a TXT file, sniffing the encoding when needed."""
        return _read_text_with_fallback(file_path)
    
def split_text_into_chunks(
    text: str, 
    chunk_size: int = 500, 
    overlap: int = 50
) -> List[str]:
    """
    Split text into chunks.

    Args:
        text: Source text
        chunk_size: Characters per chunk
        overlap: Overlapping characters between chunks

    Returns:
        The list of chunks
    """
    if len(text) <= chunk_size:
        return [text] if text.strip() else []
    
    chunks = []
    start = 0
    
    while start < len(text):
        end = start + chunk_size
        
        # Prefer to split on a sentence boundary
        if end < len(text):
            # Find the nearest sentence terminator
            for sep in ['。', '！', '？', '.\n', '!\n', '?\n', '\n\n', '. ', '! ', '? ']:
                last_sep = text[start:end].rfind(sep)
                if last_sep != -1 and last_sep > chunk_size * 0.3:
                    end = start + last_sep + len(sep)
                    break
        
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        
        # The next chunk starts inside the overlap window
        start = end - overlap if end < len(text) else len(text)
    
    return chunks

