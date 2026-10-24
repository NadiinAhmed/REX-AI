"""Composition root: the ONE place where components are created and connected.

Everything else receives its collaborators through constructors (dependency injection), so
tests can swap any part (fake LLM, in-memory DB, temporary vector store) without patching.
Used by app.py, the CLI, the MCP server, the evaluation scripts and the integration tests.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from src.agents.registry import AgentRegistry
from src.agents.rex_assistant import RexAssistant
from src.config import PROJECT_ROOT, Settings
from src.db.database import Database
from src.db.repositories import CourseRepository, DocumentRepository, LeadRepository
from src.knowledge_base.loader import load_catalog
from src.knowledge_base.retriever import CourseRetriever, KeywordRetriever
from src.models.course import DatasetInfo
from src.services.lead_service import LeadService
from src.services.llm_service import LLMService, create_llm_service
from src.utils.logging_config import get_logger

logger = get_logger("bootstrap")
SEED_DOCUMENTS_DIR = PROJECT_ROOT / "data" / "documents"


@dataclass
class KnowledgeBase:
    """The document side of RAG: ingestion (write) and passage retrieval (read)."""
    ingestion: object          # DocumentIngestionService
    passages: object           # PassageRetriever
    documents: DocumentRepository
    embedding_model: str


@dataclass
class AppContext:
    registry: AgentRegistry
    dataset: DatasetInfo
    course_count: int
    llm_status: str
    settings: Settings | None = None
    database: Database | None = None
    courses: CourseRepository | None = None
    knowledge: KnowledgeBase | None = None
    rag_status: str = "Document search disabled."
    leads: LeadRepository | None = None
    course_backend_status: str = "direct"
    warnings: list[str] = field(default_factory=list)


def load_course_catalog(settings: Settings, courses: CourseRepository) -> tuple[DatasetInfo, list[str]]:
    """The database is the source of truth. On first run it is seeded from REX_COURSES_PATH."""
    warnings: list[str] = []
    info = courses.dataset_info()
    if info is None:
        catalog = load_catalog(settings.courses_path)    # raises KnowledgeBaseError with a clear message
        courses.replace_catalog(catalog, source_file=str(settings.courses_path))
        logger.info("Seeded %d courses from %s", len(catalog.courses), settings.courses_path.name)
        return catalog.dataset, warnings
    dataset, source_file = info
    if source_file and Path(source_file).resolve() != settings.courses_path.resolve():
        warnings.append(f"The database holds '{dataset.name}' (from {Path(source_file).name}) but "
                        f"REX_COURSES_PATH is {settings.courses_path.name}. Run "
                        "`python -m src.cli seed-courses --replace` to switch.")
    return dataset, warnings


def build_knowledge_base(settings: Settings, db: Database, seed_dir: Path | None) -> KnowledgeBase:
    from src.ingestion.chunker import TextChunker
    from src.ingestion.pdf import PdfTextExtractor, PdfValidator
    from src.ingestion.service import DocumentIngestionService, DuplicateDocumentError
    from src.rag.embeddings import create_embeddings, embedding_model_id
    from src.rag.retriever import PassageRetriever
    from src.rag.vector_store import ChromaVectorStore

    embeddings = create_embeddings(settings)
    model_id = embedding_model_id(embeddings)
    vectors = ChromaVectorStore(settings.vector_dir, model_id)
    documents = DocumentRepository(db)
    ingestion = DocumentIngestionService(
        documents, vectors, embeddings, TextChunker(settings.chunk_size, settings.chunk_overlap),
        PdfValidator(int(settings.max_upload_mb * 1_000_000)), PdfTextExtractor(settings.max_pdf_pages),
        settings.upload_dir)
    # PDFs committed to data/documents/ were reviewed by the team: index them once, as approved.
    if seed_dir and seed_dir.is_dir():
        for pdf in sorted(seed_dir.glob("*.pdf")):
            try:
                ingestion.ingest(pdf, doc_type="academy_official", approve=True)
            except DuplicateDocumentError:
                pass   # already indexed on a previous start
    # Vectors from another embedding model are not comparable: re-index those documents.
    for doc in documents.list():
        if doc.embedding_model != model_id and doc.index_status == "indexed":
            logger.info("Re-indexing %s for embedding model %s", doc.filename, model_id)
            ingestion.reindex(doc.id)
            documents.set_embedding_model(doc.id, model_id)
    passages = PassageRetriever(documents, vectors, embeddings, settings.retrieval_top_k,
                                settings.retrieval_min_score,
                                require_term_overlap=settings.keyword_gate_enabled)
    return KnowledgeBase(ingestion=ingestion, passages=passages, documents=documents, embedding_model=model_id)


def build_course_retriever(settings: Settings, courses: CourseRepository) -> tuple[CourseRetriever, str]:
    if settings.course_backend == "mcp":
        try:
            from src.mcp_client.academy_client import McpCourseRetriever
            retriever = McpCourseRetriever.connect(settings)
            return retriever, f"MCP (rex-academy server, {len(retriever.all_courses())} courses)"
        except Exception as exc:   # never let an MCP problem take the assistant down
            logger.warning("MCP course backend unavailable: %s", exc)
            return KeywordRetriever(courses.list_approved()), f"direct (MCP unavailable: {exc})"
    return KeywordRetriever(courses.list_approved()), "direct"


def build_app(settings: Settings, llm: LLMService | None = None, use_configured_llm: bool = True,
              enable_rag: bool = True, seed_dir: Path | None = SEED_DOCUMENTS_DIR) -> AppContext:
    """Raises KnowledgeBaseError if the course data cannot be loaded, DatabaseError if the DB fails."""
    db = Database(settings.database_url)
    db.migrate()
    courses = CourseRepository(db)
    dataset, warnings = load_course_catalog(settings, courses)

    if llm is not None:
        status = f"LLM injected: {llm.name}"
    elif use_configured_llm:
        llm, status = create_llm_service(settings)
    else:
        status = "LLM disabled. Running in deterministic demo mode."

    knowledge, rag_status = None, "Document search disabled."
    if enable_rag:
        try:
            knowledge = build_knowledge_base(settings, db, seed_dir)
            stats = knowledge.documents.stats()
            rag_status = (f"{stats['approved']} approved document(s), {stats['chunks']} chunks · "
                          f"embeddings: {knowledge.embedding_model}")
        except Exception as exc:   # the assistant still works on the course catalog without RAG
            logger.exception("Knowledge base unavailable")
            rag_status = f"Document search unavailable: {exc}"
            warnings.append(rag_status)

    retriever, backend_status = build_course_retriever(settings, courses)
    leads = LeadRepository(db)
    registry = AgentRegistry()
    registry.register(RexAssistant(
        retriever=retriever, llm=llm, dataset_notice=dataset.notice,
        passages=knowledge.passages if knowledge else None, lead_service=LeadService(leads)))
    return AppContext(registry=registry, dataset=dataset, course_count=len(retriever.all_courses()),
                      llm_status=status, settings=settings, database=db, courses=courses, knowledge=knowledge,
                      rag_status=rag_status, leads=leads, course_backend_status=backend_status, warnings=warnings)
