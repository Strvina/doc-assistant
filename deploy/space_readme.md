---
title: Doc Assistant
emoji: 📄
colorFrom: blue
colorTo: yellow
sdk: docker
app_port: 7860
pinned: false
short_description: Answers from company documents with page-level citations
---

# Doc Assistant — live demo

Internal AI assistant that answers only from company documents (Serbian, German, English) and cites
the document and page for every claim. Demo data is a **fictional** company.

Try a question from the examples, click a source to open the original page with the passage
highlighted, or switch to **Agent** mode. Answers come from free-tier models (Groq, with Gemini
as a fallback), so a short wait or a per-session question limit is expected.

Source code, architecture and evaluation: https://github.com/Strvina/doc-assistant
