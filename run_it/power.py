"""Measured power on macOS without sudo.

- Whole Mac: SMC power telemetry (`PowerTelemetryData.SystemLoad`, mW) read through `ioreg`.
- Per process: the kernel's energy counter (`rusage_info_v6.ri_energy_nj`) via `proc_pid_rusage`,
  readable for processes owned by the current user.

Both return None where the platform or permissions do not expose the value; callers show "—", never 0.
"""
from __future__ import annotations

import ctypes
import plistlib
import subprocess
import sys

RUSAGE_INFO_V6 = 6
_V6_FIELDS = (
    "user_time system_time pkg_idle_wkups interrupt_wkups pageins wired_size resident_size phys_footprint "
    "proc_start_abstime proc_exit_abstime child_user_time child_system_time child_pkg_idle_wkups "
    "child_interrupt_wkups child_pageins child_elapsed_abstime diskio_bytesread diskio_byteswritten "
    "cpu_time_qos_default cpu_time_qos_maintenance cpu_time_qos_background cpu_time_qos_utility "
    "cpu_time_qos_legacy cpu_time_qos_user_initiated cpu_time_qos_user_interactive billed_system_time "
    "serviced_system_time logical_writes lifetime_max_phys_footprint instructions cycles billed_energy "
    "serviced_energy interval_max_phys_footprint runnable_time flags user_ptime system_ptime pinstructions "
    "pcycles energy_nj penergy_nj").split()


class _RusageV6(ctypes.Structure):
    # The kernel writes the whole v6 struct; the tail is padded generously so newer, longer layouts fit.
    _fields_ = [("uuid", ctypes.c_uint8 * 16), *((name, ctypes.c_uint64) for name in _V6_FIELDS),
                ("tail", ctypes.c_uint64 * 32)]


_libproc = None


def _lib():
    global _libproc
    if _libproc is None and sys.platform == "darwin":
        try:
            _libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
        except OSError:
            _libproc = False
    return _libproc or None


def process_energy_nj(pid: int) -> int | None:
    """Cumulative energy the kernel has billed to `pid`, in nanojoules (also works on an unreaped zombie)."""
    lib = _lib()
    if lib is None:
        return None
    info = _RusageV6()
    if lib.proc_pid_rusage(pid, RUSAGE_INFO_V6, ctypes.byref(info)) != 0:
        return None
    return info.energy_nj


def _signed(value):
    return value - 2**64 if isinstance(value, int) and value >= 2**63 else value  # ioreg prints int64 as uint64


def parse_system_watts(plist: bytes) -> float | None:
    """Whole-Mac draw in W from `ioreg -a` output: SMC SystemLoad on the charger, battery V*I on battery."""
    try:
        entries = plistlib.loads(plist)
    except Exception:  # empty output (no battery service) or a format we do not know
        return None
    for entry in entries if isinstance(entries, list) else [entries]:
        load = _signed((entry.get("PowerTelemetryData") or {}).get("SystemLoad"))
        if entry.get("ExternalConnected") and load and load > 0:
            return load / 1000
        volts, amps = entry.get("Voltage"), _signed(entry.get("InstantAmperage", entry.get("Amperage")))
        if volts and amps and amps < 0:  # discharging: the battery is the only supply
            return volts * -amps / 1e6
    return None


def system_watts() -> float | None:
    if sys.platform != "darwin":
        return None
    try:
        out = subprocess.run(["ioreg", "-rw0", "-c", "AppleSmartBattery", "-a"],
                             capture_output=True, timeout=2, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_system_watts(out)
