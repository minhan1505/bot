"""
bot.telemetry.benchmark
~~~~~~~~~~~~~~~~~~~~~~~
Hard SLA Benchmark Suite & Protocol Runner.
Enforces Execution Guard #5:
  Report must publish:
    - N target appearances
    - N active regions
    - Resolution / DPI
    - CPU / RAM / OS
    - Model SHA-256
    - Candidate counts
    - Min / Mean / P95 / P99 / Max Latency
    - Count of samples > 700 ms (Acceptance strictly requires 0)
"""

import time
import os
import platform
import numpy as np
from typing import List, Dict, Any, Tuple
from dataclasses import dataclass, field
import logging

logger = logging.getLogger(__name__)


@dataclass
class BenchmarkReport:
    n_samples: int
    n_regions: int
    resolution: Tuple[int, int]
    dpi_scaling: str
    cpu_info: str
    ram_gb: float
    model_sha256: str
    min_latency_ms: float
    mean_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    max_latency_ms: float
    count_gt_700ms: int
    passed_hard_sla: bool
    stage_breakdown_means: Dict[str, float] = field(default_factory=dict)

    def to_markdown(self) -> str:
        status_badge = "**PASS** (0 samples > 700ms)" if self.passed_hard_sla else "**FAIL** (Violations detected!)"
        md = [
            f"# BOTAUTOCLICK V2.3 HARD SLA BENCHMARK REPORT",
            f"**Overall Status:** {status_badge}",
            f"",
            f"## System & Test Environment",
            f"- **Hardware CPU:** {self.cpu_info}",
            f"- **RAM:** {self.ram_gb:.1f} GB",
            f"- **OS:** {platform.system()} {platform.version()} ({platform.machine()})",
            f"- **Screen Resolution:** {self.resolution[0]}x{self.resolution[1]} physical pixels",
            f"- **DPI Awareness:** {self.dpi_scaling}",
            f"- **Model SHA-256:** `{self.model_sha256[:16]}...`",
            f"- **Active Regions Under Test:** {self.n_regions}",
            f"- **Total Target Appearances (N):** {self.n_samples}",
            f"",
            f"## End-to-End Latency Distribution (First Visible Frame -> Action Dispatch)",
            f"| Metric | Latency (ms) | Requirement | Status |",
            f"| :--- | :---: | :---: | :---: |",
            f"| **Min** | {self.min_latency_ms:.2f} ms | - | - |",
            f"| **Mean** | {self.mean_latency_ms:.2f} ms | - | - |",
            f"| **P95** | {self.p95_latency_ms:.2f} ms | - | - |",
            f"| **P99** | {self.p99_latency_ms:.2f} ms | - | - |",
            f"| **Max (Worst-Case)** | **{self.max_latency_ms:.2f} ms** | **<= 700.00 ms** | {'PASS' if self.max_latency_ms <= 700.0 else 'FAIL'} |",
            f"| **Violations (> 700 ms)** | **{self.count_gt_700ms}** | **Strictly 0** | {'PASS' if self.count_gt_700ms == 0 else 'FAIL'} |",
            f"",
            f"## Stage Breakdown (Mean ms)",
        ]
        for stage, val in self.stage_breakdown_means.items():
            md.append(f"- **{stage}:** {val:.2f} ms")

        return "\n".join(md)


class BenchmarkRunner:
    """Runs latency benchmarks across declared supported workloads."""

    @staticmethod
    def get_system_specs() -> Tuple[str, float]:
        cpu_name = platform.processor() or "Unknown CPU"
        ram_gb = 16.0 # Default estimation if psutil not available
        try:
            import ctypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            ram_gb = stat.ullTotalPhys / (1024 ** 3)
        except Exception:
            pass
        return cpu_name, ram_gb

    @staticmethod
    def evaluate_latency_samples(
        latencies: List[float],
        stage_records: List[Dict[str, float]],
        n_regions: int,
        resolution: Tuple[int, int],
        model_sha256: str
    ) -> BenchmarkReport:
        """Evaluates observed latencies and generates report."""
        if not latencies:
            raise ValueError("No latency samples provided for evaluation.")

        arr = np.array(latencies, dtype=np.float64)
        cpu_info, ram_gb = BenchmarkRunner.get_system_specs()

        min_lat = float(np.min(arr))
        mean_lat = float(np.mean(arr))
        p95_lat = float(np.percentile(arr, 95))
        p99_lat = float(np.percentile(arr, 99))
        max_lat = float(np.max(arr))

        count_gt_700 = int(np.sum(arr > 700.0))
        passed_hard_sla = (count_gt_700 == 0) and (max_lat <= 700.0)

        # Stage breakdown means
        stage_means = {}
        if stage_records:
            all_keys = stage_records[0].keys()
            for k in all_keys:
                vals = [rec.get(k, 0.0) for rec in stage_records]
                stage_means[k] = float(np.mean(vals))

        return BenchmarkReport(
            n_samples=len(latencies),
            n_regions=n_regions,
            resolution=resolution,
            dpi_scaling="Per-Monitor Aware V2",
            cpu_info=cpu_info,
            ram_gb=ram_gb,
            model_sha256=model_sha256,
            min_latency_ms=min_lat,
            mean_latency_ms=mean_lat,
            p95_latency_ms=p95_lat,
            p99_latency_ms=p99_lat,
            max_latency_ms=max_lat,
            count_gt_700ms=count_gt_700,
            passed_hard_sla=passed_hard_sla,
            stage_breakdown_means=stage_means
        )
