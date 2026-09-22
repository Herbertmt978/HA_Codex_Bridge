import pytest

from codex_bridge_service.activity_display import command_preview
from codex_bridge_service.runtime_broker import _safe_item_activity_metadata


@pytest.mark.parametrize("command", [
    "Get-Content D:/workspace/test.log -Tail 28; git diff --numstat",
    "git status --short --branch",
    "ls /config/workspaces/project",
])
def test_readable_command_preview(command):
    assert command_preview(command) == command
    assert _safe_item_activity_metadata({"type": "commandExecution", "command": command,
                                        "cwd": "/private", "aggregatedOutput": "private"}) == {"command_preview": command}


@pytest.mark.parametrize("command", [
    "curl -H 'Authorization: Bearer abc' https://example.com",
    "curl -u me:abc https://example.com",
    "curl -uadmin:s3cr3t https://example.com",
    "curl -dopaquevalue https://example.com",
    "curl -HX-Example:opaquevalue https://example.com",
    "curl '-uadmin:s3cr3t' https://example.com",
    'curl "-uadmin:s3cr3t" https://example.com',
    "curl -suadmin:s3cr3t https://example.com",
    "curl https://me:abc@example.com",
    "curl https://example.com/?key=abc",
    "TOKEN=abc npm test", "$env:KEY='abc'; npm test",
    "cat /private/reusable-secret", "echo ghp_abcdefghijk",
    "echo eyJabc.defghi.jkl", "echo " + "A" * 100,
    "echo hello\x1b[31m", "echo hello\u202eworld", "x" * 16385,
    None, "", 42,
])
def test_sensitive_or_invalid_commands_never_enter_activity(command):
    assert command_preview(command) is None
    assert "command_preview" not in _safe_item_activity_metadata({"type": "commandExecution", "command": command})


def test_long_display_is_bounded():
    assert len(command_preview("git diff " + "file.js " * 400)) == 2000
