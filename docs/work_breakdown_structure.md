# Work Breakdown Structure — Sprint 1

```
1. REX AI Sprint 1 Foundation
├── 1.1 Project setup (Role 3)
│   ├── 1.1.1 Repository, branches, .gitignore, .env.example        [ARC-01]
│   ├── 1.1.2 Configuration + logging                                [ARC-02]
│   └── 1.1.3 PR template + branch protection (if permitted)         [ARC-03]
├── 1.2 Shared interfaces (all roles, led by Role 3)
│   ├── 1.2.1 Agent request/response contract                        [ARC-04]
│   ├── 1.2.2 Course schema agreement                                [KB-01]
│   └── 1.2.3 Retriever + LLM interface agreement                    [KB-02, AI-01]
├── 1.3 Knowledge base (Role 2)
│   ├── 1.3.1 Course model + validation                              [KB-01]
│   ├── 1.3.2 Synthetic, labelled demo dataset                       [KB-03]
│   ├── 1.3.3 Loader with readable errors                            [KB-04]
│   ├── 1.3.4 Bilingual keyword retriever + filters                  [KB-05]
│   └── 1.3.5 Data + retrieval tests                                 [KB-06]
├── 1.4 REX Assistant (Role 1)
│   ├── 1.4.1 LLM service interface + Anthropic provider             [AI-01]
│   ├── 1.4.2 Learner profile extraction (AR/EN)                     [AI-02]
│   ├── 1.4.3 Recommendation workflow + explanations                 [AI-03]
│   ├── 1.4.4 Conversation flow: follow-ups, course Q&A, fallback    [AI-04]
│   └── 1.4.5 Assistant tests                                        [AI-05]
├── 1.5 Integration & UI (Role 3)
│   ├── 1.5.1 Agent registry/router                                  [ARC-05]
│   ├── 1.5.2 Composition root + Streamlit UI                        [ARC-06]
│   ├── 1.5.3 Integration + UI tests                                 [QA-01]
│   └── 1.5.4 Acceptance checklist + test report                     [QA-02]
└── 1.6 Documentation (Role 3, inputs from all)
    ├── 1.6.1 README + setup guide                                   [DOC-01]
    ├── 1.6.2 Architecture diagram                                   [DOC-02]
    └── 1.6.3 WBS, responsibility matrix, task board                 [DOC-03]
```
