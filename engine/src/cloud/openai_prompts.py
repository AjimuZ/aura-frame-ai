"""Prompts for the four OpenAI Quality Mode stages.

The prompts never ask the model to identify a person or to infer sensitive
characteristics (ethnicity, religion, health, orientation, social status). They evaluate
only the photograph: technique, composition, light and how memorable it is.
PROMPT_VERSIONS feed the cache key so a prompt change invalidates stale cached results.
"""

import json
from typing import Any

PROMPT_VERSIONS: dict[str, str] = {
    "broad_scan": "v2",
    "quality_review": "v2",
    "premium_review": "v2",
    "final_judge": "v2",
}

BROAD_SCAN_PROMPT = """Analizza questa foto come fotografia, qualunque sia il soggetto.

Regole:
- Non identificare mai le persone.
- Non fare inferenze su etnia, religione, salute, orientamento, status sociale o caratteristiche sensibili.
- Valuta solo la qualità fotografica, mai l'aspetto di una persona.
- Obiettivo: foto nitida, pulita, ben composta, interessante, memorabile.
- Penalizza foto buie, sfocate, confuse, troppo posate, selfie deboli, soggetto nascosto, gruppo non chiaro.

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

QUALITY_REVIEW_PROMPT = """Analizza questa foto come candidata tra le più belle di una raccolta.

Contesto:
- Non devi identificare le persone.
- Non devi fare inferenze sensibili.
- Valuta solo efficacia fotografica, naturalezza, presenza, pulizia visiva e utilità nella selezione.
- La foto deve sembrare autentica, interessante e non costruita.

Criteri: impatto visivo, chiarezza del soggetto, luce, naturalezza, stile, contesto,
postura, espressività, equilibrio, utilità nella selezione finale.

Rispondi solo JSON valido:

{
  "score": 1,
  "main_photo_score": 1,
  "face_clarity": 1,
  "naturalness": 1,
  "appeal": 1,
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

PREMIUM_REVIEW_PROMPT = """Valuta questa foto come candidata forte tra le più belle di una raccolta.

Non devi giudicare le persone. Devi giudicare solo la foto.

La foto migliore deve sembrare: autentica, pulita, interessante, espressiva,
non costruita, non banale.

Penalizza: foto troppo posate, selfie deboli, foto buie, soggetto poco chiaro, soggetto chiuso,
foto troppo formali, foto troppo confuse, gruppo dove non è chiaro il soggetto, foto
tecnicamente corrette ma prive di interesse.

Rispondi solo JSON:

{
  "score": 1,
  "overall_value": 1,
  "selection_value": 1,
  "authenticity": 1,
  "confidence": 1,
  "appeal": 1,
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

_FINAL_JUDGE_INSTRUCTIONS = """Devi costruire il miglior set di foto, tra le più belle della raccolta, partendo da queste candidate.

Regole:
- Non identificare le persone.
- Non fare inferenze sensibili.
- Valuta solo efficacia fotografica e utilità nella selezione.
- Il set deve sembrare autentico, interessante e vario.
- Non scegliere 6 foto tutte simili. Non solo selfie. Non solo primi piani.
- Non scegliere foto di gruppo come foto principale. Niente foto troppo posate.
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
  "final_notes": "breve sintesi del set"
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
