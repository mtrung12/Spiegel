"""
File parsing helpers.
Extracts text from PDF, Markdown and TXT files.

A PDF page only yields text when it has a text layer. A campaign brief is very
often a deck exported from Figma, Canva or Keynote, which is one flat image per
slide - ``page.get_text()`` returns nothing for every page of it. Those pages are
rendered and handed to a vision model instead, so the brief is not silently read
as an empty document.
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
        image_pages: List[int] = []

        with fitz.open(file_path) as doc:
            for page in doc:
                text = page.get_text()
                if len(text.strip()) >= PAGE_TEXT_MIN_CHARS:
                    text_parts.append(text)
                else:
                    # Placeholder, so a page read by the model lands back in
                    # reading order rather than at the end of the document.
                    image_pages.append(len(text_parts))
                    text_parts.append(text.strip())

            if image_pages:
                FileParser._read_image_pages(doc, image_pages, text_parts, file_path)

        return "\n\n".join(part for part in text_parts if part.strip())

    @staticmethod
    def _read_image_pages(
        doc,
        page_indices: List[int],
        text_parts: List[str],
        file_path: str,
    ) -> None:
        """Render the text-less pages and fill their slots in ``text_parts``."""
        from ..config import Config

        budget = max(0, Config.VISION_PDF_MAX_PAGES)
        if len(page_indices) > budget:
            logger.warning(
                "%s: %d pages have no text layer, reading the first %d "
                "(raise VISION_PDF_MAX_PAGES to read them all)",
                Path(file_path).name, len(page_indices), budget,
            )
            page_indices = page_indices[:budget]

        client = FileParser._vision_client()
        if client is None:
            logger.warning(
                "%s: %d pages have no text layer and no vision model is "
                "configured; set VISION_LLM_* to read them",
                Path(file_path).name, len(page_indices),
            )
            return

        logger.info(
            "%s: reading %d image-only pages with %s",
            Path(file_path).name, len(page_indices), client.model,
        )

        next_image = 1
        for index in page_indices:
            try:
                pixmap = doc[index].get_pixmap(dpi=Config.VISION_PDF_DPI)
                page_text = FileParser._describe_page(client, pixmap.tobytes("png"))
            except Exception as e:
                # One unreadable page is not worth losing the other 39.
                logger.warning(
                    "%s page %d: vision read failed: %s: %s",
                    Path(file_path).name, index + 1, type(e).__name__, str(e)[:120],
                )
                continue
            if page_text:
                # Pages are read in document order, so the running counter makes
                # [image1], [image2], ... unique across the whole file.
                page_text, used = renumber_image_markers(page_text, next_image)
                next_image += used
                text_parts[index] = page_text

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
    def _describe_page(client, png_bytes: bytes) -> Optional[str]:
        """Send one rendered page to the vision model and return what it read."""
        from .pipeline_logger import llm_caller

        encoded = base64.b64encode(png_bytes).decode('ascii')
        with llm_caller('FileParser', 'pdf page image'):
            text = client.chat(
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": PAGE_VISION_PROMPT},
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

