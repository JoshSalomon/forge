"""Tests for forge:yolo auto-approval mode."""

import pytest

from forge.models.workflow import ForgeLabel, TicketType
from forge.workflow.feature.state import create_initial_feature_state
from forge.workflow.bug.state import create_initial_bug_state


class TestForgeLabelYolo:
    def test_yolo_label_value(self):
        assert ForgeLabel.YOLO == "forge:yolo"

    def test_yolo_label_is_string(self):
        assert isinstance(ForgeLabel.YOLO, str)


class TestYoloModeDefaultsToFalse:
    def test_feature_state_yolo_mode_defaults_false(self):
        state = create_initial_feature_state("TEST-1")
        assert state.get("yolo_mode") is False

    def test_bug_state_yolo_mode_defaults_false(self):
        state = create_initial_bug_state("BUG-1")
        assert state.get("yolo_mode") is False

    def test_feature_state_yolo_mode_can_be_set_true(self):
        state = create_initial_feature_state("TEST-1", yolo_mode=True)
        assert state["yolo_mode"] is True

    def test_bug_state_yolo_mode_can_be_set_true(self):
        state = create_initial_bug_state("BUG-1", yolo_mode=True)
        assert state["yolo_mode"] is True
