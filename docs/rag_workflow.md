# RAG workflow

```
PDF ─► validate ─► extract per page ─► clean ─► chunk ─► embed ─► Chroma vectors
            │            (pypdf)        │     (LangChain splitter)     │
            ▼                           ▼                              ▼
       reject early         remove running headers,        SQLite: documents + document_chunks
   (type, size, magic,      letter-spaced headings,        (source of truth: text, page, approval)
   encrypted, no text)      lines repeated on a page

question ─► embed ─► vector search (approved docs only) ─► min score ─► keyword gate ─► de-dup ─► passages + citations
```

## Steps
| Step | Class | Notes |
|---|---|---|
| Validation | `PdfValidator` | `.pdf` name, size ≤ `MAX_UPLOAD_MB`, `%PDF-` header, page limit, password-protected files rejected |
| Extraction | `PdfTextExtractor` | page numbers kept; a broken page produces a warning, not a failure; no text at all → `EmptyDocumentError` (scans need OCR, not in MVP) |
| Cleaning | `clean_page_text`, `remove_running_headers` | the academy portfolio repeats its header on every page and duplicates a whole block on page 8 — both removed |
| Chunking | `TextChunker` | LangChain `RecursiveCharacterTextSplitter`, `CHUNK_SIZE`/`CHUNK_OVERLAP`, page kept per chunk, exact duplicates dropped |
| Embedding | `HashingEmbeddings` / `SentenceTransformerEmbeddings` | both implement LangChain `Embeddings` |
| Storage | `ChromaVectorStore` | cosine space; one collection per embedding model |
| Retrieval | `PassageRetriever` | approved docs only, `RETRIEVAL_MIN_SCORE`, keyword gate, de-dup, diagnostics |
| Generation | `RexAssistant._answer_from_documents` | LLM gets numbered passages inside `<documents>` and must cite `[n]`; without LLM, cited excerpts are shown |

## Consistency between SQLite and the vector store
- Every vector id equals a `document_chunks.id`; chunk text lives only in SQLite.
- **Add:** row (`pending`) → chunks → vectors → `indexed`. Any failure deletes vectors, rows and the stored file.
- **Remove:** vectors first, then the row (chunks cascade), then the stored file.
- **Replace:** the new file is ingested first; the old one is removed only on success. Approval status carries over.
- **Approve/reject:** only the row changes. Search reads approved ids from SQLite on every query.
- **Change embedding model:** documents indexed with another model are re-embedded at start-up from their stored chunks.
- `python -m src.cli check` (or the Knowledge base page) reports any document whose chunks and vectors differ.

## Duplicates and trust
- Identical files are detected by SHA-256 and rejected (the two uploaded portfolio PDFs are byte-identical).
- Uploaded PDFs start as **pending** and are never searched until approved. PDFs committed to `data/documents/` were reviewed by the team and are indexed as approved on first start.
- Document text is untrusted data: it is placed inside `<documents>` tags with an instruction to ignore instructions inside it.

## Embeddings: choosing a provider
| | `hashing` (default) | `sentence_transformers` |
|---|---|---|
| Needs download / internet | no | yes, once (~hundreds of MB) |
| Understands meaning | no — matches spelling | yes, multilingual |
| Arabic question → English PDF | via the project's synonym bridge only | native |
| Keyword gate (`auto`) | on | off |

**Measured reason for the keyword gate:** with hashing, an unanswerable "Do you offer scholarships?" scored 0.266 while the correct "Who can I contact?" match scored 0.269 — no threshold separates them. The gate requires a distinctive question word in the passage. Effect measured in `docs/evaluation.md`.

## Semantic course search (COURSE_RETRIEVER=semantic)

REX has two kinds of knowledge, and each gets the search that fits it:

| Knowledge | Size | Search |
|---|---|---|
| Course catalog (approved courses) | tens of records | `SemanticCourseRetriever`: one embedding per course, compared in memory |
| Approved PDFs | thousands of chunks | `PassageRetriever`: Chroma vector store |

`SemanticCourseRetriever` (`src/knowledge_base/semantic_retriever.py`) implements the same
`CourseRetriever` contract as `KeywordRetriever`, so REX, Marketing and RFP need no changes.

1. **Course card.** Each course becomes one text (title, Arabic title, description, keywords,
   career paths, modules, tools, outcomes, audience). Fees and dates are left out: they are
   filters, not topics.
2. **Embed once.** Cards are embedded at start-up with the same sentence-transformer that indexes
   the PDFs (one model in memory, not two).
3. **Query = the learner's own sentence.** `LearnerProfile.topic_text` keeps the phrase
   ("make websites that look nice"); the keyword bag ("web nice") loses its meaning.
4. **Hybrid decision.** A course is kept if cosine similarity >= `COURSE_SEMANTIC_MIN_SCORE`, or if
   the exact-word retriever finds a strong match (title, keywords, career path). Exact matches get
   a small bonus (+0.05) to break ties.
5. **Explanations.** A course found by exact words shows "Covers your interest: ...". A course
   found only by meaning shows "Closely related to what you described (...)".

Fallbacks (the sidebar shows a warning): RAG disabled, `EMBEDDING_PROVIDER=hashing` (it matches
spelling, so calling it semantic would be misleading) or a model load error -> keyword search.
`COURSE_BACKEND=mcp` still uses the MCP server's keyword search (future work).

**Threshold.** Multilingual E5 models give even unrelated texts fairly high similarity, so the
threshold must be measured per model: `python -m evaluation.courses.run_course_eval
--retriever semantic --label <name> --scan`. The default 0.80 is a placeholder until then.
