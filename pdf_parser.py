import os
import logging
from PyPDF2 import PdfReader
from langchain.text_splitter import RecursiveCharacterTextSplitter

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

BASE_DIR = os.path.join(os.path.dirname(__file__), 'AI Agent Assessment - Candidate Pack')

PDF_FILES = [
    '01_Support_Policy_v3_CURRENT.pdf',
    '02_Support_Policy_v2_DEPRECATED.pdf',
    '03_Cancellation_and_Service_Credit_SOP_v4.pdf',
    '04_Product_Operations_Guide_and_Known_Issues.pdf',
    '05_Northstar_Logistics_Enterprise_Agreement.pdf',
    '06_LumenWorks_Service_Agreement.pdf'
]

def read_pdf_and_chunk(pdf_file: str, chunk_size: int = 1000, chunk_overlap: int = 150):
    """Read PDF file, extract text, chunk it, and add metadata tags."""
    pdf_path = os.path.join(BASE_DIR, pdf_file)
    if not os.path.isfile(pdf_path):
        logging.error(f"PDF file not found: {pdf_path}")
        return [], []

    try:
        reader = PdfReader(pdf_path)
        full_text = ''.join([page.extract_text() or '' for page in reader.pages])
        logging.info(f"Extracted text from {pdf_file}, length: {len(full_text)} characters.")
    except Exception as e:
        logging.error(f"Failed to read or extract text from {pdf_file}: {e}")
        return [], []

    try:
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        chunks = text_splitter.split_text(full_text)
        metadata = [{'source': pdf_file} for _ in chunks]
        logging.info(f"Split {pdf_file} into {len(chunks)} chunks.")
    except Exception as e:
        logging.error(f"Text chunking failed for {pdf_file}: {e}")
        return [], []

    return chunks, metadata


def process_all_pdfs():
    """Process all predefined PDF files, chunk them, and gather all chunks and metadata."""
    all_chunks = []
    all_metadata = []
    for pdf_file in PDF_FILES:
        chunks, metadata = read_pdf_and_chunk(pdf_file)
        all_chunks.extend(chunks)
        all_metadata.extend(metadata)
    logging.info(f"Processed {len(PDF_FILES)} PDFs into {len(all_chunks)} chunks.")
    return all_chunks, all_metadata
