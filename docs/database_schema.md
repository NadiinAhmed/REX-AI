# Shared database schema

SQLite via SQLAlchemy 2, migrations with Alembic (`migrations/versions/0001_initial_shared_schema.py`).
The app migrates automatically at start-up; manually: `alembic upgrade head`.

**Why SQLite:** no server, one file, enough for a single-process Streamlit MVP and a three-person team.
Moving to PostgreSQL is a `DATABASE_URL` change (the models use portable types).

```mermaid
erDiagram
    catalog_meta ||--o{ courses : "describes loaded dataset"
    documents ||--o{ document_chunks : "cascade delete"
    courses {
        string course_id PK
        string title
        string department
        string skill_level
        string delivery_format
        bool approved
        json data "full validated Course record"
    }
    documents {
        string id PK
        string filename
        string sha256 UK "duplicate detection"
        string approval_status "pending|approved|rejected"
        string index_status "pending|indexed|failed"
        string embedding_model
    }
    document_chunks {
        string id PK "= vector id"
        string document_id FK
        int page
        int chunk_index
        text text
    }
    leads {
        string id PK
        string name
        string email
        string phone
        json interested_course_ids
        datetime consent_given_at
        string consent_version
    }
    evaluation_runs {
        string id PK
        json config
        json summary
        string results_path
    }
```

| Table | Owner today | Future users |
|---|---|---|
| `courses`, `catalog_meta` | Agent 1 | Curriculum, Document Generation, Marketing (read) |
| `documents`, `document_chunks` | knowledge base | Curriculum (standards), Document Generation (templates) |
| `leads` | Agent 1 (only after consent) | Marketing follow-up (human-approved) |
| `evaluation_runs` | evaluation | all agents' evaluations |

Planned (added by migration when each agent starts, referencing `courses.course_id`): `curricula`,
`generated_documents`, `rfp_opportunities`, `marketing_contents` (with `approval_status`).

**Rule:** only `src/db/repositories.py` reads or writes these tables.

## Agent 5 tables (migration `0003_marketing_and_offers`)

```mermaid
erDiagram
    marketing_campaigns ||--o{ marketing_drafts : "cascade delete"
    course_offers {
        string id PK
        string course_id "no hard FK (see below)"
        float discount_percent "exactly one of percent / price"
        float price_amount
        string currency
        datetime valid_from
        datetime valid_until
        string approval_status "pending|approved|rejected"
        string approved_by
    }
    marketing_campaigns {
        string id PK
        string course_id "no hard FK"
        json brief
        json strategy
        json facts_snapshot "exactly what the generator could use"
        string generator
    }
    marketing_drafts {
        string id PK
        string campaign_id FK
        string platform
        string language
        json content
        json claim_issues
        json evaluation
        string approval_status "pending_review|approved|rejected"
        string reviewed_by
        datetime reviewed_at
    }
```

- `course_offers` is **shared**: REX Assistant (follow-up PR), the Marketing Agent and the MCP tool
  `get_active_offers` read it through `OfferService`, which returns only approved offers valid today.
  `courses.promotions` (free text) is never used as an offer.
- **No hard foreign key on `course_id`**: `CourseRepository.replace_catalog()` deletes and re-inserts all
  courses, which a FK would block or cascade-delete. Services validate the ids (same as `leads`).
- Downgrade to `0002` drops only these three tables; the RFP table stays (tested).
