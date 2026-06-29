# -*- coding: utf-8 -*-
import fitz  # PyMuPDF
import io

def extract_text_from_pdf(pdf_content: bytes) -> str:
    """
    Extract plain text from the binary content of a PDF file.

    :param pdf_content: Binary data of the PDF file.
    :return: Extracted plain text content.
    """
    try:
        # Open the PDF from in-memory binary data
        pdf_document = fitz.open(stream=pdf_content, filetype="pdf")
        
        text = []
        for page_num in range(len(pdf_document)):
            page = pdf_document.load_page(page_num)
            text.append(page.get_text())
            
        return "\n".join(text)
    except Exception as e:
        print(f"--- Error processing PDF: {e} ---")
        return ""
