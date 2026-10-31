from app.services.research_workflow_fsm import allowed_actions, resolve_transition


def test_happy_path_uses_only_declared_transitions():
    assert resolve_transition("draft", "submit_for_review").to_state == "data_review"
    assert resolve_transition("data_review", "approve_data").to_state == "analysis"
    assert resolve_transition("analysis", "submit_results").to_state == "results_review"
    assert resolve_transition("results_review", "complete_computational").to_state == "completed"


def test_invalid_transition_is_rejected():
    decision = resolve_transition("draft", "complete_computational")
    assert decision.allowed is False
    assert decision.to_state is None


def test_block_and_resume_restore_previous_active_state():
    blocked = resolve_transition("analysis", "block")
    assert blocked.allowed is True
    assert blocked.to_state == "blocked"
    resumed = resolve_transition("blocked", "resume", resume_state="analysis")
    assert resumed.allowed is True
    assert resumed.to_state == "analysis"


def test_blocked_state_cannot_resume_without_valid_previous_state():
    decision = resolve_transition("blocked", "resume", resume_state="completed")
    assert decision.allowed is False


def test_terminal_states_do_not_allow_mutating_transitions():
    assert resolve_transition("completed", "cancel").allowed is False
    assert resolve_transition("cancelled", "submit_for_review").allowed is False
    assert allowed_actions("completed") == []
