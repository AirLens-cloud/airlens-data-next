#!/usr/bin/env python3
"""
pdf_extract.py — PDF → normalized text ($0, local, no LLM).

Pure-local extraction utility used by collect_llm_extract.py (WS3) to feed
free-text policy PDFs (China MEE / India CPCB) into the LLM extractor. On its
own it writes nothing to the registry — it is a helper, not a collector.

Dependency: pdfplumber (MIT). pymupdf (AGPL) is deliberately avoided.

Usage (as a library):
  from collect_policies.pdf_extract import extract_pdf
  text = extract_pdf("https://example.gov/policy.pdf")
"""
from __future__ import annotations

import io
import logging
import re
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)


def fetch_pdf_bytes(url: str, timeout: int = 60) -> bytes:
    """Download a PDF over HTTP(S). Returns raw bytes (b'' on failure)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AirLens-policy-collect"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as e:  # noqa: BLE001 — resilience: never throw
        logger.warning(f"PDF fetch failed ({url}): {e}")
        return b""


def pdf_bytes_to_text(data: bytes, max_pages: int = 40) -> str:
    """Extract text from PDF bytes via pdfplumber. Returns '' on any failure."""
    if not data:
        return ""
    try:
        import pdfplumber
    except ImportError:
        logger.warning("pdfplumber not installed — `pip install pdfplumber`")
        return ""
    try:
        parts: list[str] = []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages[:max_pages]:
                text = page.extract_text() or ""
                if text:
                    parts.append(text)
        return "\n\n".join(parts)
    except Exception as e:  # noqa: BLE001 — resilience: malformed PDF → empty
        logger.warning(f"PDF parse failed: {e}")
        return ""


def normalize_whitespace(text: str) -> str:
    """Collapse runs of spaces/tabs and blank lines; trim ends. Pure function."""
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_pdf(url_or_path: str, max_pages: int = 40) -> str:
    """Fetch (URL) or read (path) a PDF and return normalized text. '' on failure."""
    if url_or_path.startswith(("http://", "https://")):
        data = fetch_pdf_bytes(url_or_path)
    else:
        try:
            data = Path(url_or_path).read_bytes()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"PDF read failed ({url_or_path}): {e}")
            return ""
    return normalize_whitespace(pdf_bytes_to_text(data, max_pages=max_pages))
