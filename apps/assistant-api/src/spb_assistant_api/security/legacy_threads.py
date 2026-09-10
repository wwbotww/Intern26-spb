"""Opt-in Linux/amd64 thread compatibility WITHOUT removing the parent filter.

Old Docker returns EPERM for unknown clone3; glibc then cannot fall back to clone.
Stack an additional denial (ENOSYS) for clone3. Every other syscall remains subject
to Docker's original filter; this must never be used without that parent filter.
Install once, before importing the application / creating any worker threads.
"""

import ctypes
import errno
import platform
import threading

PR_GET_SECCOMP = 21
PR_SET_SECCOMP = 22
PR_GET_NO_NEW_PRIVS = 39
SECCOMP_MODE_FILTER = 2
AUDIT_ARCH_X86_64 = 0xC000003E
CLONE3 = 435
RET_ALLOW = 0x7FFF0000
RET_ENOSYS = 0x00050000 | errno.ENOSYS

# Classic BPF: check ABI first, then deny clone3; ALLOW here cannot override a
# denial in an existing parent filter. Same-precedence errno uses the newest data.
INSTRUCTIONS = (
    (0x20, 0, 0, 4), (0x15, 0, 3, AUDIT_ARCH_X86_64),
    (0x20, 0, 0, 0), (0x15, 0, 1, CLONE3),
    (0x06, 0, 0, RET_ENOSYS), (0x06, 0, 0, RET_ALLOW),
)


class _Filter(ctypes.Structure):
    _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte), ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint32)]


class _Program(ctypes.Structure):
    _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(_Filter))]


def install_thread_compatibility_filter() -> None:
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "amd64"} or ctypes.sizeof(ctypes.c_void_p) != 8:
        raise RuntimeError("Legacy thread compatibility requires Linux amd64")
    if threading.active_count() != 1:
        raise RuntimeError("Legacy thread compatibility must precede worker threads")
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    if libc.prctl(PR_GET_SECCOMP, 0, 0, 0, 0) != SECCOMP_MODE_FILTER:
        raise RuntimeError("Existing container seccomp filter is required")
    if libc.prctl(PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0) != 1:
        raise RuntimeError("Container no-new-privileges is required")
    instructions = (_Filter * len(INSTRUCTIONS))(*(_Filter(*item) for item in INSTRUCTIONS))
    program = _Program(len(INSTRUCTIONS), instructions)
    if libc.prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, ctypes.byref(program), 0, 0) != 0:
        raise RuntimeError("Unable to install additional thread compatibility denial")
    # Invalid null arguments cannot create a thread even if a platform is wrong.
    if libc.syscall(CLONE3, 0, 0) != -1 or ctypes.get_errno() != errno.ENOSYS:
        raise RuntimeError("Thread compatibility denial did not take effect")
