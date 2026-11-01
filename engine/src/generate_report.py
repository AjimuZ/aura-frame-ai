"""Report generation: a CSV data export and a self-contained HTML review report.

Both reports are written under the configured (git-ignored) output/reports locations.
"""

import html
from pathlib import Path
from typing import Any

import pandas as pd

from . import progress_events as events
from .utils import Photo, ensure_dir, resolve_path


# Builds a flat record for a photo matching the documented CSV schema.
def _record(photo: Photo) -> dict[str, Any]:
    gemma = photo.gemma or {}
    return {
        "id": photo.id,
        "file_name": photo.file_name,
        "original_path": photo.file_path,
        "thumbnail_path": photo.thumbnail_path,
        "width": photo.width,
        "height": photo.height,
        "technical_score": round(photo.technical_score, 1),
        "blur_score": round(photo.blur_score, 1),
        "brightness_score": round(photo.brightness_score, 1),
        "contrast_score": round(photo.contrast_score, 1),
        "face_detected": photo.face_detected,
        "number_of_faces": photo.number_of_faces,
        "face_clarity_score": round(photo.face_clarity_score, 1),
        "gemma_overall_score": gemma.get("overall_score", ""),
        "gemma_category": photo.category,
        "best_slot": photo.best_slot,
        "final_score": round(photo.final_score, 2),
        "status": photo.status,
        "strengths": "; ".join(photo.strengths),
        "weaknesses": "; ".join(photo.weaknesses),
        "reason": photo.reason,
        "reject_reason": photo.reject_reason or "",
    }


# Writes the CSV report and returns its path.
def write_csv(photos: list[Photo], config: dict[str, Any]) -> Path:
    base = ensure_dir(resolve_path(config["output"]["base_dir"]))
    out_path = base / "report.csv"
    frame = pd.DataFrame([_record(p) for p in photos])
    frame.to_csv(out_path, index=False, encoding="utf-8")
    return out_path


# Renders a single photo card for the HTML report.
def _html_card(photo: Photo) -> str:
    thumb = html.escape(Path(photo.thumbnail_path).as_uri()) if photo.thumbnail_path else ""
    img = f'<img src="{thumb}" alt="" />' if thumb else '<div class="noimg">No preview</div>'
    strengths = "".join(f"<li>{html.escape(s)}</li>" for s in photo.strengths)
    weaknesses = "".join(f"<li>{html.escape(w)}</li>" for w in photo.weaknesses)
    reject = (
        f'<div class="reject">Rejected: {html.escape(photo.reject_reason)}</div>'
        if photo.reject_reason
        else ""
    )
    return f"""
    <div class="card">
      {img}
      <div class="body">
        <div class="row"><span class="score">{photo.final_score:.1f}</span>
          <span class="chip">{html.escape(photo.category)}</span></div>
        <div class="name">{html.escape(photo.file_name)}</div>
        <div class="slot">Slot: {html.escape(photo.best_slot)}</div>
        <p class="reason">{html.escape(photo.reason)}</p>
        <ul class="strengths">{strengths}</ul>
        <ul class="weaknesses">{weaknesses}</ul>
        {reject}
      </div>
    </div>
    """


# Writes the self-contained HTML report and returns its path.
def write_html(photos: list[Photo], config: dict[str, Any]) -> Path:
    reports_dir = ensure_dir(resolve_path(config["output"]["reports_dir"]))
    out_path = reports_dir / "report.html"
    ordered = sorted(photos, key=lambda p: p.final_score, reverse=True)
    cards = "".join(_html_card(p) for p in ordered)
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8" />
<title>AuraFrame AI Report</title>
<style>
  body {{ background:#2D2F42; color:#F3D8DD; font-family:Inter,system-ui,sans-serif; margin:0; padding:24px; }}
  h1 {{ color:#fff; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(220px,1fr)); gap:18px; }}
  .card {{ background:#36384D; border:1px solid rgba(243,216,221,.14); border-radius:12px; overflow:hidden; }}
  .card img,.noimg {{ width:100%; aspect-ratio:4/5; object-fit:cover; display:block; background:#262839; }}
  .noimg {{ display:flex; align-items:center; justify-content:center; }}
  .body {{ padding:12px; }}
  .row {{ display:flex; justify-content:space-between; align-items:center; }}
  .score {{ background:#D46C7F; color:#fff; padding:2px 8px; border-radius:8px; font-weight:700; }}
  .chip {{ font-size:11px; border:1px solid rgba(243,216,221,.2); border-radius:999px; padding:2px 8px; }}
  .name {{ color:#F7E6EA; margin-top:6px; font-size:13px; }}
  .slot {{ font-size:11px; opacity:.8; }}
  .reason {{ font-size:12px; }}
  .strengths li {{ color:#8FD6B5; }} .weaknesses li {{ color:#F0B86E; }}
  .reject {{ color:#F6798C; font-size:12px; }}
</style></head>
<body>
  <h1>AuraFrame AI - Photo Review Report</h1>
  <p>{len(photos)} photos analyzed locally. Originals were never modified.</p>
  <div class="grid">{cards}</div>
</body></html>
"""
    out_path.write_text(document, encoding="utf-8")
    return out_path


# Writes both reports and emits a log message.
def generate_reports(photos: list[Photo], config: dict[str, Any]) -> None:
    csv_path = write_csv(photos, config)
    html_path = write_html(photos, config)
    events.log(f"Reports written: {csv_path.name}, {html_path.name}", "info")


# Returns the configured reports directory.
def _reports_dir(config: dict[str, Any]) -> Path:
    return ensure_dir(resolve_path(config["output"]["reports_dir"]))


# Builds one row of the Local Minimal Gate report.
def _gate_record(photo: Photo) -> dict[str, Any]:
    return {
        "photo_id": photo.id,
        "original_path": photo.file_path,
        "status": photo.gate_status or photo.status,
        "local_score": round(photo.technical_score, 1),
        "hard_reject_reason": photo.hard_reject_reason or (photo.reject_reason or ""),
        "soft_flags": "; ".join(photo.soft_flags),
        "eligible_for_openai": photo.eligible_for_openai,
        "priority_for_openai": photo.openai_priority,
    }


# Writes the Local Minimal Gate report (one row per photo) and returns its path.
def write_gate_report(photos: list[Photo], config: dict[str, Any]) -> Path:
    out_path = _reports_dir(config) / "local_minimal_gate_report.csv"
    pd.DataFrame([_gate_record(p) for p in photos]).to_csv(out_path, index=False, encoding="utf-8")
    return out_path


# Builds one row of the OpenAI Quality Mode CSV report.
def _openai_record(photo: Photo) -> dict[str, Any]:
    gemma = photo.gemma or {}
    return {
        "id": photo.id,
        "anonymized_id": photo.id,
        "original_path": photo.file_path,
        "thumbnail_path": photo.thumbnail_path,
        "width": photo.width,
        "height": photo.height,
        "local_status": photo.gate_status or photo.status,
        "local_score": round(photo.final_score, 2),
        "technical_score": round(photo.technical_score, 1),
        "face_quality_score": round(photo.face_quality_score, 1),
        "soft_flags": "; ".join(photo.soft_flags),
        "hard_reject_reason": photo.hard_reject_reason or (photo.reject_reason or ""),
        "eligible_for_openai": photo.eligible_for_openai,
        "gemma_score": gemma.get("overall_score", ""),
        "openai_broad_score": round(photo.openai_broad_score, 2),
        "openai_quality_score": round(photo.openai_quality_score, 2),
        "openai_premium_score": round(photo.openai_premium_score, 2),
        "final_judge_score": round(photo.final_judge_score, 2),
        "consensus_score": round(photo.consensus_score, 2),
        "category": photo.category,
        "best_slot": photo.best_slot,
        "final_selected_slot": photo.final_selected_slot,
        "in_top_6": photo.in_top_6,
        "in_top_20": photo.in_top_20,
        "in_top_50": photo.in_top_50,
        "in_top_100": photo.in_top_100,
        "openai_reason": photo.openai_reason,
        "openai_risk": photo.openai_risk,
        "reject_reason": photo.reject_reason or "",
        "needs_manual_review": photo.needs_manual_review,
        "failed_openai_review": photo.failed_openai_review,
        "estimated_cost_usd": round(photo.estimated_cost_usd, 6),
    }


# Writes the OpenAI Quality Mode CSV report and returns its path.
def write_openai_csv(photos: list[Photo], config: dict[str, Any]) -> Path:
    out_path = _reports_dir(config) / "openai_quality_report.csv"
    pd.DataFrame([_openai_record(p) for p in photos]).to_csv(out_path, index=False, encoding="utf-8")
    return out_path


# Renders one OpenAI photo card for the HTML report.
def _openai_card(photo: Photo) -> str:
    thumb = html.escape(Path(photo.thumbnail_path).as_uri()) if photo.thumbnail_path else ""
    img = f'<img src="{thumb}" alt="" />' if thumb else '<div class="noimg">No preview</div>'
    flags = "".join(f'<span class="flag">{html.escape(f)}</span>' for f in photo.soft_flags)
    review = '<span class="rev">manual review</span>' if photo.needs_manual_review else ""
    return f"""
    <div class="card">
      {img}
      <div class="body">
        <div class="row"><span class="score">{photo.consensus_score:.1f}</span>
          <span class="chip">{html.escape(photo.final_selected_slot or photo.best_slot)}</span></div>
        <div class="scores">L {photo.final_score:.1f} · B {photo.openai_broad_score:.1f}
          · Q {photo.openai_quality_score:.1f} · P {photo.openai_premium_score:.1f}</div>
        <p class="reason">{html.escape(photo.openai_reason)}</p>
        <p class="risk">{html.escape(photo.openai_risk)}</p>
        <div class="flags">{flags}{review}</div>
      </div>
    </div>
    """


# Renders a titled grid section of OpenAI photo cards.
def _openai_section(title: str, photos: list[Photo]) -> str:
    if not photos:
        return ""
    cards = "".join(_openai_card(p) for p in photos)
    return f'<h2>{html.escape(title)} ({len(photos)})</h2><div class="grid">{cards}</div>'


# Writes the self-contained OpenAI Quality Mode HTML report and returns its path.
def write_openai_html(photos: list[Photo], config: dict[str, Any]) -> Path:
    out_path = _reports_dir(config) / "openai_quality_report.html"
    by_consensus = sorted(
        (p for p in photos if p.eligible_for_openai), key=lambda p: p.consensus_score, reverse=True
    )
    total_cost = sum(p.estimated_cost_usd for p in photos)
    sections = "".join(
        [
            _openai_section("Top 6 — Final Set", [p for p in by_consensus if p.in_top_6]),
            _openai_section("Top 20 — Best", [p for p in by_consensus if p.in_top_20]),
            _openai_section("Top 50 — Review", [p for p in by_consensus if p.in_top_50]),
            _openai_section("Needs Manual Review", [p for p in by_consensus if p.needs_manual_review]),
            _openai_section(
                "Hard Rejected (extreme only)",
                [p for p in photos if p.gate_status in {"hard_reject", "failed_read"}],
            ),
        ]
    )
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8" />
<title>AuraFrame AI — OpenAI Quality Report</title>
<style>
  body {{ background:#2D2F42; color:#F3D8DD; font-family:Inter,system-ui,sans-serif; margin:0; padding:24px; }}
  h1,h2 {{ color:#fff; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(220px,1fr)); gap:18px; margin-bottom:28px; }}
  .card {{ background:#36384D; border:1px solid rgba(243,216,221,.14); border-radius:12px; overflow:hidden; }}
  .card img,.noimg {{ width:100%; aspect-ratio:4/5; object-fit:cover; display:block; background:#262839; }}
  .noimg {{ display:flex; align-items:center; justify-content:center; }}
  .body {{ padding:12px; }}
  .row {{ display:flex; justify-content:space-between; align-items:center; }}
  .score {{ background:#D46C7F; color:#fff; padding:2px 8px; border-radius:8px; font-weight:700; }}
  .chip {{ font-size:11px; border:1px solid rgba(243,216,221,.2); border-radius:999px; padding:2px 8px; }}
  .scores {{ font-size:11px; opacity:.85; margin-top:6px; }}
  .reason {{ font-size:12px; }} .risk {{ font-size:12px; color:#F0B86E; }}
  .flag {{ display:inline-block; font-size:10px; background:#2A2C3D; border-radius:6px; padding:1px 6px; margin:2px 2px 0 0; }}
  .rev {{ display:inline-block; font-size:10px; color:#F6798C; }}
</style></head>
<body>
  <h1>AuraFrame AI — OpenAI Quality Report</h1>
  <p>{len(photos)} photos. Estimated OpenAI cost: ${total_cost:.2f}. Only resized, EXIF-stripped
     copies were sent; originals were never modified or uploaded.</p>
  {sections}
</body></html>
"""
    out_path.write_text(document, encoding="utf-8")
    return out_path


# Writes the OpenAI Quality Mode CSV + HTML reports and emits a log message.
def generate_openai_reports(photos: list[Photo], config: dict[str, Any]) -> None:
    csv_path = write_openai_csv(photos, config)
    html_path = write_openai_html(photos, config)
    events.log(f"OpenAI reports written: {csv_path.name}, {html_path.name}", "info")
