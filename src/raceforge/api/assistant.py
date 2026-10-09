"""What AI assistants can do with RaceForge (spec 0028), as plain functions over ``raceforge.api``.

The MCP server (``raceforge.mcp``) only registers these as tools; everything here is JSON-friendly
and testable without MCP. Assemblies are edited as named drafts on disk (assembly + quick-start
params), so an assistant never has to send a whole assembly back and forth.
"""

import contextlib
import itertools
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from raceforge.api.models import (
    AssemblyEditRequest,
    AssemblyEditResponse,
    EditOp,
    QuickStartParams,
    SaveAssembly,
    TeamJobRequest,
    TrainBenchRequest,
    TrainRace,
    TrainRLRequest,
    TrainTuneRequest,
)
from raceforge.api.runs import compare, summarize_file
from raceforge.api.service import Engine
from raceforge.api.tracks import QuickTracks, validation
from raceforge.api.train import BenchConfig, benchmark
from raceforge.api.train_jobs import TrainJobs
from raceforge.api.workspace import WorkspaceApi, default_root
from raceforge.backend.models import FileSetContent
from raceforge.track.procedural import CorridorParams

DRAFT = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class AssistantError(ValueError):
    """A readable error for the assistant (bad name, unknown part, …)."""


def default_dir() -> Path:
    return default_root().parent / "mcp"


def _path(p: str) -> list[str]:
    parts = [x for x in p.strip("/").split("/") if x]
    if not parts:
        raise AssistantError("path: give a part path like 'chassis/beam-3' (see get_assembly)")
    return parts


class Assistant:
    def __init__(
        self,
        engine: Engine | None = None,
        workspace: WorkspaceApi | None = None,
        folder: Path | None = None,
        quick: QuickTracks | None = None,
        jobs: TrainJobs | None = None,
    ) -> None:
        self.engine = engine or Engine()
        self._ws = workspace
        self.folder = folder or default_dir()
        self.quick = quick or QuickTracks()
        self.jobs = jobs or TrainJobs()

    @property
    def ws(self) -> WorkspaceApi:
        if self._ws is None:
            self._ws = WorkspaceApi(self.engine.cat)
        return self._ws

    # ------------------------------------------------------------------ parts
    def search_parts(
        self, query: str = "", category: str = "", limit: int = 20
    ) -> list[dict[str, Any]]:
        rows = self.engine.parts(query, category)[: max(1, min(limit, 100))]
        return [
            {
                "key": p.key,
                "name": p.name,
                "category": p.category,
                "mass_g": p.mass_g,
                "lego": p.mesh_url is None and p.ldraw_id is not None,
                "verified": p.verified,
            }
            for p in rows
        ]

    def get_part(self, key: str) -> dict[str, Any]:
        found = [p for p in self.engine.parts(key) if p.key == key]
        if not found:
            raise AssistantError(f"no part {key!r} (use search_parts)")
        conns = [c.model_dump(mode="json") for c in self.engine.part_connectors(key)]
        return {**found[0].model_dump(mode="json", exclude={"mesh_url"}), "connector_list": conns}

    def quickstart_options(self) -> dict[str, Any]:
        s = self.engine.quickstart_schema()
        return {"defaults": s.defaults, "options": s.options}

    # ------------------------------------------------------------------ drafts
    def _draft_file(self, name: str) -> Path:
        if not DRAFT.match(name):
            raise AssistantError("draft names: lower-case letters, digits and '-', e.g. 'car-a'")
        return self.folder / "drafts" / f"{name}.json"

    def _save_draft(self, name: str, assembly: dict[str, Any], qs: dict[str, Any]) -> None:
        f = self._draft_file(name)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"assembly": assembly, "quickstart": qs}), encoding="utf-8")

    def _workspace_assembly(self, slug: str) -> dict[str, Any] | None:
        try:
            objs = self.ws.ws.objects("assembly")
        except Exception:  # not logged in: only drafts
            return None
        obj = next((o for o in objs if o.slug == slug), None)
        if obj is None or obj.latest is None:
            return None
        return self.ws.ws.version_content(obj.latest.id)

    def _load(self, name: str) -> tuple[dict[str, Any], QuickStartParams, bool]:
        """Assembly, quick-start params, and whether it is a draft (else a workspace object)."""
        f = self._draft_file(name)
        if f.is_file():
            data = json.loads(f.read_text(encoding="utf-8"))
            return data["assembly"], QuickStartParams.model_validate(data["quickstart"]), True
        assembly = self._workspace_assembly(name)
        if assembly is None:
            raise AssistantError(f"no draft or workspace assembly {name!r} (see list_assemblies)")
        return assembly, QuickStartParams(), False

    def _edit(self, name: str, op: EditOp) -> AssemblyEditResponse:
        assembly, qs, _ = self._load(name)
        try:
            res = self.engine.edit_assembly(
                AssemblyEditRequest(assembly=assembly, quickstart=qs, op=op)
            )
        except (ValueError, KeyError) as e:
            raise AssistantError(str(e)) from e
        if op.kind != "none":
            self._save_draft(name, res.assembly, qs.model_dump(mode="json"))
        return res

    @staticmethod
    def _summary(res: AssemblyEditResponse, full: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "rules": [r.model_dump(mode="json", exclude={"paths"}) for r in res.rules],
            "overlaps": len(res.overlaps),
            "budget": None
            if res.budget is None
            else {
                "total_eur": res.budget.total_eur,
                "limit_eur": res.budget.limit_eur,
                "missing_prices": res.budget.missing,
            },
            "problems": res.problems,
            "warnings": [w.message for w in res.warnings],
        }
        if full:
            out = {
                "part_count": len(res.parts),
                "parts": [
                    {"path": "/".join(p.path), "key": p.key, "name": p.name, "category": p.category}
                    for p in res.parts
                ],
                "derived": res.derived,
                **out,
            }
        if res.selected:
            out["selected"] = "/".join(res.selected)
        return out

    def apply_quickstart(self, draft: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            qs = QuickStartParams.model_validate(params or {})
        except ValidationError as e:
            raise AssistantError(str(e)) from e
        res = self.engine.quickstart(qs)
        self._save_draft(draft, res.assembly, qs.model_dump(mode="json"))
        return self.get_assembly(draft)

    def list_assemblies(self) -> dict[str, Any]:
        drafts = sorted(p.stem for p in (self.folder / "drafts").glob("*.json"))
        try:
            objs = [
                {"slug": o.slug, "version": (o.latest.semver or o.latest.id) if o.latest else None}
                for o in self.ws.ws.objects("assembly")
            ]
        except Exception:  # not logged in: drafts only
            objs = []
        return {"drafts": drafts, "workspace": objs}

    def get_assembly(self, name: str) -> dict[str, Any]:
        return self._summary(self._edit(name, EditOp()))

    def validate_assembly(self, name: str) -> dict[str, Any]:
        return self._summary(self._edit(name, EditOp()), full=False)

    def add_part(
        self,
        draft: str,
        key: str,
        attach_to: str | None = None,
        candidate: int = 0,
        position: tuple[float, float, float] = (0.0, 0.0, 0.05),
    ) -> dict[str, Any]:
        if attach_to:
            op = EditOp(kind="attach", key=key, path=_path(attach_to), candidate=candidate)
        else:
            op = EditOp(kind="add", key=key, position=position)
        return self._summary(self._edit(draft, op))

    def move_part(self, draft: str, path: str, delta: tuple[float, float, float]) -> dict[str, Any]:
        return self._summary(self._edit(draft, EditOp(kind="move", path=_path(path), delta=delta)))

    def rotate_part(
        self, draft: str, path: str, axis: Literal["x", "y", "z"] = "z", turns: int = 1
    ) -> dict[str, Any]:
        op = EditOp(kind="rotate", path=_path(path), axis=axis, turns=turns)
        return self._summary(self._edit(draft, op))

    def remove_part(self, draft: str, path: str) -> dict[str, Any]:
        return self._summary(self._edit(draft, EditOp(kind="delete", path=_path(path))))

    def get_bom(self, name: str) -> str:
        assembly, qs, _ = self._load(name)
        return self.engine.export_assembly(assembly, qs, "bom")[1]

    def save_assembly(self, draft: str, slug: str, message: str = "") -> dict[str, Any]:
        assembly, _, _ = self._load(draft)
        v = self.ws.save_assembly(
            SaveAssembly(slug=slug, assembly=assembly, message=f"AI via MCP: {message}".strip())
        )
        return json.loads(v.model_dump_json())

    # ------------------------------------------------------------------ sim & training
    def list_controllers(self) -> list[dict[str, Any]]:
        return [c.model_dump(mode="json") for c in self.engine.controllers()]

    def _controller(self, name_or_path: str) -> str:
        for c in self.engine.controllers():
            if name_or_path in (c.name, c.path):
                return c.path
        p = Path(name_or_path).expanduser()
        if not p.is_file():
            raise AssistantError(f"no controller {name_or_path!r} (see list_controllers)")
        return str(p)

    def run_simulation(
        self,
        controller: str,
        params: str | None = None,
        quick_track: str | None = None,
        seed: int = 1000,
        length_m: float = 25.0,
        laps: int = 1,
        opponents: int = 0,
        max_time_s: float = 240.0,
    ) -> dict[str, Any]:
        quick = None
        if quick_track:
            try:
                quick = self.quick.get(quick_track)
            except KeyError as e:
                raise AssistantError(f"no quick track {quick_track!r} (see list_tracks)") from e
        cfg = BenchConfig(
            tracks=1,
            seed0=seed,
            length_m=length_m,
            laps=laps,
            opponents=opponents,
            max_time_s=max_time_s,
            workers=1,
            quick=quick,
        )
        res = benchmark(self._controller(controller), params, cfg)
        return {**asdict(res.runs[0]), "score": res.score}

    def start_training(
        self,
        kind: Literal["benchmark", "tune", "rl"],
        controller: str | None = None,
        run_on: str = "local",
        params: str | None = None,
        trials: int = 30,
        train_tracks: int = 3,
        steps: int = 200_000,
        tracks: int = 5,
        length_m: float = 25.0,
        laps: int = 1,
        opponents: int = 0,
        quick_track: str | None = None,
    ) -> dict[str, Any]:
        race = TrainRace(
            tracks=tracks,
            length_m=length_m,
            laps=laps,
            opponents=opponents,
            quick_track=quick_track,
        )
        if kind != "rl" and not controller:
            raise AssistantError(f"{kind} needs a controller (see list_controllers)")
        ctrl = self._controller(controller) if controller else ""
        bench = (
            TrainBenchRequest(controller=ctrl, params=params, race=race)
            if kind == "benchmark"
            else None
        )
        tune = (
            TrainTuneRequest(controller=ctrl, trials=trials, train_tracks=train_tracks, race=race)
            if kind == "tune"
            else None
        )
        rl = (
            TrainRLRequest(steps=steps, train_tracks=train_tracks, race=race)
            if kind == "rl"
            else None
        )
        if run_on == "local":
            if bench:
                job = self.jobs.start_benchmark(bench)
            elif tune:
                job = self.jobs.start_tune(tune)
            else:
                assert rl is not None
                job = self.jobs.start_rl(rl)
            return {"job_id": job.id, "where": "local", "state": job.state}
        target = None if run_on == "team" else run_on
        info = self.ws.submit_job(
            TeamJobRequest(bench=bench, tune=tune, rl=rl, target_worker_id=target)
        )
        return {"job_id": info.id, "where": "team", "state": info.status}

    def job_status(self, job_id: str) -> dict[str, Any]:
        try:
            return {"where": "local", **self.jobs.get(job_id).model_dump(mode="json")}
        except KeyError:
            pass
        try:
            info = self.ws.ws.client().job(job_id)
        except Exception as e:
            raise AssistantError(f"no job {job_id!r}") from e
        return {"where": "team", **json.loads(info.model_dump_json(exclude={"log_tail"}))}

    def list_jobs(self) -> list[dict[str, Any]]:
        out = [
            {"job_id": j.id, "where": "local", "kind": j.kind, "state": j.state, "score": j.score}
            for j in self.jobs.list()
        ]
        # not logged in or offline: local jobs only
        with contextlib.suppress(Exception):
            out += [
                {
                    "job_id": j.id,
                    "where": "team",
                    "kind": j.kind,
                    "state": j.status,
                    "worker": j.worker_name,
                    "score": (j.result or {}).get("score"),
                }
                for j in self.ws.jobs()
            ]
        return out

    def stop_job(self, job_id: str) -> dict[str, Any]:
        try:
            return {"where": "local", "state": self.jobs.cancel(job_id).state}
        except KeyError:
            pass
        try:
            return {"where": "team", "state": self.ws.cancel_job(job_id).status}
        except Exception as e:
            raise AssistantError(f"no job {job_id!r}") from e

    # ------------------------------------------------------------------ tracks
    def list_tracks(self) -> list[dict[str, Any]]:
        return [t.model_dump(mode="json") for t in self.quick.list()]

    def get_track(self, name: str) -> dict[str, Any]:
        try:
            q = self.quick.get(name)
        except KeyError as e:
            raise AssistantError(f"no quick track {name!r} (see list_tracks)") from e
        return {
            **q.model_dump(mode="json", exclude={"edit"}),
            "has_edit_layer": q.edit is not None,
            "validation": validation(q).model_dump(mode="json"),
        }

    def validate_track(self, name: str) -> dict[str, Any]:
        return self.get_track(name)["validation"]

    def generate_corridor(self, seed: int = 0, length_m: float = 25.0) -> dict[str, Any]:
        try:
            res = self.engine.corridor(CorridorParams(seed=seed, length_m=length_m))
        except ValidationError as e:
            raise AssistantError(str(e)) from e
        pts = res.centreline
        length = sum(
            ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
            for (x0, y0), (x1, y1) in itertools.pairwise(pts)
        )
        surfaces: dict[str, int] = {}
        for p in res.primitives:
            surfaces[p.surface or p.kind] = surfaces.get(p.surface or p.kind, 0) + 1
        return {"seed": seed, "centreline_m": round(length, 2), "primitives": surfaces}

    # ------------------------------------------------------------------ run logs
    def list_runs(self) -> list[dict[str, Any]]:
        try:
            objs = self.ws.ws.objects("run")
        except Exception as e:
            raise AssistantError("log in on the Team tab to see run logs") from e
        return [
            {"run": o.slug, "message": o.latest.message if o.latest else ""}
            for o in objs
            if o.latest is not None
        ]

    def get_run_summary(self, run: str) -> dict[str, Any]:
        obj = next((o for o in self.ws.ws.objects("run") if o.slug == run), None)
        if obj is None or obj.latest is None:
            raise AssistantError(f"no run {run!r} (see list_runs)")
        files = FileSetContent.model_validate(self.ws.ws.version_content(obj.latest.id)).files
        by_name = {f.path: f for f in files}
        log = by_name.get("telemetry.jsonl.gz")
        if log is None:
            raise AssistantError(f"run {run!r} has no telemetry.jsonl.gz")
        notes: list[dict[str, Any]] = []
        if "notes.json" in by_name:
            notes = json.loads(self.ws.ws.blob(by_name["notes.json"].sha256).read_text("utf-8"))
        return {"run": run, **summarize_file(self.ws.ws.blob(log.sha256), notes)}

    def compare_runs(self, a: str, b: str) -> dict[str, Any]:
        return compare(self.get_run_summary(a), self.get_run_summary(b))
