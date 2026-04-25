"""Deep Research orchestrator — the brain.

Iterative deepening pipeline:
  Decompose → [Search → Evaluate → Extract → Gaps]* → Synthesize → Verify

Each round searches for what's missing, extracts content, identifies gaps,
and generates new queries.  Cross-domain exploration is mandatory — the
system actively searches for analogous solutions in unrelated fields.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import Callable
from pathlib import Path

from .config import DeepResearchConfig
from .extract import extract_multiple
from .llm import LLMClient
from .prompts import (
    DECOMPOSE_QUERY,
    EVALUATE_RESULTS,
    GAP_ANALYSIS,
    SUMMARIZE_BATCH,
    SYNTHESIZE_REPORT,
    SYSTEM_RESEARCHER,
    VERIFY_CLAIMS,
)
from .search import MultiSearch, SearchResult, build_search

logger = logging.getLogger(__name__)


class DeepResearcher:
    """Autonomous deep research engine with iterative deepening."""

    def __init__(
        self,
        config: DeepResearchConfig | None = None,
        llm: LLMClient | None = None,
        search: MultiSearch | None = None,
        on_progress: Callable | None = None,
    ):
        self.config = config or DeepResearchConfig.from_env()
        self.llm = llm or LLMClient(self.config)
        self.search = search or build_search(self.config)
        self.progress = on_progress or (lambda msg: print(msg))
        self._steer_queue: asyncio.Queue | None = None  # injected queries

    def set_steer_queue(self, queue: asyncio.Queue):
        """Set a queue for receiving injected sub-queries during research."""
        self._steer_queue = queue

    # ── Structured progress helper ──────────────────────────────

    def _emit(self, phase: str, detail: str, **extra):
        """Emit a structured progress event + human-readable line."""
        event = {"phase": phase, "detail": detail, **extra}
        # Default callback is print() — output JSON for machine parsing
        if self.progress is print:
            import json as _json
            self.progress(_json.dumps(event))
        else:
            try:
                self.progress(event)
            except TypeError:
                self.progress(detail)

    # ── Public API ─────────────────────────────────────────────────

    async def decompose_only(self, query: str) -> dict:
        """Run Phase 1 only — return the research plan without executing.

        Returns dict with: sub_queries, reformulations, cross_domain_queries,
        key_entities. The caller can modify this and pass it to research_with_plan().
        """
        return await self._decompose(query)

    async def research_with_plan(self, query: str, plan: dict) -> str:
        """Run research using a pre-built/modified plan (skip decomposition)."""
        return await self._run(query, plan=plan)

    async def research(self, query: str) -> str:
        """Run a full deep research session. Returns the final report markdown."""
        return await self._run(query, plan=None)

    async def _run(self, query: str, plan: dict | None = None) -> str:
        """Core research loop. If plan is provided, skip decomposition."""
        start = time.time()
        deadline = start + self.config.time_limit_minutes * 60
        topic_slug = _slugify(query)
        out_dir = Path(self.config.output_dir) / topic_slug
        out_dir.mkdir(parents=True, exist_ok=True)

        self._emit("init", (
            f"Model: {self.config.llm_model} @ {self.config.llm_base_url}\n"
            f"Search: {', '.join(self.search.active_names)}\n"
            f"Time limit: {self.config.time_limit_minutes} minutes"
        ), elapsed=0)

        # ── Phase 1: Decompose (or use provided plan) ─────────────
        if plan:
            self._emit("decompose", "Using provided research plan", elapsed=int(time.time() - start))
            decomposition = plan
        else:
            self._emit("decompose", "Decomposing query...", elapsed=int(time.time() - start))
            decomposition = await self._decompose(query)

        sub_qs = decomposition.get("sub_queries", [query])
        reformulations = decomposition.get("reformulations", [])
        cross_qs = decomposition.get("cross_domain_queries", [])
        entities = decomposition.get("key_entities", [])
        self._emit("decompose", (
            f"{len(sub_qs)} sub-queries, {len(reformulations)} reformulations, "
            f"{len(cross_qs)} cross-domain, {len(entities)} key entities"
        ), sub_queries=len(sub_qs), elapsed=int(time.time() - start))

        # ── Phase 2: Iterative research loop ───────────────────────
        findings: list[dict] = []         # {title, url, source, content, round}
        source_index: list[dict] = []     # ordered source list for citations
        searched_queries: set[str] = set()
        iteration = 0

        while time.time() < deadline and iteration < self.config.max_iterations:
            iteration += 1
            elapsed = int(time.time() - start)
            remaining = int((deadline - time.time()) / 60)
            self._emit("search", (
                f"Round {iteration}/{self.config.max_iterations} "
                f"({elapsed}s elapsed, ~{remaining}min left)"
            ), round=iteration, total_rounds=self.config.max_iterations,
               findings_count=len(findings), elapsed=elapsed)

            # Drain steer queue — inject user-supplied sub-queries
            if self._steer_queue:
                while not self._steer_queue.empty():
                    try:
                        steered = self._steer_queue.get_nowait()
                        if isinstance(steered, list):
                            sub_qs.extend(steered)
                        elif isinstance(steered, str):
                            sub_qs.append(steered)
                        self._emit("steer", f"Injected {len(steered) if isinstance(steered, list) else 1} steered queries",
                                   round=iteration, elapsed=int(time.time() - start))
                    except asyncio.QueueEmpty:
                        break

            # Determine what to search — sub-queries + reformulations + cross-domain
            queries = list(sub_qs)
            queries.extend(reformulations)
            if self.config.cross_domain and cross_qs:
                queries.extend(cross_qs)
            queries = [q for q in queries if q not in searched_queries]

            if not queries:
                self._emit("search", "No new queries to search — stopping",
                           round=iteration, elapsed=int(time.time() - start))
                break

            self._emit("search", f"Searching {len(queries)} queries...",
                       round=iteration, query_count=len(queries), elapsed=int(time.time() - start))
            all_results = await self.search.search_many(
                queries, self.config.max_results_per_source
            )
            searched_queries.update(queries)

            # Deduplicate against already-fetched URLs
            known_urls = {f["url"] for f in findings}
            new_results = [r for r in all_results if r.url not in known_urls]
            self._emit("search", f"{len(new_results)} new unique results (RRF fused)",
                       round=iteration, results_count=len(new_results), elapsed=int(time.time() - start))

            if not new_results:
                self._emit("search", "No new results — stopping",
                           round=iteration, elapsed=int(time.time() - start))
                break

            # Evaluate relevance
            self._emit("evaluate", "Evaluating relevance...",
                       round=iteration, elapsed=int(time.time() - start))
            top = await self._evaluate(query, new_results[:40])
            self._emit("evaluate", f"{len(top)} results pass relevance filter",
                       round=iteration, relevant_count=len(top), elapsed=int(time.time() - start))

            if not top:
                sub_qs = []
                cross_qs = []
                continue

            # Extract content
            fetch_urls = [r.url for r in top[: self.config.max_pages_to_fetch]]
            self._emit("extract", f"Extracting content from {len(fetch_urls)} URLs...",
                       round=iteration, url_count=len(fetch_urls), elapsed=int(time.time() - start))
            contents = await extract_multiple(fetch_urls)
            self._emit("extract", f"{len(contents)}/{len(fetch_urls)} extracted",
                       round=iteration, extracted=len(contents), elapsed=int(time.time() - start))

            # Build findings
            new_count = 0
            for r in top:
                content = contents.get(r.url)
                if not content:
                    # Fall back to snippet if extraction failed
                    content = r.snippet if len(r.snippet) > 50 else None
                if content:
                    src_num = len(source_index) + 1
                    findings.append({
                        "title": r.title,
                        "url": r.url,
                        "source": r.source,
                        "content": content,
                        "round": iteration,
                        "source_num": src_num,
                    })
                    source_index.append({
                        "num": src_num,
                        "title": r.title,
                        "url": r.url,
                        "engine": r.source,
                    })
                    new_count += 1

            self._emit("extract", f"+{new_count} findings (total: {len(findings)})",
                       round=iteration, findings_count=len(findings), elapsed=int(time.time() - start))

            # Save scratchpad
            self._save_scratchpad(out_dir, query, findings, iteration)

            # Gap analysis
            self._emit("gaps", "Analyzing gaps...",
                       round=iteration, elapsed=int(time.time() - start))
            gaps = await self._gap_analysis(query, findings, searched_queries)
            confidence = gaps.get("confidence", 0.0)
            should_continue = gaps.get("should_continue", True)
            self._emit("gaps", (
                f"Confidence: {confidence:.0%} | "
                f"Continue: {should_continue} | "
                f"Gaps: {len(gaps.get('gaps', []))}"
            ), round=iteration, confidence=confidence,
               gap_count=len(gaps.get("gaps", [])), elapsed=int(time.time() - start))

            if not should_continue:
                self._emit("gaps", "Research sufficiently comprehensive — stopping",
                           round=iteration, confidence=confidence, elapsed=int(time.time() - start))
                break

            # Prepare next round — gaps feed new queries + reformulations
            sub_qs = gaps.get("new_queries", [])
            reformulations = gaps.get("reformulations", [])
            cross_qs = gaps.get("cross_domain_suggestions", [])

        # ── Phase 3: Synthesize ────────────────────────────────────
        self._emit("synthesize", f"Synthesizing {len(findings)} findings...",
                   findings_count=len(findings), sources_count=len(source_index),
                   elapsed=int(time.time() - start))
        report = await self._synthesize(query, findings, source_index)

        # ── Phase 4: Verify (optional) ─────────────────────────────
        if self.config.verify_claims and findings:
            self._emit("verify", "Cross-referencing key claims...",
                       elapsed=int(time.time() - start))
            verification = await self._verify(report, findings)
            if verification:
                report += "\n\n## Claim Verification\n\n" + verification

        # ── Save ───────────────────────────────────────────────────
        report_path = out_dir / "comprehensive-report.md"
        report_path.write_text(report, encoding="utf-8")

        # Save source index as JSON
        (out_dir / "sources.json").write_text(
            json.dumps(source_index, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        elapsed_total = int(time.time() - start)
        self._emit("done", (
            f"Research complete in {elapsed_total}s — "
            f"{iteration} rounds, {len(findings)} sources consulted"
        ), rounds=iteration, findings_count=len(findings),
           sources_count=len(source_index), elapsed=elapsed_total)

        return report

    # ── Internal pipeline steps ────────────────────────────────────

    async def _decompose(self, query: str) -> dict:
        """Break query into sub-queries + cross-domain queries."""
        try:
            return await self.llm.generate_json(
                DECOMPOSE_QUERY.format(query=query),
                system=SYSTEM_RESEARCHER,
                task_hint="decompose",
            )
        except (ValueError, RuntimeError) as e:
            logger.warning("Decomposition failed: %s — using query as-is", e)
            return {"sub_queries": [query], "cross_domain_queries": [], "key_entities": []}

    async def _evaluate(
        self, query: str, results: list[SearchResult]
    ) -> list[SearchResult]:
        """LLM-based relevance scoring of search results."""
        if not results:
            return []

        # Format results for the LLM
        results_text = "\n".join(
            f"[{i}] {r.title}\n    {r.url}\n    {r.snippet[:200]}"
            for i, r in enumerate(results)
        )

        try:
            data = await self.llm.generate_json(
                EVALUATE_RESULTS.format(query=query, results_text=results_text),
                system=SYSTEM_RESEARCHER,
                task_hint="evaluate",
            )
            keep_indices = {
                s["index"]
                for s in data.get("scores", [])
                if s.get("keep", False)
            }
            return [r for i, r in enumerate(results) if i in keep_indices]
        except (ValueError, RuntimeError) as e:
            logger.warning("Evaluation failed: %s — keeping top results by RRF", e)
            return results[:15]

    async def _gap_analysis(
        self,
        query: str,
        findings: list[dict],
        searched: set[str],
    ) -> dict:
        """Identify what's still missing and generate follow-up queries."""
        # Build a compact summary of findings
        summary_parts = []
        for f in findings[-20:]:  # Last 20 to keep context manageable
            summary_parts.append(
                f"- [{f['source_num']}] {f['title']}: {f['content'][:200]}..."
            )
        findings_summary = "\n".join(summary_parts)
        searched_str = ", ".join(list(searched)[:20])

        try:
            return await self.llm.generate_json(
                GAP_ANALYSIS.format(
                    query=query,
                    searched=searched_str,
                    findings_summary=findings_summary,
                ),
                system=SYSTEM_RESEARCHER,
                task_hint="gap_analysis",
            )
        except (ValueError, RuntimeError) as e:
            logger.warning("Gap analysis failed: %s — stopping", e)
            return {"should_continue": False, "confidence": 0.5}

    async def _synthesize(
        self,
        query: str,
        findings: list[dict],
        source_index: list[dict],
    ) -> str:
        """Two-step synthesis: batch summarize → final report."""
        budget = self.config.context_chars
        # Per-finding budget: leave room for prompt overhead
        per_finding = max(1000, int((budget * 0.7) / max(len(findings), 1)))
        batch_size = 5
        summaries: list[str] = []

        for i in range(0, len(findings), batch_size):
            batch = findings[i : i + batch_size]
            batch_text = "\n\n---\n\n".join(
                f"[{f['source_num']}] {f['title']} ({f['url']})\n{f['content'][:per_finding]}"
                for f in batch
            )
            try:
                summary = await self.llm.generate(
                    SUMMARIZE_BATCH.format(query=query, batch_text=batch_text),
                    system=SYSTEM_RESEARCHER,
                    task_hint="summarize",
                )
                summaries.append(summary)
            except RuntimeError as e:
                logger.warning("Batch summary failed: %s", e)
                # Fallback: use raw snippets
                summaries.append(batch_text[:2000])

        # Step 2: Final synthesis from summaries
        all_summaries = "\n\n---\n\n".join(summaries)
        source_list = "\n".join(
            f"[{s['num']}] {s['title']} — {s['url']}" for s in source_index
        )

        report = await self.llm.generate(
            SYNTHESIZE_REPORT.format(
                query=query,
                source_count=len(source_index),
                findings=all_summaries,
                source_list=source_list,
            ),
            system=SYSTEM_RESEARCHER,
            task_hint="synthesize",
        )
        return report

    async def _verify(self, report: str, findings: list[dict]) -> str | None:
        """Cross-reference key claims against source content."""
        budget = self.config.context_chars
        report_excerpt = report[:int(budget * 0.3)]
        per_source = max(500, int((budget * 0.4) / min(len(findings), 15)))
        source_content = "\n\n".join(
            f"[{f['source_num']}] {f['content'][:per_source]}"
            for f in findings[:15]
        )

        try:
            data = await self.llm.generate_json(
                VERIFY_CLAIMS.format(
                    report_excerpt=report_excerpt,
                    source_content=source_content,
                ),
                system=SYSTEM_RESEARCHER,
                task_hint="verify",
            )
            verifications = data.get("verifications", [])
            if not verifications:
                return None

            lines = []
            status_emoji = {
                "VERIFIED": "V",
                "LIKELY": "~",
                "UNVERIFIED": "?",
                "CONTRADICTED": "X",
            }
            for v in verifications:
                icon = status_emoji.get(v.get("status", ""), "?")
                sources = v.get("sources", [])
                src_str = f" (sources: {', '.join(str(s) for s in sources)})" if sources else ""
                lines.append(
                    f"- **[{icon}]** {v.get('claim', '?')}{src_str}\n"
                    f"  {v.get('note', '')}"
                )
            return "\n".join(lines)
        except (ValueError, RuntimeError) as e:
            logger.warning("Verification failed: %s", e)
            return None

    # ── Helpers ─────────────────────────────────────────────────────

    def _save_scratchpad(
        self,
        out_dir: Path,
        query: str,
        findings: list[dict],
        iteration: int,
    ):
        """Append current findings to scratchpad (monitorable by aligner)."""
        path = out_dir / "scratchpad.md"
        lines = [f"# Scratchpad — {query}\n\n"]
        current_round = None
        for f in findings:
            if f["round"] != current_round:
                current_round = f["round"]
                lines.append(f"\n## Round {current_round}\n\n")
            lines.append(
                f"### [{f['source_num']}] {f['title']}\n"
                f"- Source: {f['source']} | URL: {f['url']}\n"
                f"- {f['content'][:300]}...\n\n"
            )
        path.write_text("".join(lines), encoding="utf-8")

    async def close(self):
        await self.llm.close()


def _slugify(text: str) -> str:
    """Convert text to filesystem-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text[:60].rstrip("-")
