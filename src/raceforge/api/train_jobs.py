"""Training jobs in the local engine (spec 0013 Train tab): one at a time, in a background thread.

The heavy part (the races) runs in worker processes of :mod:`raceforge.train.benchmark`; this thread
only coordinates and keeps the progress the UI polls. Cancel takes effect after the current
race/trial.
"""

import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from raceforge.api.models import (
    TrainBenchRequest,
    TrainJob,
    TrainRace,
    TrainRun,
    TrainTrial,
    TrainTuneRequest,
)
from raceforge.api.train import BenchConfig, RunResult, Trial, TuneConfig, benchmark, tune
from raceforge.control.controller import load_controller_class

MAX_JOBS = 20


class CancelledError(Exception):
    pass


def _bench(race: TrainRace) -> BenchConfig:
    quick = None
    if race.quick_track:
        from raceforge.api.tracks import QuickTracks

        try:
            quick = QuickTracks().get(race.quick_track)
        except KeyError as e:
            raise ValueError(f"no quick track {race.quick_track!r}") from e
        if quick.loop:
            quick = quick.model_copy(update={"laps": race.laps})
    return BenchConfig(
        quick=quick,
        tracks=race.tracks,
        length_m=race.length_m,
        laps=race.laps,
        opponents=race.opponents,
        max_time_s=race.max_time_s,
    )


def _run(r: RunResult) -> TrainRun:
    return TrainRun(
        seed=r.seed,
        finished=r.finished,
        time_s=r.time_s,
        fraction=r.fraction,
        wall_contacts=r.wall_contacts,
        error=r.error,
    )


class TrainJobs:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, TrainJob] = {}
        self._cancel: set[str] = set()

    # ------------------------------------------------------------------ queries
    def list(self) -> list[TrainJob]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.started_at, reverse=True)

    def get(self, job_id: str) -> TrainJob:
        with self._lock:
            return self._jobs[job_id]

    def cancel(self, job_id: str) -> TrainJob:
        with self._lock:
            job = self._jobs[job_id]
            if job.state == "running":
                self._cancel.add(job_id)
            return job

    # ------------------------------------------------------------------ start
    def _start(self, kind: str, controller: str, total: int) -> TrainJob:
        with self._lock:
            if any(j.state == "running" for j in self._jobs.values()):
                raise RuntimeError("a training job is already running; cancel it or wait")
            job = TrainJob(
                id=uuid.uuid4().hex[:12],
                kind=kind,  # pyright: ignore[reportArgumentType]
                controller=controller,
                state="running",
                started_at=time.time(),
                total=total,
            )
            self._jobs[job.id] = job
            for old in sorted(self._jobs.values(), key=lambda j: j.started_at)[:-MAX_JOBS]:
                del self._jobs[old.id]
            return job

    def _update(self, job_id: str, **changes: object) -> None:
        with self._lock:
            self._jobs[job_id] = self._jobs[job_id].model_copy(update=changes)
            if job_id in self._cancel and changes.get("state") is None:
                raise CancelledError

    def _finish(self, job_id: str, state: str, **changes: object) -> None:
        with self._lock:
            self._cancel.discard(job_id)
            self._jobs[job_id] = self._jobs[job_id].model_copy(
                update={"state": state, "finished_at": time.time(), **changes}
            )

    def _thread(self, job_id: str, work: Callable[[], None]) -> None:
        def run() -> None:
            try:
                work()
            except CancelledError:
                self._finish(job_id, "cancelled")
            except Exception as e:  # shown in the UI; the engine keeps running
                self._finish(job_id, "error", error=f"{type(e).__name__}: {e}")

        threading.Thread(target=run, name=f"train-{job_id}", daemon=True).start()

    def start_benchmark(self, req: TrainBenchRequest) -> TrainJob:
        controller = Path(req.controller).expanduser()
        load_controller_class(controller)  # a broken file fails the request, not the job
        bench = _bench(req.race)  # a missing quick track too
        job = self._start("benchmark", str(controller), req.race.tracks)
        runs: list[TrainRun] = []

        def progress(r: RunResult) -> None:
            runs.append(_run(r))
            self._update(job.id, runs=list(runs))

        def work() -> None:
            params = Path(req.params).expanduser() if req.params else None
            res = benchmark(controller, params, bench, progress)
            self._finish(job.id, "done", score=res.score, finished_rate=res.finished_rate)

        self._thread(job.id, work)
        return job

    def start_tune(self, req: TrainTuneRequest) -> TrainJob:
        controller = Path(req.controller).expanduser()
        if not load_controller_class(controller).Params.tunables():
            raise ValueError(f"{controller.name}: no Tunable parameters to tune")
        out = (
            Path(req.out).expanduser()
            if req.out
            else controller.with_name(f"{controller.stem}.tuned.yaml")
        )
        bench = _bench(req.race)
        job = self._start("tune", str(controller), req.trials)
        trials: list[TrainTrial] = []

        def progress(t: Trial, best: Trial) -> None:
            trials.append(
                TrainTrial(number=t.number, score=t.score, best=best.score, params=t.params)
            )
            self._update(job.id, trials=list(trials))

        def work() -> None:
            cfg = TuneConfig(
                trials=req.trials,
                timeout_s=req.timeout_s,
                train_tracks=req.train_tracks,
                bench=bench,
                out=out,
            )
            res = tune(controller, cfg, progress)
            self._finish(
                job.id,
                "done",
                score=res.validation.score,
                default_score=res.default_validation.score,
                finished_rate=res.validation.finished_rate,
                out=str(out),
                best_params=res.best_params,
                runs=[_run(r) for r in res.validation.runs],
            )

        self._thread(job.id, work)
        return job
