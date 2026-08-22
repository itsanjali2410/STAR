import os
import re
import logging
from PyPDF2 import PdfReader
from langchain_text_splitters import RecursiveCharacterTextSplitter

BASE_DIR = os.path.join(os.path.dirname(__file__), 'documents')


def read_pdf_text(pdf_file: str) -> str:
    """Extract and whitespace-normalise the full text of one PDF."""
    reader = PdfReader(os.path.join(BASE_DIR, pdf_file))
    text = '\n'.join(page.extract_text() or '' for page in reader.pages)
    return re.sub(r'\s+', ' ', text).strip()


def load_all_documents(chunk_size: int = 800, chunk_overlap: int = 120):
    """Load every PDF in documents/.

    Returns (chunks, metadatas, texts): chunks + per-chunk {'source': filename}
    metadata for the vector store, and texts = {filename: full_text} for tools that
    need a whole document (e.g. a customer's agreement).
    """
    splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    chunks, metadatas, texts = [], [], {}
    for pdf_file in sorted(f for f in os.listdir(BASE_DIR) if f.lower().endswith('.pdf')):
        try:
            text = read_pdf_text(pdf_file)
        except Exception as e:
            logging.error(f"Failed to read {pdf_file}: {e}")
            continue
        texts[pdf_file] = text
        for chunk in splitter.split_text(text):
            chunks.append(chunk)
            metadatas.append({'source': pdf_file})
    logging.info(f"Loaded {len(texts)} PDFs into {len(chunks)} chunks.")
    return chunks, metadatas, texts
