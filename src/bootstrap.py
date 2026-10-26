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
    rfp_repository: object | None = None       # RfpRepository (Agent 2 dashboard)
    curriculum_service: object | None = None   # CurriculumService (Agent 3 page + Agent 4 input)
    document_service: object | None = None     # DocumentService (Agent 4 page)


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
    rfp_repository = register_rfp_agent(registry, settings, db, retriever, llm, warnings)
    curriculum_service = register_curriculum_agent(registry, db, courses, knowledge.passages if knowledge else None,
                                                   llm, rfp_repository, warnings)
    document_service = register_document_agent(registry, settings, db, courses, curriculum_service, llm, warnings)
    return AppContext(registry=registry, dataset=dataset, course_count=len(retriever.all_courses()),
                      llm_status=status, settings=settings, database=db, courses=courses, knowledge=knowledge,
                      rag_status=rag_status, leads=leads, course_backend_status=backend_status, warnings=warnings,
                      rfp_repository=rfp_repository, curriculum_service=curriculum_service, document_service=document_service)


def register_rfp_agent(registry: AgentRegistry, settings: Settings, db: Database,
                       courses: CourseRetriever, llm: LLMService | None, warnings: list[str]):
    """Agent 2: tender feed → scan pipeline → shared DB. Its problems never stop the rest of the app."""
    try:
        from src.agents.rfp_research import RfpResearchAgent
        from src.db.rfp_repository import RfpRepository
        from src.rfp.pipeline import RfpScanPipeline
        from src.rfp.sources import JsonFileSource

        repository = RfpRepository(db)
        pipeline = RfpScanPipeline(
            sources=[JsonFileSource(settings.rfp_feed_path)], repository=repository,
            courses=courses.all_courses, llm=llm if settings.rfp_llm_summaries else None)
        registry.register(RfpResearchAgent(pipeline, repository))
        return repository
    except Exception as exc:
        logger.exception("RFP research agent unavailable")
        warnings.append(f"RFP research agent unavailable: {exc}")
        return None


def register_curriculum_agent(registry: AgentRegistry, db: Database, courses: CourseRepository, passages,
                              llm: LLMService | None, rfp_repository, warnings: list[str]):
    """Agent 3: course catalog + approved documents + RFP signals -> curricula -> review -> PDF."""
    try:
        from src.agents.curriculum import CurriculumAgent
        from src.curriculum.frameworks import FrameworkLibrary
        from src.curriculum.service import CurriculumService
        from src.curriculum.topics import TrackTopics
        from src.db.curriculum_repository import CurriculumRepository

        service = CurriculumService(CurriculumRepository(db), courses, FrameworkLibrary.load(), TrackTopics.load(),
                                    passages=passages, llm=llm, rfp_repository=rfp_repository)
        registry.register(CurriculumAgent(service, courses))
        return service
    except Exception as exc:
        logger.exception("Curriculum agent unavailable")
        warnings.append(f"Curriculum agent unavailable: {exc}")
        return None


def register_document_agent(registry: AgentRegistry, settings: Settings, db: Database, courses: CourseRepository,
                            curriculum_service, llm: LLMService | None, warnings: list[str]):
    """Agent 4: approved curriculum (or course record) -> templates -> review -> PDF / e-mail."""
    if curriculum_service is None:
        warnings.append("Document Generation agent unavailable: it needs the Curriculum agent.")
        return None
    try:
        from src.agents.document_generation import DocumentGenerationAgent
        from src.db.document_repository import GeneratedDocumentRepository
        from src.documents.service import DocumentService
        from src.services.email_service import EmailService

        service = DocumentService(GeneratedDocumentRepository(db), curriculum_service, courses, llm=llm,
                                  email=EmailService(settings))
        registry.register(DocumentGenerationAgent(service, courses))
        return service
    except Exception as exc:
        logger.exception("Document Generation agent unavailable")
        warnings.append(f"Document Generation agent unavailable: {exc}")
        return None
