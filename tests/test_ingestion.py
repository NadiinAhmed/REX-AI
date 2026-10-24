"""PDF ingestion: validation, multiple documents, duplicates, update/remove, consistency."""
from pathlib import Path

import pytest

from src.db.database import Database
from src.db.models import ApprovalStatus
from src.db.repositories import DocumentRepository
from src.ingestion.chunker import TextChunker
from src.ingestion.pdf import (EmptyDocumentError, InvalidDocumentError, PdfTextExtractor, PdfValidator,
                               clean_page_text)
from src.ingestion.service import DocumentIngestionService, DocumentNotFoundError, DuplicateDocumentError
from src.rag.embeddings import HashingEmbeddings
from src.rag.retriever import PassageRetriever
from src.rag.vector_store import ChromaVectorStore
from tests.conftest import PORTFOLIO_PDF

FAQ = ["Admission FAQ\nApplicants must be final-year university students.\nThe admission interview takes 20 minutes.",
       "Payment policy\nFees can be paid in two installments.\nRefunds are possible within 7 days."]
SCHEDULE = ["Cohort schedule\nThe winter cohort starts on 15 January.\nClasses run on Saturday and Tuesday evenings."]


@pytest.fixture
def kb(tmp_path):
    db = Database("sqlite://"); db.migrate()
    docs = DocumentRepository(db)
    emb = HashingEmbeddings()
    vectors = ChromaVectorStore(tmp_path / "vec", emb.model_id)
    service = DocumentIngestionService(docs, vectors, emb, TextChunker(400, 60), PdfValidator(2_000_000),
                                       PdfTextExtractor(50), tmp_path / "files")
    retriever = PassageRetriever(docs, vectors, emb, top_k=3, min_score=0.1)
    return service, retriever, docs, vectors


def test_multiple_pdfs_keep_page_and_document_metadata(kb, make_pdf):
    service, retriever, docs, _ = kb
    faq = service.ingest(make_pdf("faq.pdf", FAQ), approve=True)
    sch = service.ingest(make_pdf("schedule.pdf", SCHEDULE), approve=True)
    assert (faq.page_count, sch.page_count) == (2, 1)
    top = retriever.retrieve("Can I pay the fees in installments?").passages[0]
    assert (top.filename, top.page, top.document_id) == ("faq.pdf", 2, faq.document_id)
    top = retriever.retrieve("When does the winter cohort start?").passages[0]
    assert (top.filename, top.page) == ("schedule.pdf", 1)


def test_pending_documents_are_never_searched_until_approved(kb, make_pdf):
    service, retriever, *_ = kb
    r = service.ingest(make_pdf("faq.pdf", FAQ))                      # default: pending review
    assert r.approval_status == "pending"
    assert not retriever.retrieve("refunds within 7 days").found
    service.set_approval(r.document_id, ApprovalStatus.APPROVED)
    assert retriever.retrieve("refunds within 7 days").found
    service.set_approval(r.document_id, ApprovalStatus.REJECTED)
    assert not retriever.retrieve("refunds within 7 days").found


def test_duplicate_upload_is_rejected(kb, make_pdf):
    service, *_ = kb
    path = make_pdf("faq.pdf", FAQ)
    first = service.ingest(path)
    copy = path.with_name("faq_copy.pdf"); copy.write_bytes(path.read_bytes())
    with pytest.raises(DuplicateDocumentError) as err:
        service.ingest(copy)
    assert err.value.existing_id == first.document_id


@pytest.mark.parametrize("name,content,error,match", [
    ("notes.txt", b"%PDF-1.4 fake", InvalidDocumentError, "not a PDF"),
    ("broken.pdf", b"this is not a pdf at all", InvalidDocumentError, "does not look like"),
    ("zero.pdf", b"", InvalidDocumentError, "empty"),
    ("truncated.pdf", b"%PDF-1.4\n1 0 obj << /Type /Catalog >>\n", InvalidDocumentError, "could not be read"),
])
def test_invalid_files_are_rejected_with_clear_errors(kb, tmp_path, name, content, error, match):
    service, _, docs, _ = kb
    path = tmp_path / name; path.write_bytes(content)
    with pytest.raises(error, match=match):
        service.ingest(path)
    assert docs.list() == []                                     # nothing half-saved


def test_pdf_without_text_is_rejected(kb, make_pdf):
    service, *_ = kb
    with pytest.raises(EmptyDocumentError, match="no extractable text"):
        service.ingest(make_pdf("scan.pdf", ["", ""]))


def test_oversized_file_is_rejected(tmp_path, make_pdf):
    with pytest.raises(InvalidDocumentError, match="limit"):
        PdfValidator(max_bytes=100).validate(make_pdf("big.pdf", FAQ), "big.pdf")


def test_replace_and_remove_touch_only_one_document(kb, make_pdf):
    service, retriever, docs, vectors = kb
    faq = service.ingest(make_pdf("faq.pdf", FAQ), approve=True)
    sch = service.ingest(make_pdf("schedule.pdf", SCHEDULE), approve=True)
    new = service.replace(sch.document_id, make_pdf("schedule_v2.pdf",
                                                    ["Cohort schedule\nThe spring cohort starts on 1 March."]))
    assert new.approval_status == "approved"                         # approval carried over
    assert docs.get(sch.document_id) is None
    assert retriever.retrieve("spring cohort starts").passages[0].filename == "schedule_v2.pdf"
    service.remove(faq.document_id)
    assert vectors.ids_for_document(faq.document_id) == []
    assert [d.filename for d in docs.list()] == ["schedule_v2.pdf"]
    assert service.check_consistency() == []
    with pytest.raises(DocumentNotFoundError):
        service.remove(faq.document_id)


def test_failed_indexing_rolls_back_everything(kb, make_pdf, monkeypatch):
    service, _, docs, vectors = kb
    monkeypatch.setattr(service._embeddings, "embed_documents", lambda texts: 1 / 0)
    with pytest.raises(Exception, match="nothing was kept"):
        service.ingest(make_pdf("faq.pdf", FAQ))
    assert docs.list() == [] and vectors.count() == 0


def test_letter_spaced_headings_and_repeated_lines_are_cleaned():
    text = "C U R R I C U L U M\nA long sentence that appears twice on this page.\nA long sentence that appears twice on this page."
    assert clean_page_text(text) == "CURRICULUM\nA long sentence that appears twice on this page."


def test_real_academy_portfolio(kb):
    """The official PDF: running headers removed, page-8 duplicate block removed, pages cited."""
    service, retriever, docs, _ = kb
    r = service.ingest(PORTFOLIO_PDF, approve=True)
    assert r.page_count == 9 and r.warnings == []
    chunks = docs.chunks(r.document_id)
    assert not any("PORTFOLIO" in c.text.split("\n")[0].replace(" ", "") for c in chunks)
    assert len({c.text_hash for c in chunks}) == len(chunks)
    assert retriever.retrieve("Software Testing tools Playwright Cypress").passages[0].page == 3
    assert retriever.retrieve("contact email for partnerships").passages[0].page == 9
