from custom_components.codex_bridge.const import (
    CONF_QUESTION_NOTIFICATIONS_ENABLED,
    CONF_QUESTION_NOTIFICATIONS_PERSISTENT,
    CONF_QUESTION_NOTIFICATIONS_PREVIEW,
    CONF_QUESTION_NOTIFICATIONS_TARGETS,
)
from custom_components.codex_bridge.question_notification_settings import (
    question_notification_settings,
)
import pytest


def test_old_options_are_opt_out_and_target_lists_are_copied():
    assert question_notification_settings({}) == {
        "enabled": False,
        "persistent": True,
        "preview": False,
        "mobile_targets": [],
    }
    options = {
        CONF_QUESTION_NOTIFICATIONS_ENABLED: True,
        CONF_QUESTION_NOTIFICATIONS_TARGETS: ["registered_phone"],
    }
    settings = question_notification_settings(options)
    assert settings["enabled"] is True
    options[CONF_QUESTION_NOTIFICATIONS_TARGETS].clear()
    assert settings["mobile_targets"] == ["registered_phone"]


@pytest.mark.parametrize(
    "invalid",
    [
        {CONF_QUESTION_NOTIFICATIONS_ENABLED: "yes"},
        {CONF_QUESTION_NOTIFICATIONS_PERSISTENT: 1},
        {CONF_QUESTION_NOTIFICATIONS_PREVIEW: "true"},
        {CONF_QUESTION_NOTIFICATIONS_TARGETS: ["registered_phone"] * 2},
        {CONF_QUESTION_NOTIFICATIONS_TARGETS: ["notify.arbitrary"]},
        {CONF_QUESTION_NOTIFICATIONS_TARGETS: [f"phone_{index}" for index in range(9)]},
        {CONF_QUESTION_NOTIFICATIONS_TARGETS: "registered_phone"},
    ],
)
def test_invalid_saved_options_disable_notices(invalid):
    assert (
        question_notification_settings(
            {CONF_QUESTION_NOTIFICATIONS_ENABLED: True, **invalid}
        )["enabled"]
        is False
    )
