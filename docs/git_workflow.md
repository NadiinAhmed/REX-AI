# Git Workflow — turning the Sprint 1 package into real branch history

> **Step-by-step instructions for each student are in the role guides:**
> [Role 1](ROLE_1_AI_CHATBOT_GUIDE.md) · [Role 2](ROLE_2_KNOWLEDGE_BASE_GUIDE.md) · [Role 3](ROLE_3_ARCHITECTURE_INTEGRATION_QA_GUIDE.md).
> This page is the short overview. Note: the repository's `main` already has an initial commit with
> `README.md` and empty `.gitignore` / `.env.example`; the Role 3 guide explains how to handle them.

The Sprint 1 code was prepared as one package. **Each student commits and pushes their own
files from their own machine and account**, so GitHub history reflects who integrated what.
Before committing, read your files, run your tests, and make sure you can explain them
(see `docs/team_responsibilities.md`).

File lists per PR: `docs/sprint1_handoff/pr*.txt`. Every file in the package belongs to exactly one list.

## Merge order (each step keeps `main` green)

```
PR1  feature/ui-architecture-qa  → main   Role 3: foundation + shared contract
PR2  feature/knowledge-base      → main   Role 2: model, data, retriever        (needs PR1)
PR3  feature/rex-assistant       → main   Role 1: LLM, workflows, assistant     (needs PR1+PR2)
PR4  feature/ui-architecture-qa  → main   Role 3: bootstrap, UI, integration, docs (needs all)
```

Review pairs: Role 1 reviews PR2, Role 2 reviews PR3, Role 1 or 2 reviews PR1 and PR4.

## 0. One-time setup (Role 3)

```bash
git clone https://github.com/<org>/<repo>.git rex-ai && cd rex-ai
git status                              # inspect first: is the repo empty? existing files?
git switch main && git pull
# If the three branches don't exist yet:
for b in feature/rex-assistant feature/knowledge-base feature/ui-architecture-qa; do
  git switch -c "$b" main && git push -u origin "$b"
done
git switch main
```
If `main` already contains files, compare them with the package before copying anything.
Never overwrite existing work without discussing it.

Then in GitHub → Settings → Branches, protect `main` (require PR, 1 approval, no force push)
if your permissions allow.

## Common step: copy only your files into your branch

Unzip the package next to your clone (e.g. `../rex-ai-sprint1/`). From inside your clone:

```bash
python ../rex-ai-sprint1/docs/sprint1_handoff/copy_files.py <MANIFEST_NAME>
git status          # should show ONLY the files in your list
```
The script works in Bash, Git Bash and PowerShell. It refuses to overwrite an existing file
with different content (exit code 2) so nobody's work is replaced silently.

## PR1 — Role 3 (foundation)

```bash
git fetch origin && git switch feature/ui-architecture-qa && git merge origin/main
python ../rex-ai-sprint1/docs/sprint1_handoff/copy_files.py pr1_role3_foundation
python -m pip install -r requirements.txt
python -m pytest                       # expected: config + registry tests pass
git add -A && git commit -m "Add project foundation: config, logging, agent contract, registry [ARC-01..05]"
git push
```
Open PR: `feature/ui-architecture-qa → main`. Review, then merge.

## PR2 — Role 2 (knowledge base)

```bash
git fetch origin && git switch feature/knowledge-base && git merge origin/main   # gets PR1
python ../rex-ai-sprint1/docs/sprint1_handoff/copy_files.py pr2_role2_knowledge_base
python -m pytest
git add -A
git commit -m "Add course model, synthetic dataset, loader and bilingual retriever [KB-01..06]"
git push
```
Open PR: `feature/knowledge-base → main`.

## PR3 — Role 1 (REX Assistant)

```bash
git fetch origin && git switch feature/rex-assistant && git merge origin/main    # gets PR1+PR2
python ../rex-ai-sprint1/docs/sprint1_handoff/copy_files.py pr3_role1_rex_assistant
python -m pytest
git add -A
git commit -m "Add REX Assistant: LLM interface, learner profile, recommendations, fallback [AI-01..05]"
git push
```
Open PR: `feature/rex-assistant → main`.

## PR4 — Role 3 (integration, UI, docs)

```bash
git fetch origin && git switch feature/ui-architecture-qa && git merge origin/main   # gets everything
python ../rex-ai-sprint1/docs/sprint1_handoff/copy_files.py pr4_role3_integration
python -m pytest                       # full suite
streamlit run app.py                   # manual acceptance check (README checklist)
git add -A
git commit -m "Add Streamlit UI, bootstrap, integration tests and Sprint 1 docs [ARC-06, QA-01..02, DOC-01..03]"
git push
```
Open PR: `feature/ui-architecture-qa → main`. After merge, tag the release:
```bash
git switch main && git pull && python -m pytest && git tag sprint-1 && git push origin sprint-1
```

Smaller commits are better than one big commit: you may split your list into several commits
(for example model first, then dataset, then retriever) as long as you only commit files you own.

## Keeping a branch up to date later

```bash
git fetch origin
git switch <your-branch>
git merge origin/main        # resolve conflicts keeping BOTH sides' intent; never discard a teammate's code
python -m pytest
git push
```

## Never
- commit directly to `main`, `git push --force` to a shared branch, or `git reset --hard` on shared history
- commit `.env` or API keys
- commit files from another role's list without that owner's agreement
- delete the feature branches after merging (they continue into Sprint 2)
