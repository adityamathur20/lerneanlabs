# GST Notice Reply-as-a-Service: Technical, UX & Rollout Implementation Research

## TL;DR
- **Build the RAG on your existing Supabase using pgvector + native Postgres BM25 (hybrid search with Reciprocal Rank Fusion), embed with Voyage voyage-3.5-lite (or OpenAI text-embedding-3-small) at $0.02/1M tokens, and your total monthly infrastructure at 50–200 notices/month lands around $32–49 — comfortably inside $100.** Do NOT add a dedicated vector DB or an MCP server for launch; both are premature for a solo operator with a 2-day runway.
- **For the June 21 alpha, ship the narrowest credible slice: manual PDF upload → notice classification → law retrieval → reply draft → human review/edit/export, for ONE or two notice types (DRC-01/ASMT-10), CA-targeted, per-notice/credit pricing.** Defer auto-fetch/portal-sync, multi-client dashboards, and WhatsApp/email automation — this matches exactly how every Indian competitor actually sequenced its build.
- **Deploy Flask + n8n on a single Hetzner VPS (~$5/mo) with Docker, and move the hardcoded Supabase service_role JWT into environment variables / n8n external secrets immediately.** That credential leak is your single most urgent pre-launch fix; self-host n8n rather than pay n8n Cloud.

## Key Findings

### 1. The cost math overwhelmingly favors your existing stack
Your instinct to stay dependency-light is correct and cost-optimal. pgvector on the Supabase you already run is free; a 15,000–25,000 document legal corpus is trivial for it (pgvector HNSW performs well up to ~5–10 million vectors on a Supabase Pro instance, keeping query latency under 10ms at p99 for datasets up to ~5 million vectors — orders of magnitude more than you need). One-time embedding of the corpus is effectively free at this scale. Claude API is your only meaningful variable cost, and even at 200 notices/month it is small. A dedicated vector DB (Pinecone/Qdrant/Weaviate) adds cost, a second system to sync, and zero benefit at your scale.

### 2. MCP is the wrong tool for launch — use a direct in-process RAG call
The 2026 consensus is clear: RAG is for retrieving stable, unstructured knowledge (your statute/circular/case-law corpus); MCP is for giving agents live, structured tool access (actions). Your core need — "given this notice, fetch relevant law and draft a reply" — is textbook RAG. An MCP server adds a protocol layer, latency, and a security surface you don't need when you control the whole application. Build RAG retrieval directly in your Flask code.

### 3. The winning UX pattern is a 3-pane, single-CTA, status-driven workflow
Across NoticeAI, Canopy, and ClearTax, the same patterns recur: notices organized as "cases" (like an email inbox), a strong status/deadline badge system, progressive disclosure of legal complexity, and one clear primary action per screen. ClearTax explicitly organizes by open/overdue/time-critical/closed; Canopy uses pre-built resolution templates that auto-populate forms. A solo dev can replicate the highest-value 80% with a simple list view + detail view + draft editor.

### 4. Every competitor launched AI-drafting-first and deferred auto-fetch
The single clearest finding from the competitor teardown: NabsAI, Quick Litigate, gstreplyai, and gstnoticeai all shipped manual-upload + AI-drafting as the MVP core. Portal auto-fetch/sync is universally the LAST feature — explicitly "coming soon" at Quick Litigate, absent at both GST-AI sites, and only working at NabsAI (the oldest/most mature). This validates deferring your blocked WhiteBooks live-fetch (blocked until May 14, 2026 anyway) and your orphaned cancelled-GSTIN check.

### 5. Self-hosted n8n on a cheap VPS is the right deployment
n8n Cloud starts at €20–24/mo with execution caps that a webhook-heavy automation burns through fast. A Hetzner VPS at ~$4.51–5/mo with Docker Compose runs Flask + n8n + their Postgres needs with unlimited executions, well under budget.

## Details

### AREA 1 — Low-cost RAG infrastructure under $100/month

**Vector database — use pgvector on your existing Supabase. (Verdict: decided.)**
- pgvector is included free on all Supabase plans (Free and the $25/mo Pro), storing vectors alongside your existing 5 tables — no sync complexity, no second system, no new SDK (you can hit it through your existing hand-written REST/RPC wrapper or raw SQL).
- Capacity: pgvector with an HNSW index performs well up to ~5–10M vectors on a Pro instance; your 15,000–25,000 legal documents (even chunked 5–10× into ~150,000–250,000 chunks) is a tiny fraction of that. In Supabase's own published benchmark ("pgvector vs Pinecone: cost and performance"), at matched 0.98 accuracy@10 the pgvector HNSW index managed **1,185% more queries per second while being $70/month cheaper** than a same-budget Pinecone setup (a single ~$410/mo 2XL instance vs ~$480/mo on Pinecone).
- Free vs Pro decision: Supabase Free gives 500MB database, 50,000 MAU, 5GB egress, but **pauses after 7 days of inactivity** and has no backups. For a real product handling client tax data, budget the **$25/mo Pro** plan (removes the pause, adds 8GB DB, daily backups, 100K MAU). This is your single biggest fixed cost.
- Dedicated alternatives, if you ever outgrow pgvector: Qdrant Cloud (~$9/mo entry, lowest latency in benchmarks ~4ms p50), Redis Cloud (~$7/mo), Chroma (free/self-host). None justified at your scale now.

**Embedding model — Voyage voyage-3.5-lite (or OpenAI text-embedding-3-small).**
- Pricing (verified June 2026): OpenAI text-embedding-3-small and Voyage voyage-3.5-lite (and voyage-4-lite) all tie at **$0.02 per 1M tokens** (input-only, no output cost). OpenAI's Batch API halves this to $0.01/1M. Google's text-embedding-005 is cheapest at ~$0.006/1M if you want to minimize further.
- Quality for legal text: Voyage's domain models (voyage-law-2, voyage-3-large) measurably lead on legal/financial retrieval by 4–6 MTEB points, and Anthropic recommends Voyage. voyage-law-2 has a 50M-token free tier; voyage-3.5/voyage-4 models carry a 200M-token free tier. Given you hand-roll over urllib, either provider is a simple POST; pick **voyage-3.5-lite** ($0.02, 200M free tokens) for the price/quality balance, or **voyage-law-2** for maximum legal-domain accuracy. OpenAI's text-embedding-3-small is the safe "good enough and easy" fallback.
- One-time corpus embedding cost: ~20,000 documents averaging ~2,000 tokens (legal docs run long) = ~40M tokens. At $0.02/1M = **$0.80 one-time** — and likely $0 because both Voyage and OpenAI free tiers cover it. Even at 250,000 chunks × 500 tokens = 125M tokens, that's ~$2.50, mostly free-tier-covered.

**OCR — Tesseract self-hosted first; Google Cloud Vision as cheap paid fallback.**
- Tesseract is free/open-source and fine for clean, digital-born or good-quality scanned PDFs, but its accuracy collapses on handwriting/poor scans (benchmarks cite ~20–40% on handwriting).
- Google Cloud Vision: 1,000 free pages/month, then $1.50/1,000 pages — at 50–200 notices/month you're effectively free or paying cents. AWS Textract is $1.50/1,000 for basic detection too, but $15/1,000 for forms/tables.
- Recommendation: run Tesseract self-hosted on your VPS for digital PDFs (most GST portal notices are digital-born), and route only failed/low-confidence or scanned docs to Google Cloud Vision. Note Quick Litigate uses AI-powered OCR for scanned Hindi+English notices — Hindi support matters; Google Vision handles 50+ languages including Hindi, and Tesseract needs the Hindi language pack installed.

**Realistic monthly cost breakdown (50–200 notices/month):**
- Supabase Pro: $25 (or $0 on Free with inactivity-pause risk)
- Hetzner VPS (Flask + n8n + Tesseract): ~$5
- Embeddings (query-time, tiny): ~$0–1
- OCR: ~$0–3
- Claude API: ~$2–20 (see below)
- **Total: roughly $32–49/month**, leaving headroom inside $100.

**Claude API cost (verified June 2026 against Anthropic's official pricing page):** Haiku 4.5 = **$1/$5** per 1M input/output; Sonnet 4.6 = **$3/$15**. Prompt caching bills cache hits at ~10% of the base input rate (**~90% savings**; first write costs 1.25× input for a 5-min TTL or 2.0× for a 1-hour TTL), and the Batch API is 50% off. Routing classification/extraction to Haiku and drafting to Sonnet: a notice reply might use ~15K input + 3K output on Sonnet ≈ $0.09, plus Haiku classification ~$0.01. At 200 notices that's ~$20/month before caching, materially less with it. Your stated models (claude-haiku-4-5, claude-sonnet-4-6) are current and correctly chosen. (Note: Anthropic's current flagship is Opus 4.8 at $5/$25, released May 28, 2026 — relevant only if you ever need top-tier reasoning for edge-case drafting.)

### AREA 2 — RAG architecture & pipeline (practical)

**Chunking for legal/statutory text:** Chunk by structural unit (section/sub-section/rule/clause), NOT naive fixed-size, because statutory meaning lives at the section boundary. The current best-practice pattern from legal-RAG research (LegalBench-RAG; the 2026 "Reliable Retrieval in RAG for Large Legal Datasets" paper) is hierarchical/small-to-big: embed at a fine grain (sentence or sub-section) for precise matching, then retrieve the enclosing section/paragraph for context to send to the LLM. Preserve metadata on every chunk: Act name, section number, circular/notification number and date, court/bench for case law. This metadata is what enables accurate citations and filtering.

**Retrieval — hybrid search + rerank, all inside Postgres:** Dense vector search alone misses exact statutory references ("Section 73", "DRC-01", specific notification numbers). The proven fix is hybrid: combine pgvector cosine similarity with Postgres BM25/full-text (tsvector + ts_rank_cd, or the pg_search / VectorChord-BM25 extension), then fuse with Reciprocal Rank Fusion (RRF). This is implementable in ~100 lines of SQL + Python with no new infrastructure. Add a reranking step on the top candidates — either Voyage's reranker (rerank-2.5, 200M free tokens) or an LLM-rubric rerank via Haiku — which legal-RAG benchmarks show is where systems become production-grade (it catches the "topically similar but wrong section" false positives, e.g., a warranty period matching a limitation-period query).

**Structure as a multi-step pipeline, not single-shot RAG:**
1. **Classify** (Haiku): notice type (ASMT-10/DRC-01/etc.), section invoked, demand amount, period, deadline, allegations. You already do Haiku JSON extraction — extend it.
2. **Retrieve** (hybrid search): query the corpus using the extracted issues + section numbers, not the raw notice text.
3. **Rerank**: trim to the most relevant statutory/circular/case-law chunks.
4. **Draft** (Sonnet): generate the structured reply (facts → applicable sections → arguments → citations → conclusion) grounded only in retrieved chunks.
5. **Verify**: a cheap Haiku pass checking every cited section/case actually appears in retrieved context (anti-hallucination guard — critical given the legal-hallucination risk documented in the literature).

**Frameworks — stay lean, skip LangChain/LlamaIndex.** Your hand-rolled, dependency-light style is the right call. For a Python/Flask/Supabase stack, you need only: your existing urllib-based API callers, raw SQL for pgvector+BM25, and ~200 lines of orchestration. LangChain/LlamaIndex add heavy abstractions and dependencies for capabilities you can write directly. If you ever want a thin helper, the embedding/rerank calls are simple POSTs.

### AREA 3 — MCP vs RAG

**Verdict: build a direct RAG retrieval call in your app; do NOT build an MCP server for launch.** The 2026 best-practice framing: RAG = "what do we know" (stable knowledge retrieval); MCP = "what's happening / take an action" (live systems, tools). Your GST corpus is stable, unstructured knowledge — pure RAG. MCP's value is interoperability (one server usable by Claude Desktop, Cursor, n8n, etc.) and live actions; you have neither requirement at launch, and you control the full app, so a direct in-process call is faster, cheaper per query (~100–500ms vs added tool-call latency), and has a smaller security surface. Security analyses explicitly recommend RAG over MCP for public-facing/untrusted-user apps — which is exactly your SME/CA product.

**Existing open-source MCP servers** do exist and could be adapted later (mcp-rag-server, the RAG Documentation MCP Server, legal-specific servers like the Turkish Yargı server), and an MCP server *can* wrap a RAG pipeline. A custom GST MCP server is a reasonable few-days task IF you later need it (e.g., to let CAs query the corpus from Claude Desktop, or to expose "draft reply" as an agent tool). But for the June 21 alpha it's scope creep. Build RAG now; consider wrapping it as an MCP tool in month 2+ if a concrete integration need appears.

### AREA 4 — UI/UX teardown & principles

**NabsAI/NoticeAI workflow:** A 5-step linear flow — Upload notice (PDF/scanned) → AI analysis & classification (auto-detects IT/GST/TDS, extracts metadata, identifies provisions) → Draft generation (full structured reply with facts/sections/citations/conclusion in seconds) → Review & customize (edit tone/formatting, add comments) → Submit & track (status in a unified dashboard). Core UX strengths: a single unified multi-client dashboard filterable by notice type/due date/status; automatic data masking (PAN/GSTIN) shown as a trust signal; "what took 3–4 hours now takes under 30 minutes" framing. It is CA-targeted and module-organized (IT/GST/TDS as separate paid modules).

**Canopy "Notices" / Tax Resolution module:** Pre-built workflow templates guide users step-by-step through resolution per notice type; client surveys gather required financial info that auto-populates IRS forms; once a proposal/engagement is accepted the system auto-creates the associated projects and tasks. Transferable U.S. patterns: (a) template-driven resolution flows that match each notice type to a guided checklist, (b) auto-population of structured forms from prior data, (c) task/handoff automation triggered by status change, (d) a client portal for document collection. Caveat from reviews: Canopy's depth creates a learning curve — a warning to keep your alpha simple.

**ClearTax Notice Management / Compliance Cloud:** Notices are organized as "cases," with a PAN-level view and a GSTIN-level view where "cases are organized like emails." Each case shows reference ID, form name, case source (Additional Notices vs Notices & Orders), case type (scrutiny/demand/informational), and status (pending on you / pending on dept / closed). Users toggle between open / overdue / time-critical / closed and filter by reference ID or form name. This inbox-with-status-filters model is the single most copyable pattern for a solo dev.

**5–7 concrete UI/UX principles a solo dev can implement:**
1. **Inbox/case model with status filters.** A single list view of notices as "cases," with tabs/filters for Open · Overdue · Time-critical · Closed (ClearTax's exact model). Trivial in Flask + a table.
2. **Deadline-first status badges.** Color-coded badge per row showing days-to-deadline and whose-court-it's-in (You / Dept / Filed). Deadlines are the user's #1 anxiety — make them the most prominent visual element.
3. **Single clear CTA per screen.** List view → one button ("Draft Reply"); detail view → one primary action ("Generate" / "Submit"). Avoid Canopy's menu-maze.
4. **Three-pane case detail.** Left: extracted notice metadata (type, section, amount, deadline). Center: the editable draft reply. Right: the cited sources/case law (so the CA can verify citations — directly addresses trust/hallucination).
5. **Progressive disclosure of legal complexity.** Show the clean draft by default; put statutory analysis, alternative grounds, and full citations behind expandable sections ("Show legal reasoning"). Matches how Quick Litigate surfaces "4–6 probable grounds" on demand.
6. **Human-in-the-loop, review-before-submit.** Always present AI output as an editable draft with a visible disclaimer ("Draft only — review with your CA before submission"), as every competitor does. This is both UX and liability protection.
7. **Trust signals inline.** Show data-masking status, "citations verified against corpus," and per-citation source links. In a tax-compliance product where errors have financial/legal consequences, visible trust mechanics drive adoption.

Implementation: a server-rendered Flask app with a light frontend (HTMX or Alpine.js + Tailwind, or a minimal React SPA) achieves all seven without a design team. Avoid building a custom design system.

### AREA 5 — Staggered feature rollout for a solo operator

**Minimum viable alpha (ship June 21):** Manual PDF/image upload → OCR (Tesseract) → classify + extract (Haiku) → hybrid RAG retrieval → Sonnet draft → human review/edit → export (PDF/copy). Scope to **one or two high-volume notice types** (DRC-01 and/or ASMT-10 — the most common, per competitor focus). CA-targeted. Per-notice or credit pricing (lowest friction, fastest to validate). Auth + a basic case list. That's it.

**Explicitly defer:**
- *Week 2:* Multi-client dashboard/case-list polish; status filters; deadline tracking; second/third notice types.
- *Week 3:* Email/PDF delivery; saved templates; citation-source side panel.
- *Month 2:* Additional notice types to cover the 15+ landscape; WhatsApp alerts (your Meta Cloud API stub); reranker upgrade; MCP wrapper if an integration need appears.
- *Month 3+ / gated on the May 14, 2026 sandbox unblock:* WhiteBooks GSTR-2B live fetch; portal auto-fetch/sync; cancelled-GSTIN auto-detection (wire up the orphaned `check_gstin_status()`); the built-but-unwired n8n session-check branch.

**Frameworks & rationale:** The solo-founder consensus is to ship a focused MVP fast and exactly four feature categories (auth/account/payment + the one core value feature), using **manual-first-then-automate** ("simulation is often smarter than automation in an MVP" — do things manually behind the scenes before building). The canonical proof point is Buffer: founder Joel Gascoigne built "the simplest possible version in 7 weeks," launched November 30, 2010, and "had the first paying customer within four days of launch" (reaching 100 signups and 3 paying customers in the first month). The "if you're not embarrassed by the first version of your product, you've launched too late" maxim is from LinkedIn co-founder Reid Hoffman. For a product where errors carry financial/legal consequences, layer in **feature-flag-driven progressive rollout** (Flagsmith/GitLab-style: internal → friendly beta CAs → wider) and **cohort gating** — start with a handful of friendly CAs you can support directly, expand only after the drafts hold up. Keep the human-in-the-loop disclaimer permanently; never auto-submit to the portal.

**How competitors actually sequenced (from teardown + dedicated competitor research):**
- **NabsAI/NoticeAI** (NABS AI Solutions Pvt Ltd, New Delhi; corporate founding 2022; current product-site artifacts date to ~Sept 2025): launched CA-targeted, all three domains (IT/GST/TDS) as modules, subscription/per-module pricing only (list ₹12,000–21,000/module/yr across Silver/Gold/Platinum; ~₹7,200–12,600/module discounted when bundling all three; 7-day free trial; +₹50/extra client/yr). Most mature; the only one with working portal auto-fetch.
- **Quick Litigate** (Amjix Audit IQ Softwares Pvt Ltd; site artifacts ~Jan 2026; "Powered by Gemini 3 Pro/OpenAI/Perplexity"): GST + Income Tax, CA-leaning but hybrid. Free trial 3 credits; Standard ₹2,999/yr for 40 credits; **Pro "coming soon" = auto-fetch + advanced RAG** (the clearest proof auto-fetch is the last feature). Launched with upload + AI drafting + a legal-research assistant.
- **gstreplyai.com** ("GST Notice AI," © 2025, "Powered by Claude AI"): **SME/business-direct first** (plain-language input, Hindi+English), GST-only. First reply free, then ₹299/reply; **later added ₹999/month unlimited** ("best for CA firms"). Expanded **9 → 12 notice types** (added the high-volume DRC-01B/01C auto-notices). CA partner/referral program (₹90/reply, ₹300/subscription, 30% commission).
- **gstnoticeai.com** ("GST Notice AI," © 2026, by Vetrivel Appverse, Mumbai): newest, barely past MVP ("5+ firms, 16+ notices processed"; payment gateway "in progress"). CA-firm-targeted, GST-only, 8 notice types. First 3 notices free then ₹199/notice; **credit packs "coming soon"** (10/₹1,799 → 100/₹9,999). Features tagged "NEW" (risk predictor, precedent DB, hearing calendar) show analytics added AFTER core drafting + multi-client dashboard.
- **Cross-cutting pattern:** all launched manual-upload + AI-drafting as the MVP; auto-fetch is universally last; lightweight tools started per-notice/credit (₹199–299, first few free) and added subscriptions later; CA-first targeting dominates, with gstreplyai's SME-direct + CA-referral channel the notable exception.

(Caveat: no independent primary launch evidence — Product Hunt, press, dated social posts — exists for any of the four; dates above are inferred from website artifacts, copyright years, AI-model references, and early-stage usage counters, and should be treated as estimates.)

**Strategic implication for you:** The market is crowded but fragmented, every player is young (mostly late-2025/2026), and none has a defensible moat — the proprietary notice+reply dataset you can accumulate IS the moat. Launch narrow (one notice type, CA-targeted, per-notice pricing to match gstnoticeai/gstreplyai's friction-minimizing model), and instrument everything to capture the notice+reply+edit triples from day one.

### AREA 6 — Deployment under $100/month

**Hosting — single Hetzner VPS with Docker (~$4.51–5/mo).** Run Flask + n8n + their supporting Postgres/Redis on one Hetzner CX22 (€4.51/mo) or CAX11 (€3.29/mo, 4GB RAM). n8n needs ≥1GB RAM for production and persistent storage, which a VPS gives you. This is the cheapest reliable option; the tradeoff is you're the sysadmin (Docker, TLS via Let's Encrypt/Caddy, backups, updates). Middle-ground alternatives if you'd rather not manage the box: Coolify on a Hetzner VPS (~$5–8/mo, Heroku-like dashboard, auto-SSL), or managed n8n pods (PikaPods ~$3.80/mo, InstaPods ~$3/mo). Railway (~$5/mo usage-based) and Render ($25/mo web + ~$19 Postgres) are pricier or less predictable; Render also spins down on inactivity, which breaks scheduled triggers.

**Self-host n8n, don't pay n8n Cloud.** n8n Cloud is €20–24/mo (Starter) with a 2,500-execution cap that webhook-heavy automation exhausts in days. Self-hosted Community Edition is free with unlimited executions; you pay only for the ~$5 server. Verdict: self-host on the same VPS as Flask.

**Security hardening — your most urgent pre-launch work:**
1. **Remove the hardcoded Supabase service_role JWT from the n8n workflow JSON immediately.** The service_role key bypasses Row-Level Security — leaking it exposes all client tax data. Move it to an environment variable referenced via n8n's `$env` expression (set in the Docker `.env` / compose file), or use n8n's external-secrets feature. **Rotate the key after removal — assume it is already compromised.**
2. **Secrets management on a budget:** Cheapest/simplest is Docker `.env` files with strict file permissions + n8n environment-variable references (free, adequate for solo). If you want a real vault, **self-host Infisical** (MIT-licensed, free, Docker; n8n supports it natively as an external-secrets provider from v2.26.0 via a Universal Auth machine identity) — deployable one-click on Railway or your VPS. Doppler's free tier (up to 3 users) is the easiest managed option. Avoid HashiCorp Vault (operational overhead) at this stage.
3. **Other quick wins:** enable Supabase Row-Level Security on all tables (Free tier supports it); use the service_role key ONLY server-side (never in n8n nodes reachable from the browser); put n8n behind basic auth + HTTPS; enable Supabase daily backups (Pro plan); and add explicit Postgres grants before the Oct 30, 2026 PostgREST change if you use the auto-generated REST API.

## Recommendations

**Today → June 21 (alpha):**
1. **Fix the credential leak first.** Pull the service_role JWT out of the n8n JSON into env vars; rotate it. Non-negotiable before any external user touches the system.
2. **Stand up the RAG on pgvector in your existing Supabase.** Chunk the corpus by statutory section with full metadata; embed with voyage-3.5-lite or voyage-law-2 (free tier covers the one-time embed); implement hybrid search (pgvector + Postgres BM25 + RRF) in raw SQL.
3. **Build the 5-step pipeline** (classify → retrieve → rerank → draft → verify) directly in Flask, reusing your urllib Anthropic callers. Haiku for classify/verify, Sonnet for draft, prompt-cache the system/statute prefix (~90% off cached input).
4. **Ship the narrowest UI:** upload → case detail (3-pane) → editable draft with citation panel → export. One or two notice types (DRC-01/ASMT-10). CA-targeted. Per-notice/credit pricing. Permanent "review before submitting" disclaimer.
5. **Deploy Flask + n8n on one Hetzner VPS** with Docker, Caddy for TLS, Tesseract for OCR, Google Vision as fallback. Keep Supabase Pro ($25/mo) to avoid the inactivity pause and get backups.

**Week 2–3:** Polish the case list with status filters and deadline badges; add email/PDF delivery; add 1–2 more notice types; recruit 3–5 friendly CAs as a gated cohort; instrument capture of every notice+draft+human-edit triple (your moat).

**Month 2–3:** Expand notice-type coverage toward the 15+ landscape; add WhatsApp alerts; upgrade reranking; consider an MCP wrapper only if a concrete integration (e.g., Claude Desktop for CAs) is requested. Gate all live-portal features (GSTR-2B fetch, auto-fetch, cancelled-GSTIN detection) on the May 14, 2026 WhiteBooks sandbox unblock.

**Thresholds that change the plan:**
- If query latency or recall degrades past ~250K chunks, or you need <5ms p50 → consider Qdrant (~$9/mo), still inside budget.
- If Claude spend exceeds ~$40/mo → lean harder on prompt caching and route more to Haiku before changing models.
- If you exceed 50K MAU or 8GB DB → that's a real upgrade signal (and a good problem).
- If a paying customer demands portal auto-fetch → that's when auto-fetch graduates from "deferred" to "next," not before.

## Caveats
- Competitor launch dates are **estimates inferred from website artifacts** (upload-folder dates, copyright years, AI-model references, usage counters), not dated primary announcements — no Product Hunt/press/social launch evidence exists publicly for these small, very new products. Treat the chronology as directional.
- Several competitor "Pro"/auto-fetch features are advertised as "coming soon" — these are forward-looking claims, not shipped capabilities.
- All pricing figures are as of the June 2026 sources cited; AI-model and infrastructure pricing changes frequently — re-verify before committing.
- Supabase Free tier's 7-day inactivity pause makes it unsuitable for a live product; the $25/mo Pro plan is effectively mandatory, which is your largest fixed cost.
- OCR accuracy on poor scans/handwriting is a known weak point for all engines; budget human review for low-confidence extractions.
- This is a tax-compliance product where errors carry real financial/legal consequences for users — the human-in-the-loop review step and the anti-hallucination verification pass are not optional polish; they are core to both correctness and liability.