"""Document Generation workflow: resolve data -> build from template -> store -> human edit -> approve -> export.

    generate ──▶ DRAFT ──edit──▶ DRAFT (new revision) ──approve──▶ APPROVED ──▶ download / e-mail

Only APPROVED documents can be e-mailed. Any edit sends an approved document back to draft.
"""
from __future__ import annotations

import hashlib
from datetime import date
from typing import Callable

from src.curriculum.service import CurriculumService
from src.db.document_models import GeneratedDocumentRecord
from src.db.document_repository import DocumentTemplateRepository, GeneratedDocumentRepository
from src.documents.academy_templates import TemplateError, apply_outline, read_outline
from src.documents.context import DocumentError, resolve_context
from src.documents.llm_helpers import generate_mcq, translate_document
from src.documents.models import DocStatus, DocType, DocumentRequest, StoredDocument
from src.documents.templates import TEMPLATE_VERSION, build_document
from src.pdf.content import DocumentContent, Section, bullets, note
from src.pdf.markdown import from_markdown, to_markdown
from src.pdf.renderer import render_pdf
from src.services.email_service import Attachment, EmailService
from src.services.llm_service import LLMService
from src.utils.logging_config import get_logger

logger = get_logger("documents.service")
MAX_MARKDOWN_CHARS = 300_000
MAX_TEMPLATE_BYTES = 10_000_000


def _stored(r: GeneratedDocumentRecord) -> StoredDocument:
    return StoredDocument(
        id=r.id, doc_type=DocType(r.doc_type), course_id=r.course_id, curriculum_id=r.curriculum_id,
        curriculum_revision=r.curriculum_revision, language=r.language, title=r.title, status=DocStatus(r.status),
        revision=r.revision, basis=r.basis, template_version=r.template_version, generation_mode=r.generation_mode,
        content=DocumentContent.model_validate(r.content), notices=list(r.notices), log=list(r.log),
        approved_by=r.approved_by, approved_at=r.approved_at)


class DocumentService:
    def __init__(self, repository: GeneratedDocumentRepository, curricula: CurriculumService, courses,
                 llm: LLMService | None = None, email: EmailService | None = None,
                 today: Callable[[], date] = date.today, templates: DocumentTemplateRepository | None = None) -> None:
        self._repo = repository
        self._templates = templates
        self._curricula = curricula
        self._courses = courses
        self._llm = llm
        self._email = email
        self._today = today

    @property
    def email_enabled(self) -> bool:
        return bool(self._email and self._email.enabled)

    # ------------------------------------------------------------------ generate
    def generate(self, request: DocumentRequest, actor: str = "system", use_llm: bool = True) -> StoredDocument:
        language = request.language
        ctx = resolve_context(request.model_copy(update={"language": "en" if request.doc_type == DocType.CURRICULUM_PDF else language}),
                              self._curricula, self._courses, self._today())
        notices = list(ctx.notices)
        if request.doc_type == DocType.CURRICULUM_PDF and language == "ar":
            notices.append("The curriculum PDF is English-only; choose another document type for an Arabic version.")
            language = "en"
        llm = self._llm if use_llm else None
        extras: dict = {}
        used_llm = False
        if request.doc_type == DocType.QUESTION_BANK:
            mcq, mcq_notice = generate_mcq(ctx.curriculum, llm)
            extras["mcq"] = mcq
            used_llm = used_llm or bool(mcq)
            if mcq_notice:
                notices.append(mcq_notice)
            if mcq:
                notices.append("Multiple-choice questions and keys were written by an AI model: verify every answer.")
        doc = build_document(request.doc_type, ctx, extras)
        if language == "ar":
            doc, tr_notice = translate_document(doc, llm)
            used_llm = used_llm or (tr_notice or "").startswith("Course content was translated")
            if tr_notice:
                notices.append(tr_notice)
        template_version = TEMPLATE_VERSION
        academy = self.active_template(request.doc_type, language) if request.use_academy_template else None
        if academy is not None:
            doc, template_notices = apply_outline(doc, list(academy.headings), f"{academy.name} ({academy.id})")
            notices.extend(template_notices)
            template_version = f"academy {academy.id}"
        row = self._repo.create(
            actor=actor, note=ctx.basis, doc_type=request.doc_type.value, course_id=ctx.curriculum.course_id,
            curriculum_id=ctx.curriculum_id, curriculum_revision=ctx.curriculum_revision, language=language,
            title=doc.title, basis=ctx.basis, template_version=template_version,
            generation_mode="llm" if used_llm else "deterministic", content=doc.model_dump(mode="json"),
            notices=list(dict.fromkeys(notices)))
        logger.info("Document %s generated (%s, %s, mode=%s)", row.id, request.doc_type.value, language, row.generation_mode)
        return _stored(row)

    # ------------------------------------------------------------------ academy templates
    @property
    def templates_enabled(self) -> bool:
        return self._templates is not None

    def _templates_required(self) -> DocumentTemplateRepository:
        if self._templates is None:
            raise DocumentError("Academy templates are not available.")
        return self._templates

    def upload_template(self, doc_type: DocType, language: str, name: str, filename: str, data: bytes, actor: str):
        """Read the outline of an academy document and store it as a PENDING template (not used until approved)."""
        repo = self._templates_required()
        if language not in ("en", "ar"):
            raise DocumentError("Template language must be en or ar.")
        if not (name or "").strip():
            raise DocumentError("Give the template a name (e.g. 'Academy participant guide 2026').")
        if not data or len(data) > MAX_TEMPLATE_BYTES:
            raise DocumentError("The file is empty or larger than 10 MB.")
        digest = hashlib.sha256(data).hexdigest()
        existing = repo.find_by_hash(digest)
        if existing is not None:
            raise DocumentError(f"This file was already uploaded as {existing.id} ({existing.status}).")
        try:
            headings = read_outline(filename, data)
        except TemplateError as exc:
            raise DocumentError(str(exc)) from exc
        row = repo.create(doc_type=doc_type.value, language=language, name=name.strip()[:200], filename=filename[:300],
                          sha256=digest, headings=headings, uploaded_by=(actor or "unknown")[:200])
        logger.info("Template %s uploaded for %s/%s (%d headings)", row.id, doc_type.value, language, len(headings))
        return row

    def review_template(self, template_id: str, approve: bool, reviewer: str):
        if not (reviewer or "").strip():
            raise DocumentError("Approving or rejecting a template needs the reviewer's name.")
        row = self._templates_required().review(template_id, approve=approve, reviewer=reviewer.strip())
        if row is None:
            raise DocumentError(f"No template with ID {template_id}.")
        return row

    def delete_template(self, template_id: str) -> None:
        if not self._templates_required().delete(template_id):
            raise DocumentError(f"No template with ID {template_id}.")

    def list_templates(self, **filters) -> list:
        return self._templates.list(**filters) if self._templates else []

    def active_template(self, doc_type: DocType, language: str):
        """The approved academy template for this type + language, or None (then the built-in template is used)."""
        return self._templates.active(doc_type.value, language) if self._templates else None

    # ------------------------------------------------------------------ read
    def get(self, document_id: str) -> StoredDocument | None:
        row = self._repo.get(document_id)
        return _stored(row) if row else None

    def list(self, **filters) -> list[StoredDocument]:
        return [_stored(r) for r in self._repo.list(**filters)]

    def _require(self, document_id: str) -> StoredDocument:
        doc = self.get(document_id)
        if doc is None:
            raise DocumentError(f"No document with ID {document_id}.")
        return doc

    # ------------------------------------------------------------------ review
    def edit_markdown(self, document_id: str, markdown: str, actor: str, note_text: str = "") -> StoredDocument:
        """Replace the body with the edited Markdown. New revision, back to draft."""
        stored = self._require(document_id)
        if len(markdown) > MAX_MARKDOWN_CHARS:
            raise DocumentError("The text is too long to save.")
        edited = from_markdown(markdown, stored.content)
        if not edited.sections or not any(s.blocks for s in edited.sections):
            raise DocumentError("The document would be empty. Keep at least one section with content (## Heading).")
        row = self._repo.save_content(document_id, title=edited.title, content=edited.model_dump(mode="json"),
                                      actor=actor or "unknown", note=note_text or "Edited text")
        return _stored(row)

    def approve(self, document_id: str, reviewer: str, note_text: str = "") -> StoredDocument:
        if not (reviewer or "").strip():
            raise DocumentError("Approval needs the reviewer's name.")
        stored = self._require(document_id)
        if stored.status == DocStatus.APPROVED:
            raise DocumentError(f"{stored.id} is already approved.")
        return _stored(self._repo.approve(document_id, reviewer.strip(), note_text))

    # ------------------------------------------------------------------ export
    def output_content(self, stored: StoredDocument) -> DocumentContent:
        """The document as printed: status line on the cover; DRAFT banner and reviewer notes until approved."""
        t = (lambda en, ar: ar if stored.language == "ar" else en)
        status = (t(f"approved by {stored.approved_by}", f"معتمد من {stored.approved_by}") if stored.status == DocStatus.APPROVED
                  else t("DRAFT: not approved", "مسودة: غير معتمدة"))
        doc = stored.content.model_copy(update={"meta": [*stored.content.meta, (t("Status", "الحالة"), f"{status} · {t('revision', 'المراجعة')} {stored.revision}")]})
        if stored.status != DocStatus.APPROVED:
            front = Section(heading=t("Review status", "حالة المراجعة"), blocks=[note(t(
                "This document is a DRAFT and has not been approved. Do not distribute it.", "هذا المستند مسودة ولم يُعتمد بعد. لا توزّعه."))])
            back = [Section(heading=t("Notes for the reviewer", "ملاحظات للمراجع"), blocks=[bullets(stored.notices)])] if stored.notices else []
            doc = doc.model_copy(update={"sections": [front, *doc.sections, *back]})
        return doc

    def pdf(self, document_id: str) -> bytes:
        return render_pdf(self.output_content(self._require(document_id)))

    def markdown(self, document_id: str) -> str:
        return to_markdown(self._require(document_id).content)

    def email(self, document_id: str, recipients: str | list[str], actor: str, message: str = "") -> int:
        """E-mail the PDF of an APPROVED document. The send is recorded in the log (count only, no addresses)."""
        stored = self._require(document_id)
        if stored.status != DocStatus.APPROVED:
            raise DocumentError("Only approved documents can be e-mailed. Approve it first.")
        if self._email is None:
            raise DocumentError("E-mail is not available.")
        body = (message.strip() + "\n\n" if message.strip() else "") + f"Attached: {stored.title} (approved by {stored.approved_by}).\nSent from REX AI."
        count = self._email.send(recipients, subject=stored.title, body=body,
                                 attachment=Attachment(f"{stored.id}.pdf", self.pdf(document_id)))
        self._repo.add_log(document_id, actor or "unknown", "emailed", f"{count} recipient(s)")
        return count
