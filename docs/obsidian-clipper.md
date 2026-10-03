# Obsidian Web Clipper template

A second ingestion path. The n8n pipeline distills spoken transcripts; the Clipper
captures web pages. Both land in the same Git vault, so the indexer picks either up
without changing.

```
transcript → n8n (five agents) ─┐
                                ├→ Git vault → GitHub → indexer → Qdrant → MCP
web page   → Clipper ───────────┘
```

## Why the Clipper distills instead of n8n

The five agents exist to pull decisions and action items out of **speech**, which is
unstructured and repetitive. A web article is already written and already
structured — running it through that pipeline would add a hop without adding
signal. The Clipper's Interpreter does it at capture time, in one request, with a
small model.

## Two rules the template must honour

The indexer makes exactly two assumptions about a note:

| Rule | Why |
|---|---|
| An `# H1` must exist | the title is extracted with `^#\s+(.+)$` |
| Sections use `##` | chunking splits on them; without any, long pages fall back to paragraph splitting |

And one mechanism worth reusing: the indexer drops everything from `> [!note]`
onward. The transcript notes keep their raw audio transcript there so it stays
readable in the file but never dominates the embedding. The captured article body
goes in the same place, for the same reason.

## Settings

Create a template in the Clipper (Settings → Templates → New) with these fields.

**Behavior** — `Create new note`

**Note name**

```
{{date|date:"YYYY-MM-DD"}} - {{title|kebab|safe_name}}
```

Matches the filename convention the n8n pipeline already writes
(`2026-10-03 - kickoff-do-second-brain.md`), so both paths produce one vocabulary.

**Path**

```
03 - Recursos
```

Clipped material is reference by definition, so it skips the PARA classifier.
Move a clip to `01 - Projetos` by hand on the rare occasion it belongs to one.

**Properties**

| Name | Value | Type |
|---|---|---|
| `tipo` | `clip` | Text |
| `status` | `bruta` | Text |
| `fonte` | `{{url}}` | Text |
| `autor` | `{{author}}` | Text |
| `dominio` | `{{domain}}` | Text |
| `publicado` | `{{published}}` | Date |
| `clipado` | `{{date|date:"YYYY-MM-DD"}}` | Date |
| `tags` | `clip, {{"uma a quatro tags em kebab-case, separadas por vírgula, sobre o tema desta página"}}` | Multitext |

`tipo: clip` vs `tipo: referencia` is the field that later lets search filter your
own writing apart from material you merely saved.

**Template triggers** — leave empty for the default template, or scope it:

```
https://
/^https:\/\/(www\.)?(github|arxiv|anthropic)\.com\//
```

## Content

```markdown
# {{title}}

> [!info] Resumo em 1 linha
> {{"Resuma esta página em uma única frase em português. Use apenas o que está na página."}}

## Por que salvei isto
{{"Em uma frase, por que este conteúdo é útil para um engenheiro de IA. Se a página não permitir dizer, escreva exatamente: motivo não registrado."}}

## Pontos principais
{{"Liste de 3 a 6 pontos principais da página, em bullets, em português. Apenas o que está escrito na página; não complete com conhecimento externo."}}

## Como se aplica
{{"Liste em bullets como isto se aplicaria a um sistema de IA em produção — RAG, agentes, avaliação ou infraestrutura. Se não for possível inferir da página, escreva exatamente: não aplicável diretamente."}}

## Fonte
- {{url}}
- autor: {{author}}
- publicado: {{published}}
- clipado em: {{date|date:"YYYY-MM-DD"}}

---

> [!note]- 📄 Conteúdo capturado — clique para expandir
> {{content|blockquote}}
```

### Why the prompts are written that way

Each one names an escape hatch — *"escreva exatamente: motivo não registrado"* —
and forbids outside knowledge. Without that, a small model fills every section with
something plausible, and a note that invents its own relevance is worse than no
note: it reads as your judgement when it is the model's.

Same discipline as the n8n agents, which are told that *"decisão é algo definido ou
combinado, não um assunto discutido"*. If two categories are close, the prompt has
to name the difference.

## The real risk is not technical

Clipping is cheap, writing is not. Two hundred impulse clips bury the handful of
notes you actually reasoned through, and search starts returning somebody's blog
post instead of your own thinking.

Two defences are built into the template above:

**"Por que salvei isto"** forces one sentence of your own intent. A clip whose
reason reads *motivo não registrado* is a clip you will never consult — and it is
visible, so it can be pruned.

**`tipo: clip`** keeps the two kinds separable. The Qdrant payload already carries
note metadata, so `search_notes` can grow a filter when the ratio starts to hurt.

## Checking it works

After the first clip, the Obsidian Git plugin commits and pushes. Then:

```bash
make count          # before
# run the indexer in n8n
make count          # after
```

A page with `##` sections should add several points, one per section. If it adds
exactly one, the Interpreter did not run and the note has no headings — check that
the model is configured under Settings → Interpreter.
