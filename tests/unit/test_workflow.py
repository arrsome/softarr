from softarr.models.release import WorkflowState
from softarr.services.release_service import VALID_TRANSITIONS


class TestWorkflowTransitions:
    """Verify the state machine allows and blocks the right transitions."""

    def test_discovered_can_stage(self):
        assert WorkflowState.STAGED in VALID_TRANSITIONS[WorkflowState.DISCOVERED]

    def test_discovered_can_reject(self):
        assert WorkflowState.REJECTED in VALID_TRANSITIONS[WorkflowState.DISCOVERED]

    def test_discovered_cannot_approve(self):
        assert WorkflowState.APPROVED not in VALID_TRANSITIONS[WorkflowState.DISCOVERED]

    def test_staged_can_review(self):
        assert WorkflowState.UNDER_REVIEW in VALID_TRANSITIONS[WorkflowState.STAGED]

    def test_staged_can_reject(self):
        assert WorkflowState.REJECTED in VALID_TRANSITIONS[WorkflowState.STAGED]

    def test_staged_cannot_download(self):
        assert (
            WorkflowState.QUEUED_FOR_DOWNLOAD
            not in VALID_TRANSITIONS[WorkflowState.STAGED]
        )

    def test_under_review_can_approve(self):
        assert WorkflowState.APPROVED in VALID_TRANSITIONS[WorkflowState.UNDER_REVIEW]

    def test_under_review_can_reject(self):
        assert WorkflowState.REJECTED in VALID_TRANSITIONS[WorkflowState.UNDER_REVIEW]

    def test_approved_can_queue(self):
        assert (
            WorkflowState.QUEUED_FOR_DOWNLOAD
            in VALID_TRANSITIONS[WorkflowState.APPROVED]
        )

    def test_approved_cannot_stage(self):
        assert WorkflowState.STAGED not in VALID_TRANSITIONS[WorkflowState.APPROVED]

    def test_rejected_can_restage(self):
        assert WorkflowState.STAGED in VALID_TRANSITIONS[WorkflowState.REJECTED]

    def test_rejected_cannot_approve(self):
        assert WorkflowState.APPROVED not in VALID_TRANSITIONS[WorkflowState.REJECTED]

    def test_queued_can_complete(self):
        assert (
            WorkflowState.DOWNLOADED
            in VALID_TRANSITIONS[WorkflowState.QUEUED_FOR_DOWNLOAD]
        )

    def test_queued_can_rollback_to_approved(self):
        assert (
            WorkflowState.APPROVED
            in VALID_TRANSITIONS[WorkflowState.QUEUED_FOR_DOWNLOAD]
        )

    def test_downloaded_is_terminal(self):
        assert len(VALID_TRANSITIONS[WorkflowState.DOWNLOADED]) == 0

    def test_all_states_have_transition_entry(self):
        for state in WorkflowState:
            assert state in VALID_TRANSITIONS, f"{state} missing from VALID_TRANSITIONS"
