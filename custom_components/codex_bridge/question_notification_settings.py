"""Validate additive opt-in question-notification options without granting authority."""

from collections.abc import Mapping
import re

from .const import (
    CONF_QUESTION_NOTIFICATIONS_ENABLED,
    CONF_QUESTION_NOTIFICATIONS_PERSISTENT,
    CONF_QUESTION_NOTIFICATIONS_PREVIEW,
    CONF_QUESTION_NOTIFICATIONS_TARGETS,
)

_ENTRY_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


def question_notification_settings(options: object) -> dict:
    """Invalid saved values disable delivery; old configurations remain opt-out."""

    default = {
        "enabled": False,
        "persistent": True,
        "preview": False,
        "mobile_targets": [],
    }
    if not isinstance(options, Mapping):
        return default
    enabled = options.get(CONF_QUESTION_NOTIFICATIONS_ENABLED, False)
    persistent = options.get(CONF_QUESTION_NOTIFICATIONS_PERSISTENT, True)
    preview = options.get(CONF_QUESTION_NOTIFICATIONS_PREVIEW, False)
    targets = options.get(CONF_QUESTION_NOTIFICATIONS_TARGETS, [])
    if (
        any(type(value) is not bool for value in (enabled, persistent, preview))
        or not isinstance(targets, list)
        or len(targets) > 8
        or any(
            not isinstance(target, str) or not _ENTRY_ID.fullmatch(target)
            for target in targets
        )
        or len(set(targets)) != len(targets)
    ):
        return default
    return {
        "enabled": enabled,
        "persistent": persistent,
        "preview": preview,
        "mobile_targets": list(targets),
    }
