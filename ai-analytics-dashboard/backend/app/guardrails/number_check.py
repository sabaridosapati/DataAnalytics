"""Verify that every number in a generated answer appears in the tool outputs."""
import math
import re
from dataclasses import dataclass, field

NUM_RE = re.compile(
    r"(?<![A-Za-z0-9_.])(?P<sign>[-+])?\$?(?P<int>\d{1,3}(?:,\d{3})+|\d+)(?P<frac>\.\d+)?"
    r"(?P<suffix>\s*(?:%|percent\b|thousand\b|million\b|billion\b|[kKmMbB]\b))?"
)
MULTIPLIERS = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "billion": 1e9}


@dataclass
class NumberCheckResult:
    ok: bool
    unverified: list[str] = field(default_factory=list)


def _parse(match: re.Match) -> tuple[float, float, bool, bool]:
    """Return (value, tolerance, is_percent, is_plain_integer)."""
    frac = match.group("frac") or ""
    value = float(match.group("int").replace(",", "") + frac)
    if match.group("sign") == "-":
        value = -value
    decimals = len(frac) - 1 if frac else 0
    suffix = (match.group("suffix") or "").strip().lower()
    is_pct = suffix in ("%", "percent")
    mult = MULTIPLIERS.get(suffix, 1.0)
    tolerance = max(0.01, 0.5 * 10 ** (-decimals) * mult)
    plain_int = decimals == 0 and mult == 1.0 and not is_pct
    return value * mult, tolerance, is_pct, plain_int


def _collect(obj, out: list[float]) -> None:
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, (int, float)):
        if math.isfinite(obj):
            out.append(float(obj))
        return
    if isinstance(obj, str):
        try:
            value = float(obj.replace(",", "").strip())
            if math.isfinite(value):
                out.append(value)
            return
        except ValueError:
            pass
        for m in NUM_RE.finditer(obj):
            out.append(_parse(m)[0])
        return
    if isinstance(obj, dict):
        for v in obj.values():
            _collect(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _collect(v, out)


def check_numbers(answer: str, sources, question: str = "") -> NumberCheckResult:
    allowed: list[float] = []
    _collect(sources, allowed)
    _collect(question, allowed)
    allowed_abs = [abs(v) for v in allowed]
    unverified: list[str] = []
    for m in NUM_RE.finditer(answer):
        value, tol, is_pct, plain_int = _parse(m)
        if plain_int and (abs(value) <= 31 or 1900 <= value <= 2100):
            continue  # counts, ranks, days, years
        target = abs(value)
        if any(abs(target - v) <= tol or (is_pct and abs(target - v * 100) <= tol) for v in allowed_abs):
            continue
        unverified.append(m.group(0).strip())
    return NumberCheckResult(ok=not unverified, unverified=unverified)
