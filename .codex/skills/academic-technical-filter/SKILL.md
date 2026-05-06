---
name: academic-technical-filter
description: Use when an agent must decide whether a link, newsletter item, Miner queue row, or archive candidate belongs in Jiphyeonjeon academic-search/technical-report collection. Applies to 집현전-광부 intake, 집현전-클로 review, newsletter raw archive, and 뉴스레타 아카이브 cleanup; reject jobs, social/admin notifications, analytics, generic market/career content, and non-technical topics.
---

# Academic / Technical Filter

Use this skill before admitting a collected link into review queues, approved exports, raw newsletter archives, or source-link archive posts.

## Verdicts

Return one of:

- `eligible`: academic-search or technical-report evidence is present.
- `reject`: content is outside scope or is an operational/social/admin notification.
- `needs_review`: public evidence is too weak for automatic inclusion; hold for 집현전-클로 with an explicit reason.

## Eligible content

Accept when at least one strong public signal is present:

- Academic/search sources: arXiv, DOI, OpenReview, Semantic Scholar, ACL Anthology, PMLR, NeurIPS/ICML pages, Papers with Code.
- Technical reports/posts: AI lab research/engineering blogs, developer/engineering reports, open-source technical repositories, technical benchmark/evaluation writeups.
- Technical substance in title/summary/url: RAG, retrieval, knowledge graph, LLM/agent, model, machine learning, benchmark/evaluation, inference/serving, GPU/CUDA, multimodal/vision, security/privacy evaluation, API/framework/library architecture.
- Technical-industry case studies are allowed when they describe method, system, model, evaluation, architecture, or engineering workflow. Example: Meta REA remains eligible.

## Reject content

Reject even if a newsletter supplied it:

- Jobs, hiring, career ladder, recruiting, job alerts.
- Social/profile/feed notifications, impressions, analytics, weekly stats, profile views, post views.
- Account/admin/security/login/preferences/unsubscribe/terms/privacy/settings notices.
- Workspace notifications such as Notion page updates.
- Generic market, pricing, funding, partnership, product-growth, or career content without method/system/evaluation evidence.
- Broad homepage/profile links without a technical article title or summary.

## Agent output format

When asked to classify, answer in compact JSON:

```json
{"verdict":"eligible|reject|needs_review","bucket":"academic_search|technical_report|out_of_scope","reason":"short reason code","evidence":["public signal 1","public signal 2"]}
```

Do not use private email body text, tokens, OAuth secrets, Discord tokens, relay tokens, or mailbox-only snippets as evidence.
