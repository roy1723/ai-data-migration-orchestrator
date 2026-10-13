# AI Data Migration Orchestrator (multi-agent)

Four LLM agents cooperate, with no human in the loop, to turn a source schema, a target schema and a field-mapping sheet into migration SQL (Raiser's Edge -> Salesforce Nonprofit Cloud), a validation report and a plain-English explanation. A Memory Agent, an automatic re-prompt loop, a CLI summary and a Streamlit UI are included as extras.

All data is dummy and small: 4 source tables, 5 target objects, 21 mapping rows. Salesforce object names (`GiftTransaction`, `GiftDesignation`) follow the Nonprofit Cloud docs; the field names are illustrative and were not checked against a real org.

## Run it (about two minutes)

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python main.py --reset-memory            # offline stub, no key needed
export GEMINI_API_KEY=...                # Windows: set GEMINI_API_KEY=...
python main.py --llm gemini --reset-memory   # real model (Google)
export GROQ_API_KEY=...                  # Windows: set GROQ_API_KEY=...
python main.py --llm groq --reset-memory     # real model (Groq, OpenAI-compatible API)
streamlit run ui/app.py                  # optional UI
pytest -q                                # 34 tests, no network
```

### API key via `.env`

```bash
cp .env.example .env        # Windows: copy .env.example .env
# edit .env and set GEMINI_API_KEY and/or GROQ_API_KEY (and the matching *_MODEL if needed)
python main.py --llm gemini --reset-memory
python main.py --llm groq --reset-memory
```

`main.py` loads `.env` automatically (python-dotenv); a real environment variable takes precedence.

### Docker

```bash
docker build -t migration-tool .
docker run --rm -v "$PWD/outputs:/app/outputs" migration-tool                                   # offline stub
docker run --rm --env-file .env -v "$PWD/outputs:/app/outputs" migration-tool --llm gemini --reset-memory
# or with compose:
docker compose run --rm cli --llm gemini --reset-memory
docker compose up ui        # Streamlit at http://localhost:8501
```

Options: `--max-retries N` (re-prompts after the first attempt, default 2), `--no-memory`, `--reset-memory`, `--data-dir`, `--out-dir`. Exit code is 0 when the final SQL passes validation.

## Which model, and an important disclosure

- **Real model (used to generate the shipped outputs): Groq**, via its OpenAI-compatible chat completions API (`llm/groq_client.py`, stdlib `urllib`, no extra SDK). Default model is `openai/gpt-oss-20b` (`GROQ_MODEL`), temperature 0, JSON mode on. Two things specific to Groq's free tier that this client handles explicitly:
  - Groq's Cloudflare WAF rejects `urllib`'s default User-Agent as bot-like, so every request sends a custom one.
  - The free tier caps at 8000 tokens/minute, and `gpt-oss` models spend part of their budget on hidden reasoning before the JSON answer. The client sets `reasoning_effort: "low"` and an explicit `max_completion_tokens`, and on a 429 it parses the server's own "try again in Ns" and waits exactly that long before retrying (`RateLimitError.retry_after`) instead of blind exponential backoff.
- **Real model (integration untested live): Google Gemini** through the `google-genai` SDK (`llm/gemini_client.py`, `GEMINI_MODEL`/`GEMINI_API_KEY`). The client is covered by mocked unit tests but has not completed a live call in this environment — the free tier's daily quota (20 requests/day) was exhausted during testing before a full run finished. Treat it as implemented-and-tested-offline rather than proven end-to-end; `--llm groq` is the backend actually exercised against a real model.
- **Offline stub:** `llm/fake_llm.py` is a rule-based stand-in so the project runs and is testable without any key. It is **not a language model**. It reads the same prompts as the real backends, deliberately behaves naively on the first attempt (keeps a mapping row that points at a field that does not exist; forgets the default for a required field) so that the validator and the retry loop have something real to do.
- **The files in `outputs/` in this zip were generated with `--llm groq`** (check the header of `sample_output.sql` for the backend and model). That run is a genuine demonstration of the retry loop against a live model: attempt 1 failed validation (`date_parse_mismatch`, a day/month field-order bug the model introduced — see the Validation Logic section below), the Validator's feedback was sent back, and attempt 2 passed. Re-run with `--llm fake` or `--llm gemini` to compare. Swapping providers means writing one small subclass of `LLMClient`.

## How the agents coordinate

```
 memory_recall -> schema_analyst -> sql_generator -> validator --pass--> explainer -> memory_update
                                         ^               |
                                         +--- FIXABLE ---+   (feedback, at most max_attempts)
```

Built with LangGraph. All agents read and write one shared state (`core/state.py`). **Agents never call each other.** Each node returns a partial state update plus one message (`from`, `to`, `kind`, `attempt`, `payload`), and `main.py` appends every message to `outputs/agent_trace.jsonl`, so the whole conversation can be replayed.

| Agent | File | What the code does | What the LLM does |
|---|---|---|---|
| Schema Analyst | `agents/schema_analyst.py` | Parses schemas and mapping; type, length and nullability comparison; unmapped required fields; mapping rows that point to unknown fields; load order by topological sort of foreign keys | Writes the analysis brief: risks, priorities and documented assumptions for required fields with no mapping |
| SQL Generator | `agents/sql_generator.py` | Assembles the script; keeps feedback and previous SQL for retries | Prompt chain: (1) plan per target object, (2) write `INSERT ... SELECT` from the plan, with few-shot examples. On a retry it receives coded feedback and changes only what was flagged |
| Validation Agent | `agents/validator.py` | sqlglot static checks, SQLite dry run, row-count reconciliation, report | One semantic review whose findings must name a real statement and column or they are dropped |
| Explainer | `agents/explainer.py` | Coverage check (every statement and column explained), audit tables | Plain-English purpose, per-field sentences, assumptions and risks |
| Memory Agent | `agents/memory_agent.py` | Stores resolved defects in `data/memory.json`; recalls only hints that still apply | none |

### Design decisions worth reading

1. **Facts by code, judgement by the LLM.** Types, lengths, dependency order and "does this column exist" are computed. A model is never trusted to count characters or order foreign keys. The Schema Analyst overrides any load order the model returns.
2. **Two kinds of issue.** `FIXABLE` issues are SQL defects (unknown column, missing cast, wrong order, missing required column). They trigger a re-prompt. `FINDING`s are mapping or data problems (a mapped target field that does not exist, a required field with no mapping, a length mismatch, rows lost to a join). No SQL rewrite fixes those, so they never loop; they go into the report. Without this split a retry loop either never converges or hides real data problems.
3. **"No human in the loop"** means the pipeline never pauses for approval. Items that need a person (confirm a default, profile long emails) are still listed in the report, as in a real consulting hand-over.
4. **Validation is layered, cheapest first:** static checks -> dry run on in-memory SQLite with seeded rows -> LLM review. The dry run only runs when static checks pass, and the LLM review only runs when both are clean.
5. **A parsed date can still be wrong.** sqlglot's static check only confirms a parse function was used instead of a bare `CAST` - it cannot tell `'YYYY-MM-DD'` from `'YYYY-DD-MM'`, since both are syntactically "parsed". `core/sqlite_runner.check_date_parsing` replays every sample row's source date through the format documented in the mapping notes (e.g. `MM/DD/YYYY`) and compares it against what the dry run actually inserted, catching field-order bugs the LLM introduces (observed in practice: a live Groq run swapped day and month in the `SUBSTR` offsets; the mismatch triggered a re-prompt and attempt 2 fixed it).
6. **Prompts** live in `prompts/` as Markdown with `## SYSTEM` and `## USER` sections and `{{placeholders}}`. Every prompt demands one JSON object with a stated schema; `BaseAgent.ask` extracts the JSON and re-asks once if it is malformed.

## Dummy data and the problems planted in it

| # | Problem | Class | Where it is caught |
|---|---|---|---|
| 1 | `gift_total` decimal(12,2) mapped to a Text field (the example in the brief) | finding | Analyst |
| 2 | `gift_date` is text `MM/DD/YYYY`, target is Date: needs a parse, not a cast | finding + FIXABLE if the SQL only casts, or if it parses with the fields in the wrong order | Analyst, Validator (static check + `check_date_parsing` against sample rows) |
| 3 | `email` 120 characters into an 80-character field (plus three more length overflows) | finding | Analyst |
| 4 | Required `GiftTransaction.Status` has no mapping | assumption + FIXABLE if SQL omits it | Analyst, Validator |
| 5 | Mapping row 21 points at `GiftTransaction.PaymentChannel`, which does not exist | finding; FIXABLE if SQL writes it | Analyst, Validator |
| 6 | Organisations go to Account, individuals to Contact (filter from the mapping notes) | n/a | SQL Generator |
| 7 | Gifts from organisations are lost because the donor lookup uses Contact | finding | Dry-run reconciliation (2 of 7 rows) |
| 8 | `fund_description` is never mapped | info | Analyst |

On the first attempt the stub writes `PaymentChannel` and leaves out `Status`. The Validator returns both as coded feedback, and attempt 2 fixes them. With a real model (Groq), the specific defect on attempt 1 can vary run to run - the shipped `outputs/` were produced by a run where attempt 1 parsed `gift_date` with day and month swapped, caught by `check_date_parsing`, fixed on attempt 2. On a second run, the Memory Agent supplies past lessons up front and attempt 1 is more likely to pass outright (use `--reset-memory` to see the retry loop fresh).

## Outputs

| File | Content |
|---|---|
| `outputs/sample_output.sql` | One `INSERT ... SELECT` per object in load order, each commented with its rationale |
| `outputs/validation_report.md` | Result, findings with recommendations, dry-run row counts, assumptions, attempt history, checks performed |
| `outputs/sql_explanation.md` | Overview, per-statement plain English, field-level audit table, open items |
| `outputs/agent_trace.jsonl` | Every message between agents |
| `outputs/memory_after_first_run.json` | A one-time snapshot of `data/memory.json` taken after an early run, kept as a worked example of what the Memory Agent stores; it is not regenerated automatically and will not match `data/memory.json` after later runs |

## Layout

```
main.py            CLI and LangGraph wiring
agents/            one module per agent + base class
llm/               LLMClient interface, Gemini client, Groq client, offline stub
prompts/           prompt templates and few-shot examples
core/              schema utils, deterministic analysis, SQLite runner, state, trace
data/              dummy inputs and sample rows for the dry run
ui/app.py          Streamlit view: agent messages, retry timeline, SQL, report, explanation
tests/             pytest suite
```

## Assumptions and limitations

- The dry run uses SQLite, not Salesforce. Salesforce loads happen through Data Loader, Bulk API or an ETL tool, not SQL, so the SQL is a precise, reviewable specification of the transformation rather than something to run in Salesforce. Lookups to parent `Id`s are modelled by loading parents first into SQLite tables.
- The date parser assumes `MM/DD/YYYY`; malformed dates become NULL in SQLite and are flagged for profiling. `check_date_parsing` verifies the *parsed value* is correct against sample rows, but only for mappings whose notes spell out the format with `YYYY`/`MM`/`DD` tokens.
- The validator checks single `INSERT ... SELECT` statements with simple expressions. It is not a full SQL type checker.
- Memory is a flat JSON file keyed by (target, defect). It only stores defects of two kinds (unknown column, missing required column).
- Groq's free tier caps at 8000 tokens/minute; a full pipeline run with retries can approach that, and the client backs off using the server's own suggested wait time on a 429.
- The Gemini client has not been exercised against the live API in the environment where this was built (its daily free-tier quota was exhausted before a full run completed); it is covered by mocked unit tests only. Groq is the real backend actually used to produce the shipped `outputs/`.
