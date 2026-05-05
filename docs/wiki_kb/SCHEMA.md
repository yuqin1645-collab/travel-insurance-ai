# Wiki Schema

## Domain
旅行保险AI自动审核系统（travel-insurance-ai）——基于 LLM 的航班延误、行李延误、行李损坏等险种的自动化理赔审核 pipeline。

## Conventions
- File names: lowercase, hyphens, no spaces (e.g., `baggage-delay-pipeline.md`)
- Every wiki page starts with YAML frontmatter (see below)
- Use `[[wikilinks]]` to link between pages (minimum 2 outbound links per page)
- When updating a page, always bump the `updated` date
- Every new page must be added to `index.md` under the correct section
- Every action must be appended to `log.md`
- **Provenance markers:** On pages that synthesize 3+ sources, append `^[raw/articles/source-file.md]`
  at the end of paragraphs whose claims come from a specific source.

## Frontmatter
```yaml
---
title: Page Title
created: YYYY-MM-DD
updated: YYYY-MM-DD
type: entity | concept | comparison | query | summary
tags: [from taxonomy below]
sources: [raw/articles/source-name.md]
confidence: high | medium | low
contested: true
contradictions: [other-page-slug]
---
```

## Tag Taxonomy
- **Modules:** baggage-delay, baggage-damage, flight-delay, claim-type
- **Architecture:** pipeline, engine, rules, prompts, scheduler, database
- **Concepts:** compensation, exclusion, policy-validity, identity-check, material-gate, tier-lookup
- **Operations:** review, push, sync, download, report
- **Meta:** convention, pitfall, checklist, architecture-decision

Rule: every tag on a page must appear in this taxonomy. Add new tags here first.

## Page Thresholds
- **Create a page** when an entity/concept appears in 2+ sources OR is central to one source
- **Add to existing page** when a source mentions something already covered
- **DON'T create a page** for passing mentions or minor details
- **Split a page** when it exceeds ~200 lines
- **Archive a page** when its content is fully superseded

## Entity Pages
One page per notable entity (module, rule file, script, database table). Include:
- Overview / what it is
- Key facts and dates
- Relationships to other entities ([[wikilinks]])
- Source references

## Concept Pages
One page per concept or topic. Include:
- Definition / explanation
- Current state of knowledge
- Open questions or debates
- Related concepts ([[wikilinks]])

## Update Policy
When new information conflicts with existing content:
1. Check the dates — newer sources generally supersede older ones
2. If genuinely contradictory, note both positions with dates and sources
3. Mark the contradiction in frontmatter: `contradictions: [page-name]`
4. Flag for user review in the lint report
