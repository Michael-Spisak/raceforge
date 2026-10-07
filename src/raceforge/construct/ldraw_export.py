"""Export an assembly as an LDraw multi-part document (.mpd) for LDView / BrickLink Studio."""

import re
from collections import Counter

from raceforge.core.assembly import Assembly, PartInstance
from raceforge.core.frames import Transform, core_to_ldraw_transform, mirror_matrix
from raceforge.parts.catalogue import Catalogue, Category

# LDraw colour codes, coloured by function (plan: "colour by function" default).
COLOURS = {
    Category.BEAM: 72,
    Category.AXLE: 0,
    Category.PIN: 1,
    Category.BUSH: 71,
    Category.AXLE_JOINER: 71,
    Category.GEAR: 71,
    Category.DIFFERENTIAL: 72,
    Category.STEERING_ARM: 1,
    Category.STEERING_LINK: 1,
    Category.CV_JOINT: 72,
    Category.WHEEL_RIM: 71,
    Category.TYRE: 0,
    Category.EV3_BRICK: 15,
    Category.MOTOR: 15,
    Category.SENSOR: 15,
    Category.BOARD: 2,
    Category.BATTERY: 2,
}
PLACEHOLDER = "0 !RACEFORGE PLACEHOLDER"


def _fmt(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _line(t: Transform) -> str:
    rot, pos = core_to_ldraw_transform(t)
    return " ".join(_fmt(v) for v in (*pos, *rot[0], *rot[1], *rot[2]))


def export_mpd(assembly: Assembly, cat: Catalogue, name: str = "raceforge-car") -> str:
    lines: list[str] = []
    order = [assembly.root, *sorted(s for s in assembly.submodels if s != assembly.root)]
    for sid in order:
        sub = assembly.submodels[sid]
        lines += [
            f"0 FILE {sid}.ldr",
            f"0 {name if sid == assembly.root else sub.name}",
            f"0 Name: {sid}.ldr",
            "0 Author: RaceForge quick-start",
            "0 !LDRAW_ORG Unofficial_Model",
            "",
        ]
        for item in sub.items:
            t = Transform.from_pose(item.pose)
            if isinstance(item, PartInstance):
                key = cat.key_for_hash(item.part.content_hash)
                entry = cat.entry(key)
                if entry.ldraw_id is None:
                    lines.append(f"{PLACEHOLDER} {key} {_line(t)}")
                else:
                    lines.append(
                        f"1 {COLOURS.get(entry.category, 16)} {_line(t)} {entry.ldraw_id}.dat"
                    )
            else:
                if item.mirrored is not None:
                    t = t.compose(Transform(mirror_matrix(item.mirrored)))
                lines.append(f"1 16 {_line(t)} {item.submodel}.ldr")
        lines.append("")
    return "\n".join(lines)


_TYPE1 = re.compile(r"^1\s+\S+(?:\s+\S+){12}\s+(\S+)\s*$")


def parse_mpd_parts(text: str) -> Counter[str]:
    """Count part usages (LDraw ids and placeholder keys), expanding sub-files inside the MPD."""
    files: dict[str, list[str]] = {}
    current: str | None = None
    main: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("0 FILE "):
            current = line[7:].strip().lower()
            files[current] = []
            main = main or current
        elif current is not None:
            files[current].append(line)

    def count(name: str, depth: int = 0) -> Counter[str]:
        if depth > 20:
            raise ValueError("MPD sub-file nesting too deep")
        out: Counter[str] = Counter()
        for line in files.get(name, []):
            if line.startswith(PLACEHOLDER):
                out[line.split()[3]] += 1
                continue
            m = _TYPE1.match(line)
            if not m:
                continue
            ref = m.group(1).lower()
            if ref in files:
                out += count(ref, depth + 1)
            else:
                out[ref.removesuffix(".dat")] += 1
        return out

    return count(main) if main else Counter()
