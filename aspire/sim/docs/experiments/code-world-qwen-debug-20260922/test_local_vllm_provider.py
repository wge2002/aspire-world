#!/usr/bin/env python3
"""Offline tests for the opt-in `model_provider: local-vllm` branch.

No model API, no GPU, no simulator and no network: every assertion is made
against the settings dict and the child environment that
`scripts/libero/native_world_campaign.py` builds.

Run from `aspire/sim`:
  /mnt/home/gewang/code/ASPIRE/aspire/sim/.venv-libero/bin/python3 \
    docs/experiments/code-world-qwen-debug-20260922/test_local_vllm_provider.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts/libero"))

import native_world_campaign as campaign  # noqa: E402

CASE_PATH = REPO / "outputs/libero_fix_loop/_offline_test/case.json"
CONFIG_DIR = REPO / "outputs/libero_fix_loop/_offline_test/cc-config"

# The remote cell as it exists today: `model_provider` absent, apiKeyHelper set.
REMOTE_CASE = {
    "id": "Q0", "suite": "libero_10_pro", "task": "task_00", "condition": "C",
    "skill_library_dir": ".claude/libero/skills",
    "api_key_helper": "/mnt/home/gewang/.config/existing/api_key_helper.sh",
    "model_tag": "claude-opus-4-6", "expected_served_model": "claude-opus-4-6",
    "effort": "high", "context_tokens": 200000, "max_output_tokens": 32000,
    "disable_experimental_betas": "1", "trial_timeout": 1800,
    "gpu": 0, "python_root": "/mnt/home/gewang/code/ASPIRE",
    "sim": str(REPO), "claude_bin": "/usr/local/bin/claude",
    "service_ports": [8100],
}

# The pilot cell: local branch, and deliberately no `api_key_helper` key at all.
LOCAL_CASE = dict(REMOTE_CASE, model_provider="local-vllm",
                  inference_endpoint="http://127.0.0.1:8120",
                  model_tag=campaign.PILOT_MODEL,
                  expected_served_model=campaign.PILOT_MODEL,
                  effort=campaign.PILOT_EFFORT,
                  context_tokens=campaign.PILOT_CONTEXT_TOKENS,
                  max_output_tokens=campaign.PILOT_MAX_OUTPUT_TOKENS)
LOCAL_CASE.pop("api_key_helper")

# A shell carrying another provider's selectors and credentials.
DIRTY_ENV = {
    "PATH": "/usr/bin", "HOME": "/mnt/home/gewang",
    "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_USE_VERTEX": "1",
    "CLAUDE_CODE_USE_FOUNDRY": "1", "CLAUDE_CODE_USE_MANTLE": "1",
    "CLAUDE_CODE_USE_ANTHROPIC_AWS": "1",
    "CLAUDE_CODE_OAUTH_TOKEN": "inherited-oauth",
    "ANTHROPIC_API_KEY": "inherited-key", "ANTHROPIC_AUTH_TOKEN": "inherited-token",
    "ANTHROPIC_CUSTOM_HEADERS": "x-inherited: 1", "ANTHROPIC_BASE_URL": "https://elsewhere",
}


def settings_for(case: dict) -> dict:
    return campaign.native_settings(case, REPO, CASE_PATH)


def environment_for(case: dict) -> dict:
    return campaign.native_environment(case, REPO, CASE_PATH, CONFIG_DIR)


class LocalBranchSettings(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = settings_for(LOCAL_CASE)
        self.env = self.settings["env"]

    def test_no_api_key_helper_and_case_field_not_required(self) -> None:
        self.assertNotIn("api_key_helper", LOCAL_CASE)
        self.assertNotIn("apiKeyHelper", self.settings)

    def test_every_model_alias_points_at_qwen(self) -> None:
        for alias in campaign.LOCAL_VLLM_MODEL_ALIASES:
            self.assertEqual(self.env.get(alias), campaign.PILOT_MODEL, alias)
        for alias in ("ANTHROPIC_CUSTOM_MODEL_OPTION", "ANTHROPIC_DEFAULT_FABLE_MODEL"):
            self.assertIn(alias, campaign.LOCAL_VLLM_MODEL_ALIASES)
        self.assertEqual(self.settings["model"], campaign.PILOT_MODEL)
        self.assertEqual(LOCAL_CASE["expected_served_model"], campaign.PILOT_MODEL)

    def test_placeholders_endpoint_and_effort(self) -> None:
        self.assertEqual(self.env["ANTHROPIC_BASE_URL"], "http://127.0.0.1:8120")
        self.assertEqual(self.env["ANTHROPIC_API_KEY"], campaign.LOCAL_VLLM_PLACEHOLDER)
        self.assertEqual(self.env["ANTHROPIC_AUTH_TOKEN"], campaign.LOCAL_VLLM_PLACEHOLDER)
        self.assertEqual(self.env["CLAUDE_CODE_ALWAYS_ENABLE_EFFORT"], "1")
        self.assertEqual(self.env["CLAUDE_CODE_EFFORT_LEVEL"], campaign.PILOT_EFFORT)
        self.assertEqual(self.settings["effortLevel"], campaign.PILOT_EFFORT)

    def test_compaction_context_and_output_stay_explicit(self) -> None:
        self.assertEqual(self.env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "1000000")
        self.assertEqual(self.env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "1000000")
        self.assertEqual(self.env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"], "64000")

    def test_guard_hooks_and_permission_checks_retained(self) -> None:
        self.assertEqual(self.settings["hooks"],
                         campaign.guard_settings(CASE_PATH, REPO)["hooks"])
        command = campaign.native_command(LOCAL_CASE, "prompt", self.settings)
        self.assertEqual(command[0], LOCAL_CASE["claude_bin"])
        self.assertIn("--permission-mode", command)
        self.assertEqual(command[command.index("--permission-mode") + 1], "dontAsk")
        self.assertNotIn("--dangerously-skip-permissions", command)
        for tool in campaign.ALLOWED_TOOLS:
            self.assertIn(tool, command)
        self.assertEqual(command[command.index("--model") + 1], campaign.PILOT_MODEL)
        self.assertEqual(command[command.index("--effort") + 1], campaign.PILOT_EFFORT)
        self.assertFalse(any("claude_with_local_model" in part for part in command))


class LocalBranchRejections(unittest.TestCase):
    def test_rejects_non_loopback_destinations(self) -> None:
        for endpoint in ("https://llmapi.roboscience.xyz:18443",
                         "http://10.0.0.7:8120", "http://example.com:8120",
                         "http://127.0.0.1:8120/v1", "http://127.0.0.1",
                         "https://127.0.0.1:8120", ""):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(campaign.ProtocolFailure):
                    settings_for(dict(LOCAL_CASE, inference_endpoint=endpoint))

    def test_missing_endpoint_field_is_rejected(self) -> None:
        case = dict(LOCAL_CASE)
        case.pop("inference_endpoint")
        with self.assertRaises(campaign.ProtocolFailure):
            settings_for(case)

    def test_accepts_localhost_and_trailing_slash(self) -> None:
        settings = settings_for(dict(LOCAL_CASE,
                                     inference_endpoint="http://localhost:8120/"))
        self.assertEqual(settings["env"]["ANTHROPIC_BASE_URL"], "http://localhost:8120")

    def test_rejects_wrong_pilot_effort(self) -> None:
        with self.assertRaises(campaign.ProtocolFailure):
            settings_for(dict(LOCAL_CASE, effort="high"))

    def test_rejects_other_pilot_drift(self) -> None:
        for override in ({"model_tag": "claude-opus-4-6"},
                         {"expected_served_model": "qwen3.8-flash"},
                         {"context_tokens": 200000},
                         {"max_output_tokens": 32000}):
            with self.subTest(**override):
                with self.assertRaises(campaign.ProtocolFailure):
                    settings_for(dict(LOCAL_CASE, **override))


class LocalBranchEnvironment(unittest.TestCase):
    def test_provider_flags_and_inherited_auth_removed(self) -> None:
        with mock.patch.dict(campaign.os.environ, DIRTY_ENV, clear=True):
            env = environment_for(LOCAL_CASE)
        for flag in campaign.INHERITED_PROVIDER_FLAGS:
            self.assertNotIn(flag, env)
        for leaked in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY",
                       "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_CUSTOM_HEADERS",
                       "ANTHROPIC_BASE_URL"):
            self.assertNotIn(leaked, env)
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], str(CONFIG_DIR))
        self.assertEqual(env["HOME"], DIRTY_ENV["HOME"])


class UnflaggedBehaviorUnchanged(unittest.TestCase):
    def test_remote_settings_are_byte_identical_to_the_existing_recipe(self) -> None:
        expected = {
            "apiKeyHelper": REMOTE_CASE["api_key_helper"],
            "env": {
                "ANTHROPIC_BASE_URL": campaign.ENDPOINT,
                "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-4-6",
                "ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-opus-4-6",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": "claude-opus-4-6",
                "ANTHROPIC_SMALL_FAST_MODEL": "claude-opus-4-6",
                "ANTHROPIC_MODEL": "claude-opus-4-6",
                "CLAUDE_CODE_SUBAGENT_MODEL": "claude-opus-4-6",
                "CLAUDE_CODE_EFFORT_LEVEL": "high",
                "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "200000",
                "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "32000",
                "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "200000",
                "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS": "43200000",
                "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1",
                "BASH_DEFAULT_TIMEOUT_MS": "1920000",
                "BASH_MAX_TIMEOUT_MS": "2100000",
            },
            "model": "claude-opus-4-6",
            "effortLevel": "high",
            "includeCoAuthoredBy": False,
            **campaign.guard_settings(CASE_PATH, REPO),
        }
        self.assertEqual(settings_for(REMOTE_CASE), expected)

    def test_remote_branch_gains_no_local_placeholders_or_new_aliases(self) -> None:
        env = settings_for(REMOTE_CASE)["env"]
        self.assertNotIn(campaign.LOCAL_VLLM_PLACEHOLDER, env.values())
        for alias in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                      "ANTHROPIC_CUSTOM_MODEL_OPTION", "ANTHROPIC_DEFAULT_FABLE_MODEL",
                      "CLAUDE_CODE_ALWAYS_ENABLE_EFFORT",
                      "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "ENABLE_TOOL_SEARCH"):
            self.assertNotIn(alias, env)

    def test_remote_environment_is_untouched_by_the_new_stripping(self) -> None:
        """Only the local branch strips provider selectors.

        `CLAUDE_CODE_USE_ANTHROPIC_AWS` is excluded because the pre-existing
        credential filter already removes it from both branches (it contains
        "ANTHROPIC"); asserting it survives would test the old filter wrongly.
        """
        with mock.patch.dict(campaign.os.environ, DIRTY_ENV, clear=True):
            env = environment_for(REMOTE_CASE)
        for flag in campaign.INHERITED_PROVIDER_FLAGS:
            if flag == "CLAUDE_CODE_USE_ANTHROPIC_AWS":
                self.assertNotIn(flag, env)
                continue
            self.assertEqual(env.get(flag), "1", flag)

    def test_other_provider_values_do_not_take_the_local_branch(self) -> None:
        for value in (None, "", "remote", "anthropic"):
            case = dict(REMOTE_CASE) if value is None else dict(REMOTE_CASE,
                                                                model_provider=value)
            with self.subTest(model_provider=value):
                self.assertFalse(campaign.is_local_vllm(case))
                self.assertIn("apiKeyHelper", settings_for(case))


if __name__ == "__main__":
    unittest.main(verbosity=2)
