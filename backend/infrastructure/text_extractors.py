from backend.domain.entities import ITextExtractor
import logging

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)

class PyPDFExtractor(ITextExtractor):
    """PDF text extraction using pypdf - Strategy Pattern implementation"""
    
    def extract_text(self, file_path: str) -> str:
        try:
            import pypdf
            with open(file_path, 'rb') as file:
                reader = pypdf.PdfReader(file)
                text = ""
                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
                return text.strip()
        except Exception as e:
            logger.error(f"PyPDF extraction failed: {e}")
            raise

class PDFPlumberExtractor(ITextExtractor):
    """Alternative PDF extraction using pdfplumber - Fallback strategy"""
    
    def extract_text(self, file_path: str) -> str:
        try:
            import pdfplumber
            with pdfplumber.open(file_path) as pdf:
                text = ""
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
                return text.strip()
        except Exception as e:
            logger.error(f"PDFPlumber extraction failed: {e}")
            raise

class TextExtractionService:
    """Chain of Responsibility pattern for text extraction with fallback"""
    
    def __init__(self):
        self.extractors = [
            PyPDFExtractor(),
            PDFPlumberExtractor()
        ]
    
    def extract_with_fallback(self, file_path: str) -> str:
        """Try multiple extraction methods until one succeeds"""
        return self.extract_with_source(file_path)[0]

    def extract_with_source(self, file_path: str) -> tuple[str, str]:
        """The text, and the name of the extractor that produced it.

        Which extractor won is not a detail. Different libraries emit different
        whitespace and reading order for the same PDF, so the same file
        extracted two ways is a different chunk hash for text nobody touched —
        and the fallback chain silently changes winner when the first extractor
        has a bad day. That is why `extractor` is part of the chunking profile,
        and it could not be recorded while this method threw the answer away.
        """
        last_error = None

        for extractor in self.extractors:
            name = extractor.__class__.__name__
            try:
                text = extractor.extract_text(file_path)
                if text and len(text.strip()) > 50:  # Quality check
                    logger.info(f"Successfully extracted text using {name}")
                    return text, name
            except Exception as e:
                last_error = e
                logger.warning(f"{name} failed: {e}")
                continue

        raise Exception(f"All text extraction methods failed. Last error: {last_error}")