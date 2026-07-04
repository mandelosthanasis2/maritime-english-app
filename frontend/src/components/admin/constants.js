// Shared admin vocabularies + Greek labels (moved out of the old single-page
// Admin.jsx so every tab uses the same lists). Mirrors the backend enums in
// backend/admin.py — items use A1–C1 difficulty, lessons use A2–C2 CEFR.

export const DIFFICULTIES = ['A1', 'A2', 'B1', 'B2', 'C1']
export const SKILL_TYPES = [
  'teaching',
  'vocabulary',
  'listening',
  'fill_gap',
  'word_order',
  'speaking',
  'roleplay',
]
export const TRACKS = ['maritime', 'grammar', 'email']
export const TRACK_LABEL = { grammar: 'Γραμματική', maritime: 'Maritime', email: '✉️ Email' }
export const ROLE_CATEGORIES = ['engineer', 'deck', 'common']
export const ROLE_LABEL = {
  engineer: '⚙️ Μηχανικοί',
  deck: '🧭 Κατάστρωμα',
  common: '🤝 Κοινά για όλους',
}
export const CEFR_LEVELS = ['A2', 'B1', 'B2', 'C1', 'C2']
export const SKILL_AREAS = ['vocabulary', 'grammar', 'listening', 'speaking']
export const SKILL_AREA_LABEL = {
  vocabulary: '📖 Vocabulary',
  grammar: '📐 Grammar',
  listening: '👂 Listening',
  speaking: '🎙️ Speaking',
}
export const KINDS = [
  { value: 'auto', label: 'Αυτόματο' },
  { value: 'grammar', label: 'Γραμματική' },
  { value: 'maritime', label: 'Maritime' },
  { value: 'email', label: '✉️ Email Writing' },
]

// Editorial kind of an item (skill_type preferred, dialogue/translation aside).
export function itemKind(item) {
  return (item.skill_type || item.type || '').toLowerCase()
}
