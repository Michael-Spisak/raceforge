"""`raceforge worker`: run the team's benchmark/tuning jobs on this computer (spec 0020).

The worker token (``rfw_…``) is stored in ``worker.json`` next to the workspace cache with
owner-only permissions; it can only fetch jobs and report progress/results. Jobs carry the
controller source inline; it runs in a temporary folder through the normal training code
(spec 0013), so a worker computes exactly what the Train tab computes locally.
"""

import json
import os
import platform
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from raceforge import __version__
from raceforge.api.train import BenchConfig, RunResult, Trial, TuneConfig, benchmark, tune
from raceforge.backend.models import WorkerJob
from raceforge.track.quick import QuickTrack
from raceforge.workspace.client import BackendClient, BackendError, OfflineError

HEARTBEAT_S = 30.0
POLL_S = 5.0


class JobCancelledError(Exception):
    pass


@dataclass(frozen=True)
class WorkerConfig:
    server: str
    token: str
    worker_id: str
    name: str
    workspace_id: str


def config_path() -> Path:
    from raceforge.api.workspace import default_root

    return default_root().parent / "worker.json"


def save_config(cfg: WorkerConfig, path: Path | None = None) -> Path:
    p = path or config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # the token is a secret
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(asdict(cfg), f)
    return p


def load_config(path: Path | None = None) -> WorkerConfig | None:
    p = path or config_path()
    if not p.is_file():
        return None
    return WorkerConfig(**json.loads(p.read_text(encoding="utf-8")))


def machine_info() -> dict[str, Any]:
    return {
        "os": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "cpu_cores": os.cpu_count() or 1,
        "raceforge": __version__,
    }


def _bench_config(req: dict[str, Any]) -> BenchConfig:
    race = dict(req.get("race", req))
    quick = race.get("quick")
    return BenchConfig(
        tracks=int(race.get("tracks", 5)),
        length_m=float(race.get("length_m", 25.0)),
        laps=int(race.get("laps", 1)),
        opponents=int(race.get("opponents", 0)),
        max_time_s=float(race.get("max_time_s", 240.0)),
        quick=QuickTrack.model_validate(quick) if quick else None,
    )


def run_job(job: WorkerJob, report: Callable[[dict[str, Any], list[str]], bool]) -> dict[str, Any]:
    """Run one job; ``report(progress, log)`` returns True when the user cancelled."""
    with tempfile.TemporaryDirectory(prefix="raceforge-job-") as tmp:
        controller = Path(tmp) / Path(job.controller_name).name
        controller.write_text(job.controller_source, encoding="utf-8")
        params = None
        if job.params_yaml:
            params = controller.with_suffix(".yaml")
            params.write_text(job.params_yaml, encoding="utf-8")
        bench = _bench_config(job.request)
        if job.kind == "benchmark":
            done: list[RunResult] = []

            def on_run(r: RunResult) -> None:
                done.append(r)
                state = f"finished in {r.time_s:.1f} s" if r.finished else f"DNF ({r.fraction:.0%})"
                if report(
                    {"done": len(done), "total": bench.tracks}, [f"corridor {r.seed}: {state}"]
                ):
                    raise JobCancelledError

            res = benchmark(controller, params, bench, on_run)
            return {
                "score": res.score,
                "finished_rate": res.finished_rate,
                "runs": [asdict(r) for r in res.runs],
            }
        if job.kind == "rl":
            return _run_rl(job, bench, Path(tmp), report)
        trials = int(job.request.get("trials", 30))

        def on_trial(t: Trial, best: Trial) -> None:
            line = f"trial {t.number}: {t.score:.1f} (best {best.score:.1f})"
            if report({"done": max(0, t.number + 1), "total": trials, "best": best.score}, [line]):
                raise JobCancelledError

        out = Path(tmp) / f"{controller.stem}.tuned.yaml"
        cfg = TuneConfig(
            trials=trials,
            timeout_s=job.request.get("timeout_s"),
            train_tracks=int(job.request.get("train_tracks", 3)),
            bench=bench,
            out=out,
        )
        res = tune(controller, cfg, on_trial)
        return {
            "score": res.validation.score,
            "default_score": res.default_validation.score,
            "finished_rate": res.validation.finished_rate,
            "best_params": res.best_params,
            "params_yaml": out.read_text(encoding="utf-8"),
            "trials": [{"number": t.number, "score": t.score} for t in res.trials],
        }


def _run_rl(
    job: WorkerJob,
    bench: BenchConfig,
    tmp: Path,
    report: Callable[[dict[str, Any], list[str]], bool],
) -> dict[str, Any]:
    from raceforge.api.train import EnvConfig, RLConfig, RLProgress, train_ppo

    steps = int(job.request.get("steps", 200_000))

    def on_progress(p: RLProgress) -> None:
        mean = "-" if p.mean_reward is None else f"{p.mean_reward:.1f}"
        progress = {"done": p.steps, "total": steps, "mean_reward": p.mean_reward}
        if report(progress, [f"{p.steps}/{steps} steps, mean reward {mean}"]):
            raise JobCancelledError

    out = tmp / "ppo-policy.yaml"
    env = EnvConfig(
        length_m=bench.length_m,
        laps=bench.laps,
        opponents=bench.opponents,
        max_time_s=min(bench.max_time_s, 120.0),
        quick=bench.quick,
    )
    cfg = RLConfig(
        steps=steps,
        train_tracks=int(job.request.get("train_tracks", 8)),
        env=env,
        bench=bench,
        out=out,
    )
    res = train_ppo(cfg, on_progress)
    v = res.validation
    return {
        "score": v.score if v else None,
        "finished_rate": v.finished_rate if v else None,
        "steps": res.steps,
        "params_yaml": out.read_text(encoding="utf-8"),
        "runs": [asdict(r) for r in v.runs] if v else [],
    }


def _idle() -> bool:
    """Load below half the cores (Unix); always True where the load is unknown."""
    try:
        return os.getloadavg()[0] < (os.cpu_count() or 1) * 0.5
    except (AttributeError, OSError):
        return True


def run_worker(
    client: BackendClient,
    idle_only: bool = False,
    once: bool = False,
    stop: threading.Event | None = None,
    out: Callable[[str], None] = print,
) -> int:
    """Poll for jobs until ``stop`` is set (or after one job with ``once``); returns jobs run."""
    stop = stop or threading.Event()
    info = machine_info()
    last_beat, count = 0.0, 0
    while not stop.is_set():
        try:
            if time.monotonic() - last_beat > HEARTBEAT_S:
                client.worker_heartbeat(info)
                last_beat = time.monotonic()
            job = None if idle_only and not _idle() else client.worker_claim()
        except OfflineError as e:
            out(f"backend not reachable ({e}); retrying")
            stop.wait(POLL_S * 4)
            continue
        if job is None:
            if once:
                return count
            stop.wait(POLL_S)
            continue
        out(f"job {job.id}: {job.kind} {job.controller_name}")
        pending: list[str] = []
        last_report = 0.0

        def report(
            progress: dict[str, Any], log: list[str], job_id: str = job.id, buf: list[str] = pending
        ) -> bool:
            nonlocal last_report
            buf.extend(log)
            for line in log:
                out(f"  {line}")
            if time.monotonic() - last_report < 1.0 and progress.get("done") != progress.get(
                "total"
            ):
                return False
            last_report = time.monotonic()
            cancelled = client.job_progress(job_id, progress, buf[:])
            buf.clear()
            return cancelled

        try:
            result = run_job(job, report)
            client.job_finish(job.id, "done", result)
            out(f"job {job.id}: done")
        except JobCancelledError:
            client.job_finish(job.id, "cancelled")
            out(f"job {job.id}: cancelled")
        except (BackendError, OfflineError):
            raise
        except Exception as e:  # the team's controller may raise anything; report, keep serving
            client.job_finish(job.id, "error", error=f"{type(e).__name__}: {e}")
            out(f"job {job.id}: error {type(e).__name__}: {e}")
        count += 1
        if once:
            return count
    return count
