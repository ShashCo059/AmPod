# Agent Pod Projects

This repository contains multiple related projects for ambient clinical documentation and clinical decision support.

## Included Projects

- `ambient_scribe_s/` — ambient transcription and clinical summary workflow
- `Clinical_decision_support/` — clinical decision support agent and knowledge-base workflow

## Getting Started

1. Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   ```
2. Install project dependencies:
   ```bash
   pip install -r ambient_scribe_s/requirement.txt
   pip install -r Clinical_decision_support/requirements.txt
   ```
3. Run the apps as needed by following each project’s local instructions.

## Coding Agent Integration Pack

For sharing the medical coding functionality with an older Agent Pod version, see the integration guide:

- [CODING_AGENT_INTEGRATION.md](./CODING_AGENT_INTEGRATION.md)

## Collaboration Workflow

- Create a feature branch for each task
- Commit in small logical chunks
- Open pull requests for review before merging
- Keep secrets and local data out of Git

## GitHub Setup

```bash
git init
git add .
git commit -m "Initial project setup"
git branch -M main
git remote add origin <your-github-repo-url>
git push -u origin main
```

Replace the remote URL with your GitHub repository URL when ready.
