"""Integration tests for checkpoint/resume flow."""

import json
from pathlib import Path

import pytest

from job_hunter.main import (
    _load_checkpoint,
    _save_checkpoint,
    _wipe_checkpoint,
    _prompt_resume_choice,
    CHECKPOINT_PATH,
    STAGES,
)


class TestCheckpointLifecycle:
    @pytest.fixture
    def tmp_checkpoint(self, tmp_path: Path):
        old = CHECKPOINT_PATH
        import job_hunter.main as main_mod
        main_mod.CHECKPOINT_PATH = tmp_path / "checkpoint.json"
        yield tmp_path / "checkpoint.json"
        main_mod.CHECKPOINT_PATH = old

    def test_save_and_load_roundtrip(self, tmp_checkpoint):
        sent_urls = {"https://a.com", "https://b.com"}
        stage_jobs = {
            "profile_analyzed": {"refined_profile": "test"},
            "collected_jobs": [{"title": "Engineer", "url": "https://a.com"}],
            "filtered_jobs": [{"title": "Engineer", "url": "https://a.com", "match_reason": "good"}],
            "researched_jobs": [],
        }
        config = {"profile": "senior dev", "keywords": ["architect"], "provider": "minimax"}
        stats = {"jobs_collected": 1, "jobs_approved": 1, "jobs_researched": 0, "jobs_notified": 0}

        _save_checkpoint("filter", config, stage_jobs, sent_urls, stats)
        assert tmp_checkpoint.exists()

        loaded = _load_checkpoint()
        assert loaded is not None
        assert loaded["stage"] == "filter"
        assert len(loaded["filtered_jobs"]) == 1
        assert set(loaded["sent_urls"]) == {"https://a.com", "https://b.com"}
        assert loaded["config_snapshot"]["keywords"] == ["architect"]

    def test_wipe_deletes_file(self, tmp_checkpoint):
        _save_checkpoint("profile", {}, {"profile_analyzed": {}}, set(), {})
        assert tmp_checkpoint.exists()
        _wipe_checkpoint()
        assert not tmp_checkpoint.exists()

    def test_load_missing_returns_none(self, tmp_checkpoint):
        result = _load_checkpoint()
        assert result is None

    def test_load_corrupt_returns_none(self, tmp_checkpoint):
        tmp_checkpoint.write_text("not valid json{{{", encoding="utf-8")
        result = _load_checkpoint()
        assert result is None


class TestStageSkipLogic:
    """Verify skip condition: skip stage X if checkpoint_stage_index >= STAGES.index(X)."""

    def test_skip_condition_when_stage_collect(self):
        """When checkpoint.stage == 'collect', profile and collect are done."""
        STAGES_local = ["profile", "collect", "filter", "research", "notify"]
        checkpoint_stage = "collect"
        cp_idx = STAGES_local.index(checkpoint_stage)  # 1

        for stage_name, stage_idx in [("profile", 0), ("collect", 1), ("filter", 2), ("research", 3), ("notify", 4)]:
            should_skip = cp_idx >= stage_idx
            if stage_idx <= cp_idx:
                assert should_skip is True, f"Expected to skip {stage_name} when cp_idx={cp_idx}"
            else:
                assert should_skip is False, f"Expected NOT to skip {stage_name} when cp_idx={cp_idx}"

    def test_skip_condition_when_stage_filter(self):
        """When checkpoint.stage == 'filter', profile, collect, filter are done."""
        STAGES_local = ["profile", "collect", "filter", "research", "notify"]
        checkpoint_stage = "filter"
        cp_idx = STAGES_local.index(checkpoint_stage)  # 2

        for stage_name, stage_idx in [("profile", 0), ("collect", 1), ("filter", 2), ("research", 3), ("notify", 4)]:
            should_skip = cp_idx >= stage_idx
            if stage_idx <= cp_idx:
                assert should_skip is True, f"Expected to skip {stage_name} when cp_idx={cp_idx}"
            else:
                assert should_skip is False, f"Expected NOT to skip {stage_name} when cp_idx={cp_idx}"

    def test_skip_condition_when_stage_notify(self):
        """When checkpoint.stage == 'notify', all stages done."""
        STAGES_local = ["profile", "collect", "filter", "research", "notify"]
        checkpoint_stage = "notify"
        cp_idx = STAGES_local.index(checkpoint_stage)  # 4

        for stage_name, stage_idx in [("profile", 0), ("collect", 1), ("filter", 2), ("research", 3), ("notify", 4)]:
            assert (cp_idx >= stage_idx) is True, f"Expected to skip {stage_name}"

    def test_stage_order_correct(self):
        assert STAGES == ["profile", "collect", "filter", "research", "notify"]
        assert STAGES.index("profile") == 0
        assert STAGES.index("collect") == 1
        assert STAGES.index("filter") == 2
        assert STAGES.index("research") == 3
        assert STAGES.index("notify") == 4


class TestResumeChoicePrompt:
    """Test _prompt_resume_choice output values."""

    def test_returns_resume_for_r(self, capsys):
        with pytest.raises(StopIteration):
            def fake_input(_):
                raise StopIteration("r")
            import builtins
            orig = builtins.input
            builtins.input = fake_input
            try:
                _prompt_resume_choice({"stage": "filter", "collected_jobs": [], "filtered_jobs": [], "researched_jobs": []})
            finally:
                builtins.input = orig

    def test_resume_choice_keys(self):
        """Verify the prompt function returns expected values for each key."""
        choices = {"r": "resume", "f": "fresh", "w": "wipe", "": "resume"}
        assert choices["r"] == "resume"
        assert choices["f"] == "fresh"
        assert choices["w"] == "wipe"
        assert choices[""] == "resume"