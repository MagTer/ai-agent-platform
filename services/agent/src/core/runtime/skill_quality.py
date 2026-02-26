"""Skill quality analyser for self-healing skill improvements.

Analyses recent skill execution spans for a context, identifies
underperforming skills, and generates improved versions as proposals
for admin review.

Architecture:
    Lives in core/runtime/ (Layer 4).
    Uses: LiteLLMClient, debug_logger queries, skill file I/O.
    Does NOT import from interfaces/ or orchestrator/.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.context.files import ensure_context_directories, get_context_dir
from core.db.models import Context, SkillImprovementProposal
from core.observability.debug_logger import (
    count_skill_executions_for_context,
    read_supervisor_events_for_context,
)
from core.runtime.litellm_client import LiteLLMClient
from core.skills import SkillRegistryProtocol
from core.skills.registry import parse_skill_content

LOGGER = logging.getLogger(__name__)

# Minimum number of executions before a skill is eligible for analysis
MIN_EXECUTIONS_THRESHOLD = 5

# Minimum failure rate (REPLAN + ABORT / total) to trigger improvement
MIN_FAILURE_RATE = 0.3  # 30%

# Maximum number of proposals to generate per run
MAX_PROPOSALS_PER_RUN = 3

# LLM model for generating improvements (use the cheaper model)
IMPROVEMENT_MODEL = "agentchat"

IMPROVEMENT_PROMPT_TEMPLATE = (
    "You are a skill improvement specialist for an AI agent platform.\n\n"
    'A "skill" is a markdown file with YAML frontmatter that instructs an LLM how to perform\n'
    "a task using scoped tools. The platform's self-correction loop has detected that this\n"
    "skill is underperforming.\n\n"
    "## Current Skill Content\n\n"
    "```markdown\n"
    "{current_skill_content}\n"
    "```\n\n"
    "## Failure Analysis\n\n"
    "This skill had {total_executions} executions in the last {analysis_days} days.\n"
    "{failed_executions} executions resulted in REPLAN or ABORT outcomes"
    " ({failure_rate:.0%} failure rate).\n\n"
    "### Failure Reasons (from supervisor evaluations):\n\n"
    "{failure_reasons}\n\n"
    "## Your Task\n\n"
    "Generate an improved version of this skill that addresses the failure patterns above.\n\n"
    "**Rules:**\n"
    "1. Keep the same YAML frontmatter structure (name, description, tools, model, max_turns)\n"
    "2. Do NOT change the skill name or tools list\n"
    "3. Focus on improving the instruction clarity, error handling guidance,"
    " and edge case coverage\n"
    "4. If failures indicate the skill needs more turns, increase max_turns (but not above 15)\n"
    "5. If failures indicate tool misuse, add explicit examples of correct tool usage\n"
    "6. If failures indicate missing context, add pre-conditions or clarifying questions\n"
    "7. Return ONLY the complete improved skill markdown (frontmatter + body), nothing else\n"
    "8. Do NOT wrap the output in code fences\n\n"
    "## Improved Skill Content:\n"
)

SUMMARY_PROMPT_TEMPLATE = (
    "Summarize the changes between these two skill versions in 2-3 sentences.\n"
    "Focus on WHAT changed and WHY (based on the failure patterns).\n\n"
    "Original:\n"
    "```\n"
    "{original}\n"
    "```\n\n"
    "Improved:\n"
    "```\n"
    "{improved}\n"
    "```\n\n"
    "Failure patterns: {failure_summary}\n\n"
    "Summary (2-3 sentences, no markdown):"
)


class SkillQualityAnalyser:
    """Analyses skill execution quality and generates improvement proposals.

    This service:
    1. Reads recent supervisor REPLAN/ABORT events for a context
    2. Identifies skills with high failure rates
    3. Reads current skill content (context overlay or global)
    4. Calls LLM to generate improved skill content
    5. Writes proposals to DB for admin review
    6. Writes the improved content to context overlay immediately (self-healing)
    """

    def __init__(
        self,
        litellm: LiteLLMClient,
        skill_registry: SkillRegistryProtocol | None = None,
    ) -> None:
        """Initialize the analyser.

        Args:
            litellm: LiteLLM client for generating improvements.
            skill_registry: Global skill registry for reading skill content.
        """
        self._litellm = litellm
        self._skill_registry = skill_registry

    async def analyse_and_propose(
        self,
        context_id: UUID,
        session: AsyncSession,
        analysis_days: int = 7,
    ) -> list[SkillImprovementProposal]:
        """Run full analysis pipeline for a context.

        Improved skills are written to the context overlay immediately (self-healing).
        Proposals are recorded in DB with status='applied' for admin visibility.
        Admins can revert via the portal if the change is unwanted.

        Args:
            context_id: Context to analyse.
            session: Database session.
            analysis_days: Number of days to look back.

        Returns:
            List of created SkillImprovementProposal records.
        """
        LOGGER.info(
            "Starting skill quality analysis for context %s (last %d days)",
            context_id,
            analysis_days,
        )

        # Verify context exists
        ctx = await session.get(Context, context_id)
        if not ctx:
            LOGGER.error("Context %s not found", context_id)
            return []

        # 1. Compute time window
        since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=analysis_days)
        since_iso = since.isoformat()

        # 2. Count executions per skill
        execution_counts = await count_skill_executions_for_context(str(context_id), since_iso)

        if not execution_counts:
            LOGGER.info(
                "No skill executions found for context %s in last %d days",
                context_id,
                analysis_days,
            )
            return []

        # 3. Identify underperforming skills
        underperforming: list[tuple[str, dict[str, int]]] = []
        for skill_name, counts in execution_counts.items():
            total = counts["total"]
            if total < MIN_EXECUTIONS_THRESHOLD:
                continue

            failures = counts.get("REPLAN", 0) + counts.get("ABORT", 0)
            failure_rate = failures / total
            if failure_rate >= MIN_FAILURE_RATE:
                underperforming.append((skill_name, counts))
                LOGGER.info(
                    "Skill '%s' identified as underperforming: %d/%d failures (%.0f%%)",
                    skill_name,
                    failures,
                    total,
                    failure_rate * 100,
                )

        if not underperforming:
            LOGGER.info("No underperforming skills found for context %s", context_id)
            return []

        # Sort by failure count descending, take top N
        underperforming.sort(
            key=lambda x: x[1].get("REPLAN", 0) + x[1].get("ABORT", 0),
            reverse=True,
        )
        underperforming = underperforming[:MAX_PROPOSALS_PER_RUN]

        # 4. Get failure reasons from supervisor events
        supervisor_events = await read_supervisor_events_for_context(str(context_id), since_iso)

        # 5. Skip skills that already have an applied (unreverted) proposal
        existing_applied_stmt = select(SkillImprovementProposal.skill_name).where(
            SkillImprovementProposal.context_id == context_id,
            SkillImprovementProposal.status == "applied",
        )
        result = await session.execute(existing_applied_stmt)
        applied_skills = set(result.scalars().all())

        # 6. Generate proposals
        proposals: list[SkillImprovementProposal] = []
        for skill_name, counts in underperforming:
            if skill_name in applied_skills:
                LOGGER.info(
                    "Skipping '%s' -- already has an applied proposal awaiting review",
                    skill_name,
                )
                continue

            try:
                proposal = await self._generate_proposal(
                    context_id=context_id,
                    skill_name=skill_name,
                    counts=counts,
                    supervisor_events=supervisor_events,
                    analysis_days=analysis_days,
                    session=session,
                )
                if proposal:
                    proposals.append(proposal)
            except Exception as exc:
                LOGGER.error(
                    "Failed to generate proposal for skill '%s': %s",
                    skill_name,
                    exc,
                    exc_info=True,
                )

        LOGGER.info(
            "Skill quality analysis complete for context %s: %d proposals created",
            context_id,
            len(proposals),
        )
        return proposals

    async def analyse_from_ratings(
        self,
        context_id: UUID,
        skill_name: str,
        ratings: list[Any],
        session: AsyncSession,
    ) -> SkillImprovementProposal | None:
        """Analyse and propose improvement using quality rating data.

        Called by the evaluator threshold trigger when a skill's average
        score drops below the threshold.

        Args:
            context_id: Context UUID.
            skill_name: Name of the skill to analyse.
            ratings: List of SkillQualityRating objects with scores and notes.
            session: Database session (caller manages commit).

        Returns:
            Created SkillImprovementProposal, or None if skipped.
        """
        # Skip if already has an applied proposal
        existing_stmt = select(SkillImprovementProposal).where(
            SkillImprovementProposal.context_id == context_id,
            SkillImprovementProposal.skill_name == skill_name,
            SkillImprovementProposal.status == "applied",
        )
        result = await session.execute(existing_stmt)
        if result.scalar_one_or_none() is not None:
            LOGGER.info(
                "Skipping '%s' -- already has an applied proposal awaiting review",
                skill_name,
            )
            return None

        # Build failure reasons from rating notes
        functional_notes = []
        formatting_notes = []
        for r in ratings:
            notes = getattr(r, "notes", "") or ""
            fscore = getattr(r, "functional_score", 3)
            fmscore = getattr(r, "formatting_score", 3)
            if fscore <= 3 and notes:
                functional_notes.append(f"[Functional {fscore}/5] {notes}")
            if fmscore <= 3 and notes:
                formatting_notes.append(f"[Formatting {fmscore}/5] {notes}")

        # Build supervisor_events-compatible list for _generate_proposal
        supervisor_events = [
            {
                "trace_id": "",
                "outcome": "REPLAN",
                "reason": note,
                "step_label": skill_name,
                "skill_name": skill_name,
            }
            for note in (functional_notes + formatting_notes)[:15]
        ]

        total_ratings = len(ratings)
        low_scores = sum(
            1
            for r in ratings
            if (getattr(r, "functional_score", 5) + getattr(r, "formatting_score", 5)) / 2.0
            <= QUALITY_EVAL_AVG_THRESHOLD
        )

        counts = {
            "total": max(total_ratings, MIN_EXECUTIONS_THRESHOLD),
            "ABORT": 0,
            "REPLAN": low_scores,
            "SUCCESS": total_ratings - low_scores,
        }

        try:
            proposal = await self._generate_proposal(
                context_id=context_id,
                skill_name=skill_name,
                counts=counts,
                supervisor_events=supervisor_events,
                analysis_days=QUALITY_EVAL_LOOKBACK_DAYS,
                session=session,
            )
            return proposal
        except Exception:
            LOGGER.exception(
                "Failed to generate proposal for skill '%s' (from ratings)", skill_name
            )
            return None

    async def _generate_proposal(
        self,
        context_id: UUID,
        skill_name: str,
        counts: dict[str, int],
        supervisor_events: list[dict[str, Any]],
        analysis_days: int,
        session: AsyncSession,
    ) -> SkillImprovementProposal | None:
        """Generate a single skill improvement proposal.

        Args:
            context_id: Context UUID.
            skill_name: Name of the skill to improve.
            counts: Execution count breakdown.
            supervisor_events: All supervisor events for this context.
            analysis_days: Analysis window in days.
            session: Database session.

        Returns:
            Created proposal, or None if generation failed.
        """
        # Read current skill content
        current_content = await self._read_skill_content(context_id, skill_name)
        if not current_content:
            LOGGER.warning("Could not read content for skill '%s'", skill_name)
            return None

        # Filter supervisor events relevant to this skill
        # Prefer events that have skill_name set (Step 2.5 enhancement), fall back to step_label
        relevant_events = [
            evt
            for evt in supervisor_events
            if evt.get("skill_name") == skill_name
            or skill_name.lower() in (evt.get("step_label", "") or "").lower()  # fallback
        ]

        # If no specific events found, use all events (the analysis is still valid
        # based on execution counts)
        if not relevant_events:
            relevant_events = supervisor_events[:20]

        # Format failure reasons
        failure_reasons_text = "\n".join(
            f"- [{evt.get('outcome', 'UNKNOWN')}] {evt.get('reason', 'No reason given')}"
            for evt in relevant_events[:15]
        )
        if not failure_reasons_text:
            failure_reasons_text = "(No specific failure reasons captured)"

        total = counts["total"]
        failures = counts.get("REPLAN", 0) + counts.get("ABORT", 0)
        failure_rate = failures / total if total > 0 else 0.0

        # Generate improved version via LLM
        prompt = IMPROVEMENT_PROMPT_TEMPLATE.format(
            current_skill_content=current_content,
            total_executions=total,
            analysis_days=analysis_days,
            failed_executions=failures,
            failure_rate=failure_rate,
            failure_reasons=failure_reasons_text,
        )

        improved_content = await self._call_llm(prompt)
        if not improved_content:
            LOGGER.warning("LLM returned empty content for skill '%s'", skill_name)
            return None

        # Strip code fences if the LLM wraps in them despite instructions
        improved_content = improved_content.strip()
        if improved_content.startswith("```"):
            lines = improved_content.split("\n")
            # Remove first and last line if they are fences
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            improved_content = "\n".join(lines)

        # Validate the improved content has valid frontmatter
        test_skill = parse_skill_content(
            Path(f"/tmp/{skill_name}.md"),  # noqa: S108
            improved_content,
            Path("/tmp"),  # noqa: S108
        )
        if not test_skill:
            LOGGER.warning("LLM generated invalid skill content for '%s'", skill_name)
            return None

        # Generate change summary
        summary_prompt = SUMMARY_PROMPT_TEMPLATE.format(
            original=current_content[:2000],
            improved=improved_content[:2000],
            failure_summary=failure_reasons_text[:500],
        )
        change_summary = await self._call_llm(summary_prompt)
        if not change_summary:
            change_summary = f"Automated improvement based on {failures}/{total} failed executions."

        # Determine file name
        skill_file_name = self._get_skill_file_name(context_id, skill_name)

        # Create failure signals for the proposal
        failure_signals = [
            {
                "trace_id": evt.get("trace_id", ""),
                "outcome": evt.get("outcome", ""),
                "reason": (evt.get("reason", "") or "")[:200],
                "step_label": evt.get("step_label", ""),
            }
            for evt in relevant_events[:10]
        ]

        # Write to context overlay immediately (self-healing -- applied before admin sees it)
        await self._write_overlay(context_id, skill_file_name, improved_content)

        proposal = SkillImprovementProposal(
            id=uuid.uuid4(),
            context_id=context_id,
            skill_name=skill_name,
            skill_file_name=skill_file_name,
            original_content=current_content,
            proposed_content=improved_content,
            change_summary=change_summary.strip(),
            failure_signals=failure_signals,
            total_executions=total,
            failed_executions=failures,
            status="applied",  # Already live -- admin can revert if needed
        )
        session.add(proposal)
        await session.flush()

        LOGGER.info(
            "Created skill improvement proposal for '%s' (context %s): %d/%d failures",
            skill_name,
            context_id,
            failures,
            total,
        )

        return proposal

    async def _read_skill_content(
        self,
        context_id: UUID,
        skill_name: str,
    ) -> str | None:
        """Read current skill content (context overlay first, then global).

        Args:
            context_id: Context UUID.
            skill_name: Skill name.

        Returns:
            Raw skill markdown content, or None if not found.
        """
        # Check context overlay first
        context_dir = get_context_dir(context_id)
        skills_dir = context_dir / "skills"
        if skills_dir.exists():
            for file_path in skills_dir.iterdir():
                if file_path.is_file() and file_path.suffix == ".md":
                    try:
                        content = file_path.read_text(encoding="utf-8")
                        skill = parse_skill_content(file_path, content, skills_dir)
                        if skill and skill.name == skill_name:
                            return content
                    except Exception as exc:
                        LOGGER.debug("Could not parse skill file %s: %s", file_path, exc)
                        continue

        # Fall back to global registry
        if self._skill_registry:
            skill = self._skill_registry.get(skill_name)
            if skill:
                return skill.raw_content

        return None

    def _get_skill_file_name(self, context_id: UUID, skill_name: str) -> str:
        """Determine the file name for a skill in the context overlay.

        If a context overlay file already exists for this skill, use its name.
        Otherwise, derive from the global skill's file name.
        Falls back to sanitized skill name + .md.

        Args:
            context_id: Context UUID.
            skill_name: Skill name.

        Returns:
            File name (basename only, e.g., "researcher.md").
        """
        # Check if context already has a file for this skill
        context_dir = get_context_dir(context_id)
        skills_dir = context_dir / "skills"
        if skills_dir.exists():
            for file_path in skills_dir.iterdir():
                if file_path.is_file() and file_path.suffix == ".md":
                    try:
                        content = file_path.read_text(encoding="utf-8")
                        skill = parse_skill_content(file_path, content, skills_dir)
                        if skill and skill.name == skill_name:
                            return file_path.name
                    except Exception as exc:
                        LOGGER.debug("Could not parse skill file %s: %s", file_path, exc)
                        continue

        # Use global skill's file name
        if self._skill_registry:
            skill = self._skill_registry.get(skill_name)
            if skill:
                return skill.path.name

        # Fallback: sanitize name
        safe_name = skill_name.replace("/", "_").replace("\\", "_").replace("..", "_")
        return f"{safe_name}.md"

    async def _write_overlay(
        self,
        context_id: UUID,
        file_name: str,
        content: str,
    ) -> None:
        """Write improved skill to context overlay directory.

        Args:
            context_id: Context UUID.
            file_name: Skill file name.
            content: Improved skill content.
        """
        import asyncio

        context_dir = ensure_context_directories(context_id)
        skills_dir = context_dir / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        target = skills_dir / file_name

        await asyncio.to_thread(target.write_text, content, "utf-8")

        LOGGER.info(
            "Wrote improved skill overlay '%s' for context %s",
            file_name,
            context_id,
        )

    async def _call_llm(self, prompt: str) -> str | None:
        """Call LLM for content generation by consuming stream_chat chunks.

        Args:
            prompt: The prompt to send.

        Returns:
            LLM response text, or None on failure.
        """
        from core.runtime.models import AgentMessage

        try:
            messages = [AgentMessage(role="user", content=prompt)]
            chunks: list[str] = []
            async for chunk in self._litellm.stream_chat(
                messages,
                model=IMPROVEMENT_MODEL,
            ):
                content_part = chunk.get("content")
                if chunk.get("type") == "content" and content_part:
                    chunks.append(content_part)
            result = "".join(chunks)
            return result if result else None
        except Exception as exc:
            LOGGER.error("LLM call failed: %s", exc, exc_info=True)
            return None


# --- End-of-conversation quality evaluator ---

# Threshold configuration
QUALITY_EVAL_MIN_RATINGS = 5  # Minimum ratings before triggering analysis
QUALITY_EVAL_AVG_THRESHOLD = 2.5  # Avg score <= this triggers analysis
QUALITY_EVAL_LOOKBACK_DAYS = 14  # Days to look back for ratings

# LLM model for evaluation (same as analyser -- runs once per conversation)
EVALUATOR_MODEL = "agentchat"

EVALUATOR_PROMPT_TEMPLATE = (
    "You are a quality evaluator for an AI agent platform.\n\n"
    "A user had a conversation with the agent. The agent used skills to complete the request.\n"
    "Evaluate each skill's performance.\n\n"
    "## Skills Used\n\n"
    "{skills_used}\n\n"
    "## Conversation Output (final assistant response)\n\n"
    "{conversation_output}\n\n"
    "## Evaluation Criteria\n\n"
    "For each skill, provide two scores (1-5):\n\n"
    "**Functional Score** (did the skill accomplish its task?):\n"
    "- 5: Perfect execution, correct results, no replans needed\n"
    "- 4: Good execution, minor issues that self-corrected\n"
    "- 3: Acceptable but required retries or partial results\n"
    "- 2: Poor execution, significant errors or missing data\n"
    "- 1: Complete failure, task not accomplished\n\n"
    "**Formatting Score** (was the output well-structured?):\n"
    "- 5: Clean, well-formatted, appropriate structure\n"
    "- 4: Good formatting with minor issues\n"
    "- 3: Acceptable but could be better organized\n"
    "- 2: Poor formatting, raw data or unstructured output\n"
    "- 1: Completely unformatted, raw JSON or garbled\n\n"
    "## Response Format\n\n"
    "Return ONLY valid JSON (no markdown fences). One object per skill:\n"
    '{{"ratings": [{{"skill_name": "...", "functional_score": N, '
    '"formatting_score": N, "notes": "brief explanation"}}]}}\n'
)


async def evaluate_conversation_quality(
    context_id: UUID,
    conversation_id: UUID,
    trace_id: str,
) -> None:
    """Background task: evaluate skill quality after conversation completes.

    Opens its own DB session (NOT the request session -- same pattern as
    the removed post-mortem hook).

    Args:
        context_id: Context UUID.
        conversation_id: Conversation UUID.
        trace_id: Trace ID for reading span events.
    """
    import json

    from core.db.engine import AsyncSessionLocal
    from core.db.models import Conversation, Message, SkillQualityRating
    from core.observability.debug_logger import is_quality_eval_enabled

    try:
        async with AsyncSessionLocal() as session:
            # Check toggles (both debug_enabled AND quality_eval_enabled)
            if not await is_quality_eval_enabled(session):
                return

            # 1. Get skills used from span events (skill_step events for this trace)
            from core.observability.debug_logger import count_skill_executions_for_context

            since = (datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)).isoformat()
            execution_counts = await count_skill_executions_for_context(str(context_id), since)

            if not execution_counts:
                LOGGER.debug(
                    "No skill executions found for conversation %s, skipping evaluation",
                    conversation_id,
                )
                return

            skills_used = list(execution_counts.keys())

            # 2. Get the final assistant message for this conversation
            from sqlalchemy import select as sa_select

            # Find sessions for this conversation, then the latest assistant message
            conv = await session.get(Conversation, conversation_id)
            if not conv:
                LOGGER.warning("Conversation %s not found for quality evaluation", conversation_id)
                return

            from core.db.models import Session as DbSession

            sess_stmt = sa_select(DbSession.id).where(DbSession.conversation_id == conversation_id)
            sess_result = await session.execute(sess_stmt)
            session_ids = [row[0] for row in sess_result.all()]

            if not session_ids:
                return

            msg_stmt = (
                sa_select(Message)
                .where(
                    Message.session_id.in_(session_ids),
                    Message.role == "assistant",
                )
                .order_by(Message.created_at.desc())
                .limit(1)
            )
            msg_result = await session.execute(msg_stmt)
            assistant_msg = msg_result.scalar_one_or_none()

            if not assistant_msg or not assistant_msg.content:
                LOGGER.debug("No assistant message for conversation %s", conversation_id)
                return

            conversation_output = assistant_msg.content[:3000]

            # 3. Build evaluation prompt
            skills_text = "\n".join(f"- {name}" for name in skills_used)
            prompt = EVALUATOR_PROMPT_TEMPLATE.format(
                skills_used=skills_text,
                conversation_output=conversation_output,
            )

            # 4. Call LLM
            from core.runtime.config import get_settings
            from core.runtime.litellm_client import LiteLLMClient
            from core.runtime.models import AgentMessage as RuntimeMessage

            litellm = LiteLLMClient(get_settings())
            messages = [RuntimeMessage(role="user", content=prompt)]
            chunks: list[str] = []
            async for chunk in litellm.stream_chat(messages, model=EVALUATOR_MODEL):
                chunk_content = chunk.get("content")
                if chunk.get("type") == "content" and chunk_content:
                    chunks.append(chunk_content)

            raw_response = "".join(chunks).strip()
            if not raw_response:
                LOGGER.warning("Empty LLM response for quality evaluation")
                return

            # Strip markdown fences if present
            if raw_response.startswith("```"):
                lines = raw_response.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                raw_response = "\n".join(lines)

            # 5. Parse response
            try:
                parsed = json.loads(raw_response)
            except json.JSONDecodeError:
                LOGGER.warning("Failed to parse quality evaluation JSON: %s", raw_response[:200])
                return

            ratings_data = parsed.get("ratings", [])
            if not isinstance(ratings_data, list):
                LOGGER.warning("Invalid ratings format in quality evaluation")
                return

            # 6. Save ratings
            saved_skills: list[str] = []
            for entry in ratings_data:
                skill_name = entry.get("skill_name", "")
                if skill_name not in skills_used:
                    continue  # LLM hallucinated a skill name

                functional = entry.get("functional_score", 3)
                formatting = entry.get("formatting_score", 3)
                notes = entry.get("notes", "")

                # Clamp scores to 1-5
                functional = max(1, min(5, int(functional)))
                formatting = max(1, min(5, int(formatting)))

                rating = SkillQualityRating(
                    context_id=context_id,
                    conversation_id=conversation_id,
                    skill_name=skill_name,
                    functional_score=functional,
                    formatting_score=formatting,
                    notes=str(notes)[:500],
                )
                session.add(rating)
                saved_skills.append(skill_name)

            await session.commit()

            LOGGER.info(
                "Quality evaluation complete for conversation %s: %d skill(s) rated",
                conversation_id,
                len(saved_skills),
            )

            # 7. Check if any skill needs analysis (threshold trigger)
            await _check_quality_thresholds(
                session=session,
                context_id=context_id,
                skill_names=saved_skills,
                litellm=litellm,
            )

    except Exception:
        LOGGER.exception(
            "Quality evaluation failed for conversation %s (context %s)",
            conversation_id,
            context_id,
        )


async def _check_quality_thresholds(
    session: AsyncSession,
    context_id: UUID,
    skill_names: list[str],
    litellm: LiteLLMClient,
) -> None:
    """Check if any rated skill's recent average score is below threshold.

    If so, trigger SkillQualityAnalyser for that skill.

    Args:
        session: Database session.
        context_id: Context UUID.
        skill_names: Skills to check.
        litellm: LiteLLM client for analyser.
    """
    from sqlalchemy import func as sa_func
    from sqlalchemy import select as sa_select

    from core.db.models import SkillQualityRating

    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=QUALITY_EVAL_LOOKBACK_DAYS)

    for skill_name in skill_names:
        # Get recent ratings
        stmt = sa_select(
            sa_func.count(SkillQualityRating.id),
            sa_func.avg(
                (SkillQualityRating.functional_score + SkillQualityRating.formatting_score) / 2.0
            ),
        ).where(
            SkillQualityRating.context_id == context_id,
            SkillQualityRating.skill_name == skill_name,
            SkillQualityRating.created_at >= since,
        )
        result = await session.execute(stmt)
        row = result.one()
        count = row[0] or 0
        avg_score = float(row[1]) if row[1] is not None else 5.0

        if count >= QUALITY_EVAL_MIN_RATINGS and avg_score <= QUALITY_EVAL_AVG_THRESHOLD:
            LOGGER.info(
                "Skill '%s' quality below threshold (avg %.2f over %d ratings) "
                "-- triggering analysis for context %s",
                skill_name,
                avg_score,
                count,
                context_id,
            )

            # Get the rating notes for the analyser
            notes_stmt = (
                sa_select(SkillQualityRating)
                .where(
                    SkillQualityRating.context_id == context_id,
                    SkillQualityRating.skill_name == skill_name,
                    SkillQualityRating.created_at >= since,
                )
                .order_by(SkillQualityRating.created_at.desc())
                .limit(QUALITY_EVAL_MIN_RATINGS * 2)
            )
            notes_result = await session.execute(notes_stmt)
            recent_ratings = list(notes_result.scalars().all())

            try:
                analyser = SkillQualityAnalyser(
                    litellm=litellm,
                    skill_registry=None,  # Will fall back to global registry lookup
                )
                await analyser.analyse_from_ratings(
                    context_id=context_id,
                    skill_name=skill_name,
                    ratings=recent_ratings,
                    session=session,
                )
                await session.commit()
            except Exception:
                LOGGER.exception("Threshold-triggered analysis failed for skill '%s'", skill_name)
                await session.rollback()
