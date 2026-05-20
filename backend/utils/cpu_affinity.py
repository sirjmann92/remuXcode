"""CPU affinity utilities for hybrid-core architectures (Intel P/E, ARM big.LITTLE).

Detection uses two methods in priority order:

1. **core_type sysfs** (Linux 5.18+) — reads
   ``/sys/devices/system/cpu/cpu*/topology/core_type`` which the kernel sets
   to "Core" (P-core) or "Atom" (E-core).  This is the authoritative source
   and immune to Intel's Preferred Core binning (where the top 1–2 P-core
   pairs report a slightly higher ``cpuinfo_max_freq`` than the other P-cores,
   causing the naïve "pick max frequency" approach to only find 4 threads).

2. **Frequency-gap analysis** — reads ``cpuinfo_max_freq`` and finds the tier
   boundary with the largest *relative* frequency gap.  A gap ≥ 10% signals a
   genuine P→E boundary; smaller gaps (P-core binning variation, ~1–5%) are
   treated as intra-tier noise.  Falls back to ([], False) if sysfs is absent.

On homogeneous CPUs (AMD Ryzen, older Intel, VMs) is_hybrid=False and the
P-core set equals all CPUs — affinity pinning becomes a no-op.
"""

from collections.abc import Callable
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Module-level cache — CPU topology doesn't change at runtime.
_cache: tuple[list[int], bool] | None = None


def _detect_by_core_type() -> tuple[list[int], bool] | None:
    """Primary: use core_type sysfs (Linux 5.18+, Intel 12th gen+).

    Returns (p_core_ids, is_hybrid) if all CPUs were readable, else None.
    """
    cpu_paths = sorted(Path("/sys/devices/system/cpu").glob("cpu[0-9]*"))
    if not cpu_paths:
        return None

    p_cores: list[int] = []
    e_cores: list[int] = []

    for cpu_path in cpu_paths:
        ct_file = cpu_path / "topology" / "core_type"
        try:
            cpu_id = int(cpu_path.name[3:])
            core_type = ct_file.read_text().strip()
            if core_type == "Core":
                p_cores.append(cpu_id)
            else:  # "Atom" = E-core
                e_cores.append(cpu_id)
        except (OSError, ValueError):
            # File absent (older kernel) or unreadable — fall through to freq method
            return None

    if not p_cores:
        return None

    is_hybrid = bool(e_cores)
    return sorted(p_cores), is_hybrid


def _detect_by_freq_gap() -> tuple[list[int], bool]:
    """Fallback: split P-cores from E-cores using largest relative frequency gap.

    Reads cpuinfo_max_freq for each logical CPU and sorts the unique frequency
    tiers.  The boundary with the largest relative gap (freq_high - freq_low) /
    freq_high is used as the P/E split point.  A gap < 10% indicates all tiers
    belong to the same core type (homogeneous CPU).
    """
    freq_by_cpu: dict[int, int] = {}
    for cpu_path in sorted(Path("/sys/devices/system/cpu").glob("cpu[0-9]*")):
        freq_file = cpu_path / "cpufreq" / "cpuinfo_max_freq"
        if freq_file.exists():
            try:
                cpu_id = int(cpu_path.name[3:])
                freq_by_cpu[cpu_id] = int(freq_file.read_text().strip())
            except (ValueError, OSError):
                continue

    if not freq_by_cpu:
        logger.debug("CPU frequency info unavailable — P-core detection skipped")
        return [], False

    unique_freqs = sorted(set(freq_by_cpu.values()), reverse=True)

    if len(unique_freqs) == 1:
        # All threads at the same frequency: homogeneous CPU
        logger.debug(
            "Homogeneous CPU: %d threads at %d kHz — P-core pinning is a no-op",
            len(freq_by_cpu),
            unique_freqs[0],
        )
        return sorted(freq_by_cpu.keys()), False

    # Find the tier boundary with the largest relative frequency gap.
    # P→E gaps are typically 20–40%; P-core binning variation is ~1–5%.
    best_gap_ratio = 0.0
    split_threshold = unique_freqs[-1]  # default: last (lowest) tier
    for i in range(len(unique_freqs) - 1):
        gap_ratio = (unique_freqs[i] - unique_freqs[i + 1]) / unique_freqs[i]
        if gap_ratio > best_gap_ratio:
            best_gap_ratio = gap_ratio
            split_threshold = unique_freqs[i + 1]

    if best_gap_ratio < 0.10:
        # All gaps are small — treat as homogeneous (e.g. binning-only variation)
        logger.debug(
            "Largest frequency gap %.1f%% < 10%% — treating as homogeneous CPU",
            best_gap_ratio * 100,
        )
        return sorted(freq_by_cpu.keys()), False

    # CPUs with frequency strictly above the split threshold are P-cores
    p_core_ids = sorted(
        cpu for cpu, freq in freq_by_cpu.items() if freq > split_threshold
    )
    return p_core_ids, True


def get_cpu_info() -> tuple[list[int], bool]:
    """Return (p_core_ids, is_hybrid).

    p_core_ids: sorted logical CPU IDs belonging to the performance (P) cores.
    is_hybrid:  True when distinct P-core and E-core populations were detected.

    Results are cached after the first call.
    Falls back to ([], False) when no sysfs data is available.
    """
    global _cache
    if _cache is not None:
        return _cache

    result = _detect_by_core_type()
    if result is not None:
        p_core_ids, is_hybrid = result
        method = "core_type"
    else:
        p_core_ids, is_hybrid = _detect_by_freq_gap()
        method = "freq-gap"

    if is_hybrid:
        total = len(p_core_ids)
        logger.info(
            "Hybrid CPU detected (%s): %d P-core threads (IDs: %s)",
            method,
            total,
            p_core_ids,
        )
    elif p_core_ids:
        logger.debug(
            "Homogeneous CPU (%s): %d threads — P-core pinning is a no-op",
            method,
            len(p_core_ids),
        )

    _cache = p_core_ids, is_hybrid
    return _cache


def make_affinity_fn(p_core_ids: list[int]) -> Callable[[], None] | None:
    """Build a preexec_fn that pins a child process to *p_core_ids*.

    Intersects with the current process's allowed CPUs so it is safe inside
    Docker containers that restrict CPUs via cpuset.

    Returns None when:
    - *p_core_ids* is empty
    - ``os.sched_setaffinity`` is unavailable (non-Linux)
    - the intersection with the container's cpuset is empty
    """
    if not p_core_ids or not hasattr(os, "sched_setaffinity"):
        return None

    try:
        allowed = os.sched_getaffinity(0)
    except OSError:
        allowed = set(p_core_ids)

    target = allowed & set(p_core_ids)
    if not target:
        logger.warning(
            "No P-cores available within the current CPU affinity mask %s — pinning skipped",
            sorted(allowed),
        )
        return None

    def _pin() -> None:
        try:
            os.sched_setaffinity(0, target)
        except OSError as exc:
            logger.warning("CPU affinity set failed: %s", exc)

    return _pin
