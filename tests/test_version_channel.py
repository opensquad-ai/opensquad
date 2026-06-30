"""Tests for :mod:`opensquad.utils.version_channel`.

The release-channel detection drives the ``/version`` endpoint's
behaviour: it must NOT tell a ``dev`` user to upgrade to an older
``stable`` release. These tests pin the classification so a future
refactor can't silently regress it.
"""

from opensquad.utils.version_channel import (
    CHECKABLE_CHANNELS,
    detect_channel,
    should_check_for_updates,
)

# ── detect_channel ───────────────────────────────────────────────────────


class TestDetectChannel:
    def test_stable_plain(self):
        assert detect_channel("0.1.0") == "stable"
        assert detect_channel("1.2.3") == "stable"
        assert detect_channel("0.10.20") == "stable"
        assert detect_channel("100.200.300") == "stable"

    def test_stable_postrelease(self):
        # X.Y.Z.postN is still stable (post-release of a final)
        assert detect_channel("0.1.0.post0") == "stable"
        assert detect_channel("0.1.0.post5") == "stable"
        assert detect_channel("1.0.0.post10") == "stable"

    def test_dev(self):
        # The classic dev-branch shape
        assert detect_channel("0.2.0.dev0") == "dev"
        assert detect_channel("0.1.1.dev0") == "dev"
        assert detect_channel("1.0.0.dev42") == "dev"
        assert detect_channel("0.3.0.dev123") == "dev"

    def test_pre_release_alpha_beta_rc(self):
        # PEP 440 pre-releases are a / b / rc, NOT dev.
        # But dev of a pre-release (e.g. 0.1.0a2.dev0) is treated as
        # 'dev' — a feature branch still working on a not-yet-tagged
        # alpha. In our branching model the dev branch bumps straight
        # to X.Y.Z.dev0, so the "dev of a pre-release" shape is rare,
        # but if it happens the user is clearly still developing.
        assert detect_channel("0.1.0a1") == "pre-release"
        assert detect_channel("0.1.0a2.dev0") == "dev"
        assert detect_channel("0.1.0b1") == "pre-release"
        assert detect_channel("0.1.0rc1") == "pre-release"
        assert detect_channel("1.0.0rc2") == "pre-release"

    def test_local(self):
        # PEP 440 local version segment marks a local build
        assert detect_channel("0.1.0+local.abc") == "local"
        assert detect_channel("1.0.0+ci.123") == "local"
        assert detect_channel("0.2.0.dev0+abc") == "dev"  # dev wins over local

    def test_unknown(self):
        assert detect_channel("") == "unknown"
        assert detect_channel("unknown") == "unknown"
        assert detect_channel("not-a-version") == "unknown"
        # garbage that packaging.version rejects
        assert detect_channel("0.0.0-@@@") == "unknown"
        assert detect_channel("   ") == "unknown"


# ── should_check_for_updates ────────────────────────────────────────────


class TestShouldCheckForUpdates:
    def test_stable_should_check(self):
        check, channel, reason = should_check_for_updates("0.1.0")
        assert check is True
        assert channel == "stable"
        assert reason is None

        check, channel, reason = should_check_for_updates("0.1.0.post0")
        assert check is True
        assert channel == "stable"
        assert reason is None

    def test_dev_should_not_check(self):
        check, channel, reason = should_check_for_updates("0.2.0.dev0")
        assert check is False
        assert channel == "dev"
        assert reason is not None
        assert "dev" in reason
        assert "0.2.0.dev0" in reason

    def test_pre_release_should_not_check(self):
        check, channel, reason = should_check_for_updates("0.1.0b1")
        assert check is False
        assert channel == "pre-release"
        assert reason is not None
        assert "pre-release" in reason

    def test_local_should_not_check(self):
        check, channel, reason = should_check_for_updates("0.1.0+local.abc")
        assert check is False
        assert channel == "local"
        assert reason is not None
        assert "local" in reason

    def test_unknown_should_not_check(self):
        # Defensive: bad version string should NOT trigger a network call
        check, channel, reason = should_check_for_updates("garbage")
        assert check is False
        assert channel == "unknown"
        assert reason is not None

    def test_checkable_channels_constant(self):
        # The constant is used by the /version route. Only "stable" is
        # checkable; everything else is opt-out by default.
        assert frozenset({"stable"}) == CHECKABLE_CHANNELS


# ── The dev-branch bug we are fixing ─────────────────────────────────────


class TestDevBranchRegression:
    """Before this fix, /version on a dev branch would tell the user to
    "upgrade" to 0.1.0 — which is OLDER than 0.2.0.dev0 in PEP 440
    ordering. The fix is that dev should not even consult the
    upstream API."""

    def test_dev_branch_does_not_trigger_github_call(self):
        # The decision lives in this module; the route layer is a
        # thin wrapper. If should_check_for_updates returns False for
        # dev versions, the route never makes the httpx call.
        check, _, _ = should_check_for_updates("0.2.0.dev0")
        assert check is False, (
            "BUG: dev branch must not trigger GitHub update lookup; "
            "the version it would receive (e.g. 0.1.0) is OLDER than "
            "the dev version it is running."
        )

    def test_hotfix_branch_does_not_trigger_github_call(self):
        # Same problem on a hotfix/0.1.1-fix branch where the version
        # is something like 0.1.1.dev0.
        check, _, _ = should_check_for_updates("0.1.1.dev0")
        assert check is False

    def test_main_branch_does_check(self):
        # main is on 0.1.0.post0 — should be a stable channel and
        # the user should be told if 0.1.1 ships.
        check, channel, _ = should_check_for_updates("0.1.0.post0")
        assert check is True
        assert channel == "stable"

    def test_release_branch_does_check(self):
        # release/0.1.0 is on 0.1.0.post0; same as main: check is on.
        # release-line users want to know when 0.1.1 ships.
        check, channel, _ = should_check_for_updates("0.1.0.post0")
        assert check is True
        assert channel == "stable"
