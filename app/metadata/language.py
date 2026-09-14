"""Guard for the rule "nothing in Spanish in the main YouTube signal"."""

from __future__ import annotations

import re

# Proper nouns and English-market search terms that happen to be Spanish.
ALLOWED_PHRASES = (
    "costa rica", "pura vida", "la fortuna", "monteverde", "arenal", "manuel antonio", "tortuguero",
    "rincon de la vieja", "corcovado", "guanacaste", "puerto viejo", "santa teresa", "san jose",
)

# Words that are unambiguous Spanish in this niche; English homographs (sin, mar, rio, son, al...) are left out.
SPANISH_WORDS = {
    "el", "los", "las", "una", "unos", "unas", "del", "para", "por", "que", "muy", "sonido", "sonidos",
    "lluvia", "dormir", "relajante", "relajarse", "relajacion", "estudiar", "naturaleza", "bosque", "selva",
    "olas", "trueno", "truenos", "tormenta", "viento", "fuego", "chimenea", "pajaros", "noche", "horas",
    "musica", "meditacion", "profundo", "catarata", "cascada", "ambiente", "relajacion",
}
SPANISH_CHARS = re.compile(r"[ñáéíóúü¿¡]", re.IGNORECASE)
WORD = re.compile(r"[a-záéíóúüñ]+", re.IGNORECASE)


def spanish_findings(text: str) -> list[str]:
    lowered = text.lower()
    for phrase in ALLOWED_PHRASES:
        lowered = lowered.replace(phrase, " ")
    findings = sorted({m.group(0) for m in SPANISH_CHARS.finditer(lowered)})
    findings += sorted({w for w in WORD.findall(lowered) if w in SPANISH_WORDS})
    return findings


def is_english_only(text: str) -> bool:
    return not spanish_findings(text)
