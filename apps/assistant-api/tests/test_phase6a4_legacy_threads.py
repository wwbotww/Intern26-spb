import ctypes
import errno

import pytest

from spb_assistant_api.security import legacy_threads as module


def evaluate(arch, number):
    index = 0
    accumulator = 0
    while index < len(module.INSTRUCTIONS):
        code, yes, no, value = module.INSTRUCTIONS[index]
        if code == 0x20:
            accumulator = {0: number, 4: arch}[value]
        elif code == 0x15:
            index += yes if accumulator == value else no
        elif code == 0x06:
            return value
        else:
            raise AssertionError("Unexpected BPF opcode")
        index += 1
    raise AssertionError("BPF did not return")


def test_filter_only_adds_clone3_denial_and_respects_abi():
    assert ctypes.sizeof(module._Filter) == 8
    assert evaluate(module.AUDIT_ARCH_X86_64, module.CLONE3) == module.RET_ENOSYS
    for number in range(512):
        if number != module.CLONE3:
            assert evaluate(module.AUDIT_ARCH_X86_64, number) == module.RET_ALLOW
        assert evaluate(0x40000003, number) == module.RET_ALLOW  # i386: parent policy unchanged
        assert evaluate(0xC00000B7, number) == module.RET_ALLOW  # arm64: no numeric aliasing


class FakeLibc:
    def __init__(self, *, seccomp=2, no_privs=1, install=0):
        self.seccomp = seccomp
        self.no_privs = no_privs
        self.install = install
        self.calls = []
        self.syscall = type("Syscall", (), {"__call__": lambda self, *args: -1})()

    def prctl(self, operation, *args):
        self.calls.append(operation)
        return {module.PR_GET_SECCOMP: self.seccomp, module.PR_GET_NO_NEW_PRIVS: self.no_privs, module.PR_SET_SECCOMP: self.install}[operation]


def configure(monkeypatch, libc):
    monkeypatch.setattr(module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(module.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(module.threading, "active_count", lambda: 1)
    monkeypatch.setattr(module.ctypes, "CDLL", lambda *args, **kwargs: libc)
    monkeypatch.setattr(module.ctypes, "get_errno", lambda: errno.ENOSYS)


@pytest.mark.parametrize("seccomp,no_privs", [(0, 1), (1, 1), (2, 0)])
def test_refuses_without_parent_filter_and_no_new_privileges(monkeypatch, seccomp, no_privs):
    libc = FakeLibc(seccomp=seccomp, no_privs=no_privs)
    configure(monkeypatch, libc)
    with pytest.raises(RuntimeError):
        module.install_thread_compatibility_filter()
    assert module.PR_SET_SECCOMP not in libc.calls


@pytest.mark.parametrize("field,value", [("system", "Darwin"), ("machine", "aarch64"), ("threads", 2)])
def test_refuses_unsupported_platform_or_already_started_threads(monkeypatch, field, value):
    libc = FakeLibc()
    configure(monkeypatch, libc)
    if field == "threads":
        monkeypatch.setattr(module.threading, "active_count", lambda: value)
    else:
        monkeypatch.setattr(module.platform, field, lambda: value)
    with pytest.raises(RuntimeError):
        module.install_thread_compatibility_filter()
    assert libc.calls == []


@pytest.mark.parametrize("failure", ["install", "verification"])
def test_install_or_verification_failure_never_launches_application(monkeypatch, failure):
    libc = FakeLibc(install=-1 if failure == "install" else 0)
    configure(monkeypatch, libc)
    if failure == "verification":
        monkeypatch.setattr(module.ctypes, "get_errno", lambda: errno.EPERM)
    with pytest.raises(RuntimeError):
        module.install_thread_compatibility_filter()


def test_install_checks_preconditions_before_adding_denial(monkeypatch):
    libc = FakeLibc()
    configure(monkeypatch, libc)
    module.install_thread_compatibility_filter()
    assert libc.calls == [module.PR_GET_SECCOMP, module.PR_GET_NO_NEW_PRIVS, module.PR_SET_SECCOMP]
