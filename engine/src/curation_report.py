"""CSV and HTML reports of a curation run, with every piece of model text escaped where it is written.

The report lists the photos of the largest tier (or the final set, if larger) in selection order, with their tier,
rank, how the rank was reached and the finalists' critiques. It holds file names, never paths, and no image. The HTML
has no script and no external resource, and the CSV neutralizes cells a spreadsheet would run as a formula.
"""

import csv
import html
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .curation import CurationResult
from .selection import SelectionSettings

CSV_NAME = "curation.csv"
HTML_NAME = "curation.html"
# A cell starting with one of these is read as a formula by spreadsheets.
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")
CSV_COLUMNS = (
    "position", "tier", "final_set", "rank", "file_name", "export_name", "rank_source", "strength", "uncertainty",
    "prescore", "group", "content_tag", "strengths", "weaknesses", "caption", "alternates",
)  # fmt: skip
_LIST_SEPARATOR = " | "


@dataclass(frozen=True)
class ReportRow:
    """One listed photo. Text fields are raw: `file_name` comes from the disk, the critique texts from a model."""

    position: int
    tier: int | None
    in_final_set: bool
    rank: int
    file_name: str
    export_name: str
    rank_source: str
    strength: float | None
    uncertainty: float | None
    prescore: float | None
    group: int | None
    content_tag: str
    strengths: tuple[str, ...]
    weaknesses: tuple[str, ...]
    caption: str
    alternates: int


# Rounds a number for display; None stays empty.
def _number(value: float | None) -> str:
    return "" if value is None else f"{value:.4f}"


# Returns the text a spreadsheet cannot run: a leading apostrophe is put in front of anything that would be read as
# a formula, and line breaks inside the cell become spaces so a row stays one line.
def csv_safe(value: str) -> str:
    text = " ".join(value.splitlines())
    return "'" + text if text.startswith(_FORMULA_STARTS) else text


# Escapes text for HTML element content and attribute values.
def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


# Lists the photos of the selection (up to the largest tier or final set) as report rows, in selection order.
# `file_names` maps photo IDs to the original file names and `exported` to the names the copies got.
def build_rows(
    result: CurationResult,
    file_names: Mapping[str, str],
    exported: Mapping[str, str],
    settings: SelectionSettings | None = None,
) -> list[ReportRow]:
    listed = (settings or SelectionSettings()).listed_size
    final = set(result.selection.final)
    rows: list[ReportRow] = []
    for position, photo_id in enumerate(result.selection.sequence[:listed], start=1):
        ranked = result.by_id[photo_id]
        note = result.critique.notes.get(photo_id)
        rows.append(
            ReportRow(
                position, result.selection.tier_of(photo_id), photo_id in final, ranked.rank,
                file_names.get(photo_id, ""), exported.get(photo_id, ""), ranked.source,
                ranked.strength if ranked.source == "pairwise" else None, ranked.uncertainty, ranked.prescore,
                ranked.group_id if ranked.group_id is not None and ranked.group_id >= 0 else None,
                ranked.tag or "",
                note.strengths if note else (), note.weaknesses if note else (), note.caption if note else "",
                len(ranked.alternates),
            )  # fmt: skip
        )
    return rows


# One row as CSV cells: every text cell passes through csv_safe.
def _csv_cells(row: ReportRow) -> list[str]:
    return [
        str(row.position), "" if row.tier is None else str(row.tier), "yes" if row.in_final_set else "no",
        str(row.rank), csv_safe(row.file_name), csv_safe(row.export_name), csv_safe(row.rank_source),
        _number(row.strength), _number(row.uncertainty), _number(row.prescore),
        "" if row.group is None else str(row.group), csv_safe(row.content_tag),
        csv_safe(_LIST_SEPARATOR.join(row.strengths)), csv_safe(_LIST_SEPARATOR.join(row.weaknesses)),
        csv_safe(row.caption), str(row.alternates),
    ]  # fmt: skip


# Writes the rows as a UTF-8 CSV with a header line. Returns the path.
def write_csv(rows: list[ReportRow], dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / CSV_NAME
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\r\n")
        writer.writerow(CSV_COLUMNS)
        for row in rows:
            writer.writerow(_csv_cells(row))
    return path


# Renders a list of phrases as an HTML list, each item escaped.
def _html_list(items: tuple[str, ...]) -> str:
    return "<ul>" + "".join(f"<li>{_e(item)}</li>" for item in items) + "</ul>" if items else ""


# One table row; every value is escaped, including the ones the engine itself produced.
def _html_row(row: ReportRow) -> str:
    cells = [
        row.position, row.tier or "", "yes" if row.in_final_set else "no", row.rank, row.file_name,
        row.export_name, row.rank_source, _number(row.strength), _number(row.uncertainty), _number(row.prescore),
        "" if row.group is None else row.group, row.content_tag,
    ]  # fmt: skip
    text = "".join(f"<td>{_e(cell)}</td>" for cell in cells)
    critique = f"<td>{_html_list(row.strengths)}</td><td>{_html_list(row.weaknesses)}</td><td>{_e(row.caption)}</td>"
    return f"<tr>{text}{critique}<td>{row.alternates}</td></tr>"


# Writes a standalone HTML page: no script, no external resource, and a policy that forbids both. The summary lines
# say how the rank was reached, so a run that fell back to the prescore is never presented as a judged one.
# Returns the path.
def write_html(rows: list[ReportRow], result: CurationResult, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / HTML_NAME
    tournament = result.tournament
    if tournament.skipped:
        method = f"Not judged pairwise; the prescore decided ({tournament.reason})."
    else:
        method = (
            f"Judged pairwise: {tournament.rounds_played} rounds, {tournament.provider_calls} model calls, "
            f"{tournament.cache_hits} cached verdicts."
        )
    critique = (
        f"Critiques written for {len(result.critique.notes)} finalists."
        if not result.critique.skipped
        else f"No critique was written ({result.critique.reason})."
    )
    headers = [
        "Position", "Tier", "Final set", "Rank", "File", "Copy", "Rank source", "Strength", "Uncertainty", "Prescore",
        "Group", "Content tag", "Strengths", "Weaknesses", "Caption", "Alternates",
    ]  # fmt: skip
    head = "".join(f"<th>{_e(name)}</th>" for name in headers)
    body = "".join(_html_row(row) for row in rows)
    page = (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'\">\n"
        "<title>Curation report</title>\n"
        "<style>body{font-family:sans-serif;margin:1.5rem}table{border-collapse:collapse}"
        "td,th{border:1px solid GrayText;padding:.25rem .5rem;text-align:left;vertical-align:top}</style>\n"
        f"</head>\n<body>\n<h1>Curation report</h1>\n<p>{_e(method)}</p>\n<p>{_e(critique)}</p>\n"
        "<p>The critiques are written by the judge model and describe the photograph only; they do not change the "
        "rank.</p>\n"
        f"<table>\n<thead><tr>{head}</tr></thead>\n<tbody>\n{body}\n</tbody>\n</table>\n</body>\n</html>\n"
    )
    path.write_text(page, encoding="utf-8")
    return path


# Writes both reports into `dest_dir` and returns their paths (CSV first).
def write_reports(rows: list[ReportRow], result: CurationResult, dest_dir: Path) -> tuple[Path, Path]:
    return write_csv(rows, dest_dir), write_html(rows, result, dest_dir)
