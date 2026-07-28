"""
Text processing service.
"""

from typing import List
from ..utils.file_parser import split_text_into_chunks


class TextProcessor:
    """Text processor."""
    
    @staticmethod
    def split_text(
        text: str,
        chunk_size: int = 500,
        overlap: int = 50
    ) -> List[str]:
        """
        Split text into chunks.

        Args:
            text: Source text
            chunk_size: Chunk size
            overlap: Overlap size

        Returns:
            The list of chunks
        """
        return split_text_into_chunks(text, chunk_size, overlap)
    
    @staticmethod
    def preprocess_text(text: str) -> str:
        """
        Preprocess text.
        - Strip redundant whitespace
        - Normalize line endings

        Args:
            text: Source text

        Returns:
            The processed text
        """
        import re
        
        # Normalize line endings
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        
        # Collapse runs of blank lines (keep at most two newlines)
        text = re.sub(r'\n{3,}', '\n\n', text)
        
        # Strip leading and trailing whitespace on each line
        lines = [line.strip() for line in text.split('\n')]
        text = '\n'.join(lines)
        
        return text.strip()
    
