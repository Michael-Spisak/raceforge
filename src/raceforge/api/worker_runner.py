"""`raceforge worker`: run the team's benchmark/tuning jobs on this computer (spec 0020).

The worker token (``rfw_…``) is stored in ``worker.json`` next to the workspace cache with
owner-only permissions; it can only fetch jobs and report progress/results. Jobs carry the
controller source inline; it runs in a temporary folder through the normal training code
(spec 0013), so a worker computes exactly what the Train tab computes locally.

Part C: the worker only takes jobs while its :class:`WorkerPolicy` allows it, keeps heartbeating
while a job runs (the backend's lease), pauses a job after the current race/trial when the
computer is needed again, and continues jobs from the partial result of an earlier attempt.
"""

import contextlib
import json
import os
import platform
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from raceforge import __version__
from raceforge.api.models import WorkerPolicy
from raceforge.api.train import BenchConfig, RunResult, Trial, TuneConfig, benchmark, tune
from raceforge.api.worker_policy import Availability, Probe, SystemProbe, decide
from raceforge.backend.models import WorkerJob
from raceforge.track.quick import QuickTrack
from raceforge.workspace.client import BackendClient, BackendError, OfflineError

HEARTBEAT_S = 30.0
POLL_S = 5.0
FINISH_RETRY_S = 120.0  # keep trying to deliver a result while the backend is unreachable


class JobCancelledError(Exception):
    pass


class JobPausedError(Exception):
    """The computer is needed (policy) or the worker stops: hand the job back with its partial."""


class JobLostError(Exception):
    """The backend gave the job to someone else (our lease expired)."""


@dataclass(frozen=True)
class WorkerConfig:
    server: str
    token: str
    worker_id: str
    name: str
    workspace_id: str
    policy: dict[str, Any] = field(default_factory=dict[str, Any])  # WorkerPolicy (part C)

    def worker_policy(self) -> WorkerPolicy:
        return WorkerPolicy.model_validate(self.policy)


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


def _bench_config(req: dict[str, Any], processes: int = 0) -> BenchConfig:
    race = dict(req.get("race", req))
    quick = race.get("quick")
    return BenchConfig(
        tracks=int(race.get("tracks", 5)),
        length_m=float(race.get("length_m", 25.0)),
        laps=int(race.get("laps", 1)),
        opponents=int(race.get("opponents", 0)),
        max_time_s=float(race.get("max_time_s", 240.0)),
        workers=processes,
        quick=QuickTrack.model_validate(quick) if quick else None,
    )


Report = Callable[[dict[str, Any], list[str], dict[str, Any] | None], None]
"""``report(progress, log, partial)``; raises JobCancelledError / JobPausedError / JobLostError.
``partial`` None: the job cannot continue elsewhere (RL), so it is not paused, only cancelled."""


def run_job(job: WorkerJob, report: Report, processes: int = 0) -> dict[str, Any]:
    """Run one job, continuing from ``job.resume`` (partial result of an earlier attempt)."""
    resume = job.resume or {}
    with tempfile.TemporaryDirectory(prefix="raceforge-job-") as tmp:
        controller = Path(tmp) / Path(job.controller_name).name
        controller.write_text(job.controller_source, encoding="utf-8")
        params = None
        if job.params_yaml:
            params = controller.with_suffix(".yaml")
            params.write_text(job.params_yaml, encoding="utf-8")
        bench = _bench_config(job.request, processes)
        if job.kind == "benchmark":
            done = [RunResult(**r) for r in resume.get("runs", [])]

            def on_run(r: RunResult) -> None:
                done.append(r)
                state = f"finished in {r.time_s:.1f} s" if r.finished else f"DNF ({r.fraction:.0%})"
                report(
                    {"done": len(done), "total": bench.tracks},
                    [f"corridor {r.seed}: {state}"],
                    {"runs": [asdict(x) for x in done]},
                )

            res = benchmark(controller, params, bench, on_run, done=list(done))
            return {
                "score": res.score,
                "finished_rate": res.finished_rate,
                "runs": [asdict(r) for r in res.runs],
            }
        if job.kind == "rl":
            return _run_rl(job, bench, Path(tmp), report)
        trials = int(job.request.get("trials", 30))
        seen: dict[int, Trial] = {
            int(t["number"]): Trial(int(t["number"]), dict(t["params"]), float(t["score"]))
            for t in resume.get("trials", [])
        }

        def on_trial(t: Trial, best: Trial) -> None:
            seen[t.number] = t
            line = f"trial {t.number}: {t.score:.1f} (best {best.score:.1f})"
            report(
                {"done": max(0, t.number + 1), "total": trials, "best": best.score},
                [line],
                {"trials": [asdict(x) for x in seen.values()]},
            )

        out = Path(tmp) / f"{controller.stem}.tuned.yaml"
        cfg = TuneConfig(
            trials=trials,
            timeout_s=job.request.get("timeout_s"),
            train_tracks=int(job.request.get("train_tracks", 3)),
            bench=bench,
            out=out,
        )
        res = tune(controller, cfg, on_trial, previous=list(seen.values()))
        return {
            "score": res.validation.score,
            "default_score": res.default_validation.score,
            "finished_rate": res.validation.finished_rate,
            "best_params": res.best_params,
            "params_yaml": out.read_text(encoding="utf-8"),
            "trials": [{"number": t.number, "score": t.score} for t in res.trials],
        }


def _run_rl(job: WorkerJob, bench: BenchConfig, tmp: Path, report: Report) -> dict[str, Any]:
    from raceforge.api.train import EnvConfig, RLConfig, RLProgress, train_ppo

    steps = int(job.request.get("steps", 200_000))

    def on_progress(p: RLProgress) -> None:
        mean = "-" if p.mean_reward is None else f"{p.mean_reward:.1f}"
        progress = {"done": p.steps, "total": steps, "mean_reward": p.mean_reward}
        report(progress, [f"{p.steps}/{steps} steps, mean reward {mean}"], None)

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


class _Heartbeat:
    """Keeps the lease while a job runs (a single race can take longer than the lease)."""

    def __init__(self, client: BackendClient, info: Callable[[], dict[str, Any]]) -> None:
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(client, info), daemon=True)

    def _run(self, client: BackendClient, info: Callable[[], dict[str, Any]]) -> None:
        while not self._stop.wait(HEARTBEAT_S):
            # a failed beat is retried by the next progress report or heartbeat
            with contextlib.suppress(BackendError, OfflineError):
                client.worker_heartbeat(info())

    def __enter__(self) -> "_Heartbeat":
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()


def _finish(
    client: BackendClient,
    job_id: str,
    status: str,
    out: Callable[[str], None],
    result: dict[str, Any] | None = None,
    error: str = "",
) -> None:
    deadline = time.monotonic() + FINISH_RETRY_S
    while True:
        try:
            client.job_finish(job_id, status, result, error)
            return
        except BackendError as e:
            if e.status == 404:
                out(f"job {job_id}: the backend gave it to another worker; result dropped")
                return
            raise
        except OfflineError:
            if time.monotonic() > deadline:
                out(f"job {job_id}: backend unreachable; the job will be requeued")
                return
            time.sleep(POLL_S)


def run_worker(
    client: BackendClient,
    idle_only: bool = False,
    once: bool = False,
    stop: threading.Event | None = None,
    out: Callable[[str], None] = print,
    policy: WorkerPolicy | Callable[[], WorkerPolicy] | None = None,
    probe: Probe | None = None,
    now: Callable[[], datetime] = datetime.now,
) -> int:
    """Poll for jobs until ``stop`` is set (or after one job with ``once``); returns jobs run.

    ``policy`` (a value or a function, so a running worker sees changes) decides when jobs are
    taken; without one the worker always runs (``idle_only``: the ``idle`` policy). Setting
    ``stop`` during a job pauses it after the current race/trial.
    """
    stop = stop or threading.Event()
    if policy is None:
        policy = WorkerPolicy(mode="idle" if idle_only else "always")
    current = policy if callable(policy) else (lambda p=policy: p)
    sys_probe = probe or SystemProbe()
    base = machine_info()
    state = {"avail": Availability(True)}

    def check() -> Availability:
        p = current()
        state["avail"] = decide(p, now(), sys_probe)
        return state["avail"]

    def info() -> dict[str, Any]:
        a = state["avail"]
        return {**base, "mode": current().mode, "available": a.available, "reason": a.reason}

    last_beat, count, shown = 0.0, 0, ""
    while not stop.is_set():
        avail = check()
        try:
            if time.monotonic() - last_beat > HEARTBEAT_S or avail.reason != shown:
                client.worker_heartbeat(info())
                last_beat = time.monotonic()
            job = client.worker_claim() if avail.available else None
        except OfflineError as e:
            out(f"backend not reachable ({e}); retrying")
            stop.wait(POLL_S * 4)
            continue
        if avail.reason != shown:
            out(f"waiting: {avail.reason}" if avail.reason else "ready for jobs")
            shown = avail.reason
        if job is None:
            if once:
                return count
            stop.wait(POLL_S)
            continue
        resumed = f" (continuing, attempt {job.attempt})" if job.resume else ""
        out(f"job {job.id}: {job.kind} {job.controller_name}{resumed}")
        pending: list[str] = []
        last_report = 0.0

        def report(
            progress: dict[str, Any],
            log: list[str],
            partial: dict[str, Any] | None,
            job_id: str = job.id,
            buf: list[str] = pending,
        ) -> None:
            nonlocal last_report
            buf.extend(log)
            for line in log:
                out(f"  {line}")
            finished = progress.get("done") == progress.get("total")
            pausable = partial is not None and not finished
            pause = pausable and (stop.is_set() or not check().available)
            if pause or finished or time.monotonic() - last_report >= 1.0:
                try:
                    cancelled = client.job_progress(job_id, progress, buf[:], partial)
                except OfflineError:
                    cancelled = False  # keep racing; the result is delivered later
                except BackendError as e:
                    if e.status == 404:
                        raise JobLostError from e
                    raise
                buf.clear()
                last_report = time.monotonic()
                if cancelled:
                    raise JobCancelledError
            if pause:
                raise JobPausedError(partial)

        with _Heartbeat(client, info):
            try:
                result = run_job(job, report, current().processes)
                _finish(client, job.id, "done", out, result)
                out(f"job {job.id}: done")
                count += 1
            except JobCancelledError:
                _finish(client, job.id, "cancelled", out)
                out(f"job {job.id}: cancelled")
                count += 1
            except JobPausedError as e:
                partial: dict[str, Any] = e.args[0]
                _finish(client, job.id, "paused", out, partial)
                out(f"job {job.id}: paused ({state['avail'].reason or 'stopping'}); back in queue")
            except JobLostError:
                out(f"job {job.id}: the backend gave it to another worker")
            except (BackendError, OfflineError):
                raise
            except Exception as e:  # the team's controller may raise anything; report, keep serving
                _finish(client, job.id, "error", out, error=f"{type(e).__name__}: {e}")
                out(f"job {job.id}: error {type(e).__name__}: {e}")
                count += 1
        if once:
            return count
    return count
