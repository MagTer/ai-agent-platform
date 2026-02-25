"""Tests for the SkillQualityAnalyser."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.runtime.skill_quality import (
    MIN_EXECUTIONS_THRESHOLD,
    SkillQualityAnalyser,
)

_IMPROVED_CONTENT = (
    "---\nname: test_skill\ndescription: Improved\n"
    "tools: []\nmodel: agentchat\nmax_turns: 5\n---\n\nImproved instructions."
)

_ORIGINAL_CONTENT = (
    "---\nname: test_skill\ndescription: Original\n"
    "tools: []\nmodel: agentchat\nmax_turns: 5\n---\n\nOriginal instructions."
)

_PATCH_COUNT = "core.runtime.skill_quality.count_skill_executions_for_context"
_PATCH_EVENTS = "core.runtime.skill_quality.read_supervisor_events_for_context"
_PATCH_CTX_DIR = "core.runtime.skill_quality.get_context_dir"
_PATCH_ENSURE = "core.runtime.skill_quality.ensure_context_directories"


@pytest.fixture
def mock_litellm() -> MagicMock:
    """Create a mock LiteLLM client."""
    client = MagicMock()

    async def _stream_chat(messages: Any, *, model: Any = None, tools: Any = None) -> Any:
        """Yield a single content chunk."""
        yield {"type": "content", "content": _IMPROVED_CONTENT}

    client.stream_chat = _stream_chat
    return client


@pytest.fixture
def mock_skill_registry() -> MagicMock:
    """Create a mock skill registry."""
    registry = MagicMock()
    skill = MagicMock()
    skill.name = "test_skill"
    skill.raw_content = _ORIGINAL_CONTENT
    skill.path = Path("/skills/general/test_skill.md")
    registry.get.return_value = skill
    return registry


@pytest.mark.asyncio
async def test_analyse_no_executions(
    mock_litellm: MagicMock, mock_skill_registry: MagicMock
) -> None:
    """Test that analysis with no executions returns empty."""
    analyser = SkillQualityAnalyser(mock_litellm, mock_skill_registry)
    context_id = uuid.uuid4()

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=MagicMock())  # Context exists

    with patch(_PATCH_COUNT, new_callable=AsyncMock) as mock_count:
        mock_count.return_value = {}

        proposals = await analyser.analyse_and_propose(context_id, mock_session)

    assert proposals == []


@pytest.mark.asyncio
async def test_analyse_below_threshold(
    mock_litellm: MagicMock, mock_skill_registry: MagicMock
) -> None:
    """Test that skills below execution threshold are skipped."""
    analyser = SkillQualityAnalyser(mock_litellm, mock_skill_registry)
    context_id = uuid.uuid4()

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=MagicMock())

    with patch(_PATCH_COUNT, new_callable=AsyncMock) as mock_count:
        mock_count.return_value = {
            "test_skill": {
                "total": MIN_EXECUTIONS_THRESHOLD - 1,
                "SUCCESS": 1,
                "REPLAN": 1,
                "ABORT": 0,
                "RETRY": 0,
            },
        }

        proposals = await analyser.analyse_and_propose(context_id, mock_session)

    assert proposals == []


@pytest.mark.asyncio
async def test_analyse_below_failure_rate(
    mock_litellm: MagicMock, mock_skill_registry: MagicMock
) -> None:
    """Test that skills below failure rate threshold are skipped."""
    analyser = SkillQualityAnalyser(mock_litellm, mock_skill_registry)
    context_id = uuid.uuid4()

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=MagicMock())

    with patch(_PATCH_COUNT, new_callable=AsyncMock) as mock_count:
        mock_count.return_value = {
            "test_skill": {
                "total": 10,
                "SUCCESS": 9,
                "REPLAN": 1,
                "ABORT": 0,
                "RETRY": 0,
            },
        }

        proposals = await analyser.analyse_and_propose(context_id, mock_session)

    assert proposals == []


@pytest.mark.asyncio
async def test_analyse_generates_proposal(
    mock_litellm: MagicMock,
    mock_skill_registry: MagicMock,
    tmp_path: Path,
) -> None:
    """Test that a proposal is generated for an underperforming skill."""
    analyser = SkillQualityAnalyser(mock_litellm, mock_skill_registry)
    context_id = uuid.uuid4()

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=MagicMock())
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()

    # Mock the applied proposals query
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session.execute = AsyncMock(return_value=mock_result)

    with (
        patch(_PATCH_COUNT, new_callable=AsyncMock) as mock_count,
        patch(_PATCH_EVENTS, new_callable=AsyncMock) as mock_events,
        patch(_PATCH_CTX_DIR) as mock_ctx_dir,
        patch(_PATCH_ENSURE) as mock_ensure_dirs,
    ):
        mock_count.return_value = {
            "test_skill": {
                "total": 10,
                "SUCCESS": 3,
                "REPLAN": 5,
                "ABORT": 2,
                "RETRY": 0,
            },
        }
        mock_events.return_value = [
            {
                "trace_id": "abc",
                "outcome": "REPLAN",
                "reason": "Tool returned empty result",
                "step_label": "test_skill step",
            },
        ]
        # Mock context dir to avoid filesystem access
        mock_ctx_dir.return_value = tmp_path
        mock_ensure_dirs.return_value = tmp_path
        (tmp_path / "skills").mkdir(exist_ok=True)

        proposals = await analyser.analyse_and_propose(context_id, mock_session)

    assert len(proposals) == 1
    assert proposals[0].skill_name == "test_skill"
    assert proposals[0].status == "applied"
    assert proposals[0].failed_executions == 7
    assert proposals[0].total_executions == 10
    mock_session.add.assert_called_once()


@pytest.mark.asyncio
async def test_analyse_skips_applied(
    mock_litellm: MagicMock, mock_skill_registry: MagicMock
) -> None:
    """Test that skills with existing applied proposals are skipped."""
    analyser = SkillQualityAnalyser(mock_litellm, mock_skill_registry)
    context_id = uuid.uuid4()

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=MagicMock())

    # Mock the applied proposals query to return existing applied skill
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = ["test_skill"]  # already applied
    mock_session.execute = AsyncMock(return_value=mock_result)

    with (
        patch(_PATCH_COUNT, new_callable=AsyncMock) as mock_count,
        patch(_PATCH_EVENTS, new_callable=AsyncMock) as mock_events,
    ):
        mock_count.return_value = {
            "test_skill": {
                "total": 10,
                "SUCCESS": 3,
                "REPLAN": 5,
                "ABORT": 2,
                "RETRY": 0,
            },
        }
        mock_events.return_value = []

        proposals = await analyser.analyse_and_propose(context_id, mock_session)

    assert proposals == []


@pytest.mark.asyncio
async def test_evaluate_conversation_quality_disabled(
    mock_litellm: MagicMock,
) -> None:
    """Test that evaluation is skipped when toggles are off."""
    from core.runtime.skill_quality import evaluate_conversation_quality

    # Patch is_quality_eval_enabled to return False
    with patch(
        "core.observability.debug_logger.is_quality_eval_enabled",
        new_callable=AsyncMock,
        return_value=False,
    ):
        with patch("core.db.engine.AsyncSessionLocal") as mock_factory:
            mock_session = AsyncMock()
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            await evaluate_conversation_quality(
                context_id=uuid.uuid4(),
                conversation_id=uuid.uuid4(),
                trace_id="test-trace",
            )

    # Test completes without error -- LLM was not called (verified by the session
    # not having any queries executed since we returned early)
    mock_session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_analyse_from_ratings(
    mock_litellm: MagicMock,
    mock_skill_registry: MagicMock,
    tmp_path: Path,
) -> None:
    """Test analyse_from_ratings generates a proposal from rating data."""
    analyser = SkillQualityAnalyser(mock_litellm, mock_skill_registry)
    context_id = uuid.uuid4()

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=MagicMock())
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()

    # Mock: no existing applied proposals
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_result)

    # Create mock ratings
    mock_ratings = []
    for i in range(5):
        r = MagicMock()
        r.functional_score = 2
        r.formatting_score = 2
        r.notes = f"Skill failed to format output correctly (rating {i})"
        mock_ratings.append(r)

    with (
        patch(_PATCH_CTX_DIR) as mock_ctx_dir,
        patch(_PATCH_ENSURE) as mock_ensure_dirs,
    ):
        mock_ctx_dir.return_value = tmp_path
        mock_ensure_dirs.return_value = tmp_path
        (tmp_path / "skills").mkdir(exist_ok=True)

        proposal = await analyser.analyse_from_ratings(
            context_id=context_id,
            skill_name="test_skill",
            ratings=mock_ratings,
            session=mock_session,
        )

    assert proposal is not None
    assert proposal.skill_name == "test_skill"
    assert proposal.status == "applied"
    mock_session.add.assert_called_once()


@pytest.mark.asyncio
async def test_analyse_from_ratings_skips_applied(
    mock_litellm: MagicMock,
    mock_skill_registry: MagicMock,
) -> None:
    """Test that analyse_from_ratings skips skills with existing applied proposals."""
    analyser = SkillQualityAnalyser(mock_litellm, mock_skill_registry)
    context_id = uuid.uuid4()

    mock_session = AsyncMock()
    # Return existing proposal
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = MagicMock()  # exists
    mock_session.execute = AsyncMock(return_value=mock_result)

    proposal = await analyser.analyse_from_ratings(
        context_id=context_id,
        skill_name="test_skill",
        ratings=[],
        session=mock_session,
    )

    assert proposal is None


@pytest.mark.asyncio
async def test_check_quality_thresholds_below_min_ratings() -> None:
    """Test that threshold check does nothing with insufficient ratings."""
    from core.runtime.skill_quality import _check_quality_thresholds

    mock_session = AsyncMock()
    # Return count=2, avg=2.0 (below min ratings)
    mock_result = MagicMock()
    mock_result.one.return_value = (2, 2.0)
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_litellm = MagicMock()

    await _check_quality_thresholds(
        session=mock_session,
        context_id=uuid.uuid4(),
        skill_names=["test_skill"],
        litellm=mock_litellm,
    )

    # No analyser should have been triggered (only 2 ratings, need 5)
    # Verify by checking session.execute was called once (for the count query)
    assert mock_session.execute.call_count == 1
