from opensquad.tools.system import _inject_git_no_pager, _prepare_shell_command, _strip_interactive_pager


def test_strip_more_and_less_tails():
    assert _strip_interactive_pager("git diff | more") == "git diff"
    assert _strip_interactive_pager("npx tsc --noEmit | more") == "npx tsc --noEmit"
    assert _strip_interactive_pager("git log --oneline | less") == "git log --oneline"
    assert _strip_interactive_pager("echo hello") == "echo hello"
    assert _strip_interactive_pager("dir /b | more.com") == "dir /b"


def test_inject_git_no_pager():
    assert _inject_git_no_pager("git diff --stat") == "git --no-pager diff --stat"
    assert _inject_git_no_pager("git.exe status") == "git.exe --no-pager status"
    assert _inject_git_no_pager("cd src && git log -1") == "cd src && git --no-pager log -1"
    assert _inject_git_no_pager("git --no-pager diff") == "git --no-pager diff"
    assert _inject_git_no_pager("echo hello") == "echo hello"


def test_prepare_strips_pager_then_disables_git_pager():
    assert _prepare_shell_command("git diff --stat | more") == "git --no-pager diff --stat"
