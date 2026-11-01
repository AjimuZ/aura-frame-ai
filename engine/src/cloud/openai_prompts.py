"""Prompts for the four OpenAI Quality Mode stages.

The prompts never ask the model to identify the person or to infer sensitive
characteristics (ethnicity, religion, health, orientation, social status). They evaluate
only photographic effectiveness and dating-profile usefulness. PROMPT_VERSIONS feed the
cache key so a prompt change invalidates stale cached results.
"""

import json
from typing import Any

PROMPT_VERSIONS: dict[str, str] = {
    "broad_scan": "v1",
    "quality_review": "v1",
    "premium_review": "v1",
    "final_judge": "v1",
}

BROAD_SCAN_PROMPT = """Analizza questa foto per un profilo dating maschile.

Regole:
- La persona principale è sempre l'utente.
- Non identificare la persona.
- Non fare inferenze su etnia, religione, salute, orientamento, status sociale o caratteristiche sensibili.
- Valuta solo qualità fotografica e utilità per dating app.
- Obiettivo: autentico, pulito, sicuro, interessante, approcciabile.
- Penalizza foto buie, sfocate, confuse, troppo posate, troppo selfie, volto nascosto, gruppo non chiaro.

Rispondi solo JSON valido:

{
  "score": 1,
  "face": 1,
  "naturalness": 1,
  "style": 1,
  "context": 1,
  "category": "HEADSHOT | FULL_BODY | ACTIVITY | SOCIAL | CANDID | ELEGANT | BAD",
  "best_slot": "main_photo | full_body | activity_photo | social_photo | candid_photo | elegant_photo | backup | reject",
  "reject": false,
  "reason": "max 12 parole"
}
"""

QUALITY_REVIEW_PROMPT = """Analizza questa foto come candidata per un profilo dating maschile.

Contesto:
- La persona principale è sempre l'utente.
- Non devi identificare la persona.
- Non devi fare inferenze sensibili.
- Valuta solo efficacia fotografica, naturalezza, presenza, pulizia visiva e utilità nel profilo.
- Il profilo deve sembrare autentico, sicuro, interessante e non costruito.

Criteri: prima impressione, chiarezza volto, luce, naturalezza, outfit/stile, contesto,
postura, approcciabilità, sicurezza senza arroganza, utilità nello slot profilo.

Rispondi solo JSON valido:

{
  "score": 1,
  "main_photo_score": 1,
  "face_clarity": 1,
  "naturalness": 1,
  "approachability": 1,
  "confidence": 1,
  "style_score": 1,
  "context_score": 1,
  "category": "HEADSHOT | FULL_BODY | ACTIVITY | SOCIAL | CANDID | ELEGANT | BAD",
  "best_slot": "main_photo | full_body | activity_photo | social_photo | candid_photo | elegant_photo | backup | reject",
  "keep": true,
  "reason": "breve spiegazione concreta",
  "risk": "eventuale problema"
}
"""

PREMIUM_REVIEW_PROMPT = """Valuta questa foto come candidata forte per un profilo dating maschile.

Non devi giudicare la persona. Devi giudicare solo la foto.

Il profilo finale deve sembrare: autentico, pulito, sicuro, interessante, approcciabile,
non disperato, non costruito, non cringe.

Penalizza: foto troppo posate, selfie deboli, foto buie, volto poco chiaro, soggetto chiuso,
foto troppo LinkedIn, foto troppo confuse, gruppo dove non è chiaro il soggetto, foto belle
artisticamente ma inutili per dating app.

Rispondi solo JSON:

{
  "score": 1,
  "dating_value": 1,
  "profile_value": 1,
  "authenticity": 1,
  "confidence": 1,
  "approachability": 1,
  "style_score": 1,
  "context_score": 1,
  "best_slot": "main_photo | full_body | activity_photo | social_photo | candid_photo | elegant_photo | backup | reject",
  "keep_for_top_20": true,
  "keep_for_top_50": true,
  "reject_from_final_set": false,
  "reason": "spiegazione breve e concreta",
  "risk": "eventuale problema"
}
"""

_FINAL_JUDGE_INSTRUCTIONS = """Devi costruire il miglior set di foto per un profilo dating maschile partendo da queste candidate.

Regole:
- Non identificare la persona.
- Non fare inferenze sensibili.
- Valuta solo efficacia fotografica e utilità nel profilo.
- Il profilo deve sembrare autentico, sicuro, interessante e approcciabile.
- Non scegliere 6 foto tutte simili. Non solo selfie. Non solo primi piani.
- Non scegliere foto gruppo come prima foto. Niente foto troppo posate o cringe.
- Includi varietà reale. Top 6 = titolari, Top 20 = panchina di lusso.

Seleziona Top 6 (uno per slot: main_photo, full_body, activity_photo, candid_photo,
social_photo, elegant_photo), Top 20, e i candidate per slot. Usa i photo_id forniti.

Rispondi solo JSON valido:

{
  "top_6": [{"photo_id": "string", "slot": "main_photo", "reason": "perché", "risk": "rischio"}],
  "top_20": ["photo_id"],
  "slot_candidates": {
    "main_photo": ["photo_id"], "full_body": ["photo_id"], "activity_photo": ["photo_id"],
    "candid_photo": ["photo_id"], "social_photo": ["photo_id"], "elegant_photo": ["photo_id"]
  },
  "final_notes": "breve sintesi del profilo"
}
"""


# Returns the static prompt text for a single-image stage (broad/quality/premium).
def prompt_for(stage: str) -> str:
    return {
        "broad_scan": BROAD_SCAN_PROMPT,
        "quality_review": QUALITY_REVIEW_PROMPT,
        "premium_review": PREMIUM_REVIEW_PROMPT,
    }[stage]


# Builds the comparative Final Judge prompt by appending the anonymized candidate metadata
# (photo_id + scores + category/slot only — never file names, paths, or EXIF).
def build_final_judge_prompt(candidates_meta: list[dict[str, Any]]) -> str:
    meta = json.dumps(candidates_meta, ensure_ascii=False, indent=2)
    return f"{_FINAL_JUDGE_INSTRUCTIONS}\nCandidate (metadati anonimi):\n{meta}\n"
