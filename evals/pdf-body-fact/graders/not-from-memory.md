---
type: regex
pattern: "from (my )?(memory|knowledge|training)|could(n't| not) (fetch|access|download|retrieve|find)|unable to (fetch|access|download|retrieve|find)|was declined|don't have access"
flags: "i"
match: not_contains
---
The answer must come from the document, not from a fallback or a refusal.
