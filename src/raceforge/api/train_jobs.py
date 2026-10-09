"""Training jobs in the local engine (spec 0013 Train tab): one at a time, in a background thread.

The heavy part (the races) runs in worker processes of :mod:`raceforge.train.benchmark`; this thread
only coordinates and keeps the progress the UI polls. Cancel takes effect after the current
race/trial.
"""

import builtins
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from raceforge.api.models import (
    RecordingInfo,
    TrainBCRequest,
    TrainBenchRequest,
    TrainJob,
    TrainRace,
    TrainRLRequest,
    TrainRun,
    TrainTrial,
    TrainTuneRequest,
)
from raceforge.api.train import BenchConfig, RunResult, Trial, TuneConfig, benchmark, tune
from raceforge.control.controller import load_controller_class

MAX_JOBS = 20


def runs_dir() -> Path:
    """Recorded runs (MCAP) of the Simulate tab, e.g. teleop demonstrations (spec 0023)."""
    from raceforge.api.workspace import default_root

    return default_root().parent / "runs"


def policies_dir() -> Path:
    """Where the engine writes trained policies (spec 0022), next to the workspace cache."""
    from raceforge.api.workspace import default_root

    return default_root().parent / "policies"


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

    def start_rl(self, req: TrainRLRequest) -> TrainJob:
        """PPO training (spec 0022) in this engine; needs the optional extra ``rl``."""
        try:
            import stable_baselines3  # noqa: F401  # pyright: ignore[reportMissingImports, reportUnusedImport]
        except ImportError as e:
            raise ValueError("RL needs the optional extra: uv sync --extra rl") from e
        from raceforge.api.train import ONNX_TEMPLATE, EnvConfig, RLConfig, RLProgress, train_ppo

        bench = _bench(req.race)
        out = (
            Path(req.out).expanduser()
            if req.out
            else policies_dir() / f"ppo-{int(time.time())}.yaml"
        )
        job = self._start("rl", str(ONNX_TEMPLATE), req.steps)

        def progress(p: RLProgress) -> None:
            self._update(job.id, steps_done=p.steps, mean_reward=p.mean_reward)

        def work() -> None:
            env = EnvConfig(
                length_m=req.race.length_m,
                laps=req.race.laps,
                opponents=req.race.opponents,
                max_time_s=min(req.race.max_time_s, 120.0),
                quick=bench.quick,
            )
            res = train_ppo(
                RLConfig(
                    steps=req.steps, train_tracks=req.train_tracks, env=env, bench=bench, out=out
                ),
                progress,
            )
            v = res.validation
            self._finish(
                job.id,
                "done",
                steps_done=res.steps,
                out=str(out),
                score=v.score if v else None,
                finished_rate=v.finished_rate if v else None,
                runs=[_run(r) for r in v.runs] if v else [],
            )

        self._thread(job.id, work)
        return job

    def recordings(self) -> "builtins.list[RecordingInfo]":
        from raceforge.api.train import summarise

        out: list[RecordingInfo] = []
        folder = runs_dir()
        for p in sorted(folder.glob("*.mcap"), key=lambda q: q.stat().st_mtime, reverse=True):
            try:
                r = summarise(p)
                out.append(
                    RecordingInfo(
                        path=str(p),
                        name=p.name,
                        frames=r.frames,
                        demo_frames=r.demo_frames,
                        duration_s=round(r.duration_s, 2),
                        modified=p.stat().st_mtime,
                    )
                )
            except Exception as e:  # a damaged or half-written file must not hide the others
                out.append(
                    RecordingInfo(
                        path=str(p),
                        name=p.name,
                        frames=0,
                        demo_frames=0,
                        duration_s=0,
                        modified=p.stat().st_mtime,
                        error=f"{type(e).__name__}: {e}"[:200],
                    )
                )
        return out

    def start_bc(self, req: TrainBCRequest) -> TrainJob:
        """Behaviour cloning from recorded drives (spec 0023); needs torch (extra ``rl``)."""
        try:
            import torch  # noqa: F401  # pyright: ignore[reportMissingImports, reportUnusedImport]
        except ImportError as e:
            raise ValueError(
                "imitation learning needs the optional extra: uv sync --extra rl"
            ) from e
        from raceforge.api.train import (
            DEMO_STATES,
            ONNX_TEMPLATE,
            BCConfig,
            BCProgress,
            sim_robot_info,
            summarise,
            train_bc,
        )

        files = [Path(p).expanduser() for p in req.recordings] or sorted(runs_dir().glob("*.mcap"))
        missing = [str(p) for p in files if not p.is_file()]
        if missing or not files:
            raise ValueError(f"recordings not found: {', '.join(missing) or 'none recorded yet'}")
        states: tuple[str, ...] = DEMO_STATES
        if req.all_states:
            found: set[str] = set()
            from raceforge.sim.record import read_frames

            for p in files:
                found |= {f.state for f in read_frames(p)}
            states = tuple(sorted(found))
        if sum(summarise(p, states).demo_frames for p in files) < 20:
            raise ValueError(
                "fewer than 20 demonstration frames: drive with teleop and record the run"
            )
        bench = _bench(req.race)
        out = (
            Path(req.out).expanduser()
            if req.out
            else policies_dir() / f"bc-{int(time.time())}.yaml"
        )
        job = self._start("bc", str(ONNX_TEMPLATE), req.epochs)

        def progress(p: BCProgress) -> None:
            self._update(job.id, steps_done=p.epoch, val_loss=p.val_loss)

        def work() -> None:
            res = train_bc(
                files,
                sim_robot_info(),
                BCConfig(epochs=req.epochs, states=states, bench=bench, out=out),
                progress,
            )
            v = res.validation
            self._finish(
                job.id,
                "done",
                steps_done=req.epochs,
                val_loss=res.val_loss,
                out=str(out),
                score=v.score if v else None,
                finished_rate=v.finished_rate if v else None,
                runs=[_run(r) for r in v.runs] if v else [],
            )

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
