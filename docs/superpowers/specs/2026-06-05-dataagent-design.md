# Local PostgreSQL DataAgent Design

## Purpose

Build a local DataAgent on top of the existing Mini Claude Code architecture. The first version connects to a local PostgreSQL database, exposes a controlled data catalog to the main agent, lets the model write SQL for analyst questions, executes SQL through a safe backend path, and optionally delegates visualization to a plot sub-agent.

The system is for fast analyst-style querying and lightweight analysis. It is not a data ingestion platform, migration tool, BI semantic layer replacement, or general database admin agent.

## First-Version Scope

In scope:

- Connect to an existing local PostgreSQL database.
- Use a configured data catalog registry as the source of queryable tables, columns, metrics, and joins.
- Show the agent only a catalog index in the system prompt, then load detailed catalog entries on demand.
- Expose `get_data_catalog` and `execute_sql` as DataAgent tools.
- Let the model generate SQL itself after reading catalog context.
- Enforce SQL validation, read-only execution, timeout, row cap, and table allowlist inside the `execute_sql` backend.
- Add a project custom sub-agent type named `plot` via `.claude/agents/plot.md`.
- Let the plot sub-agent create charts from already returned query results or result artifacts.

Out of scope for the first version:

- Uploading local files and creating database tables.
- Database migrations or schema mutation.
- Write SQL of any kind.
- Cross-database connections.
- Long-running BI dashboards.
- Complex statistical modeling.
- Replacing the existing agent runtime with LangGraph or DeepAgent.

## Architectural Direction

Use the existing agent runtime as the shell and add a dedicated DataAgent subsystem.

The current codebase already has:

- A main agent loop with tool calling.
- A tool registry in `tools.py`.
- Custom sub-agent discovery through `.claude/agents/*.md`.
- Web and CLI entry points.
- Session, logging, and tracing infrastructure.

The DataAgent work should not push Text2SQL, SQL validation, and plotting behavior into the core `agent.py` loop. Instead, add small backend modules with narrow responsibilities and register a minimal set of tools.

## High-Level Flow

```text
User question
  -> Main agent reads catalog index from system prompt
  -> Main agent calls get_data_catalog(domain=...)
  -> Main agent writes SQL from the detailed catalog
  -> Main agent calls execute_sql(sql, purpose)
       -> backend parses and validates SQL
       -> backend rejects unsafe or out-of-catalog SQL
       -> backend applies LIMIT / row cap / timeout
       -> backend runs a read-only PostgreSQL transaction
       -> backend returns structured rows and stores a result artifact
  -> Main agent summarizes the result
  -> If visualization helps, main agent calls agent(type="plot", ...)
       -> plot sub-agent reads result artifact
       -> plot sub-agent writes chart artifact
       -> plot sub-agent returns chart path and interpretation
```

## Data Catalog Registry

The catalog registry is the data-domain equivalent of tool schema registration.

Tool schema tells the model what actions it can perform. Data catalog tells the model what data objects it can query and what they mean.

The system prompt should include only a domain index, not every table and column. Each index entry must be detailed enough to route the model to the right domain and prevent confusion between similar tables.

Example domain index:

```text
sales: Sales and order analysis. Use for GMV, order count, average order value,
channel sales performance, product/store/region sales ranking, payments, and
refund-adjusted net sales. Not for user behavior funnels, advertising spend, or
finance invoice reconciliation. Core tables include public.orders,
public.order_items, public.payments, and public.refunds.
```

Detailed catalog is loaded through `get_data_catalog`.

Example catalog file:

```yaml
domain: sales
description: >
  Sales and order analysis domain. Use for GMV, order count, average order value,
  channel sales performance, product/store/region sales ranking, payments, and
  refund-adjusted net sales.
primary_entities:
  - orders
  - payments
  - refunds
  - products
  - channels
common_questions:
  - Last month's GMV by channel
  - Daily order trend
  - Product sales top N
  - Refund rate analysis
tables:
  - name: public.orders
    description: Order fact table. One row per order.
    default_time_column: created_at
    columns:
      - name: order_id
        type: text
        description: Unique order id.
      - name: user_id
        type: text
        description: Buyer user id.
      - name: created_at
        type: timestamp
        description: Order creation time.
      - name: channel
        type: text
        description: Acquisition or sales channel.
      - name: gmv
        type: numeric
        description: Gross merchandise value before refunds.
metrics:
  - name: GMV
    expression: SUM(public.orders.gmv)
    description: Gross merchandise value before refunds.
joins:
  - left: public.orders.user_id
    right: public.users.id
    description: Join orders to users.
```

Catalog files should live in a project-controlled directory such as:

```text
.claude/data_catalog/*.yaml
```

The registry is the authority for both model context and SQL allowlist checks. The model should not see tables that the backend would reject.

## Exposed Tools

### `get_data_catalog`

Purpose: return detailed schema and business semantics for one or more registered data domains.

Inputs:

- `domain`: optional domain name. If omitted, return the domain index.
- `query`: optional natural-language hint. Later versions may use it to select relevant domains or tables.

Behavior:

- Returns the catalog index when no domain is provided.
- Returns detailed catalog for a selected domain.
- Never introspects arbitrary database schemas unless explicitly enabled later.
- Redacts anything not registered in the catalog.

### `execute_sql`

Purpose: execute analyst SQL safely against the local PostgreSQL database.

Inputs:

- `sql`: the SQL text written by the model.
- `purpose`: short human-readable reason for the query.
- `domain`: optional catalog domain. If provided, validation is constrained to that domain.

Important design point:

- Text2SQL is not a separate tool.
- SQL validation is not a separate tool.
- Read-only execution is not a separate tool.
- The model writes SQL and calls `execute_sql`.
- The backend always validates and executes through the safe path. The model cannot skip validation.

Output:

- Executed SQL, possibly normalized with an enforced limit.
- Column metadata.
- Rows, capped to a configured maximum.
- Row count returned.
- Whether the result was truncated.
- Artifact id/path for downstream plotting.
- Warnings such as "limit added", "result truncated", or "query timed out".

## SQL Safety Model

Security must be enforced in backend code, not by prompt instruction alone.

`execute_sql` must reject:

- Multi-statement SQL.
- Non-read-only SQL.
- `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `DROP`, `ALTER`, `CREATE`, `TRUNCATE`, `GRANT`, `REVOKE`, `VACUUM`, `ANALYZE`, `COPY`, `CALL`, `DO`, `SET`, `RESET`, and similar commands.
- Data definition, data manipulation, privilege, maintenance, or session mutation statements.
- Access to schemas, tables, or columns not registered in the data catalog.
- High-risk function calls unless explicitly allowlisted.
- Queries without a row cap when the result can be large.

`execute_sql` must enforce:

- PostgreSQL read-only transaction.
- `statement_timeout`.
- `lock_timeout`.
- Maximum returned rows.
- Maximum returned cells or approximate payload size.
- Default `LIMIT` for row-returning queries.
- Structured error messages that help the agent revise SQL safely.

Recommended database setup:

- Use a dedicated PostgreSQL role for the DataAgent.
- Grant only `CONNECT`, `USAGE` on allowed schemas, and `SELECT` on allowed tables/views.
- Do not use a superuser, owner role, or write-capable application role.

The database role is a second security boundary. The application validator is the first boundary.

## Plot Sub-Agent

Use the existing custom sub-agent mechanism. Add:

```text
.claude/agents/plot.md
```

The plot agent system prompt should say:

- It specializes in choosing suitable charts for query results.
- It does not access the database.
- It only uses supplied result artifacts or pasted tabular data.
- It should choose simple charts unless the user asks for something specific.
- It should output chart artifacts and a concise explanation.

Allowed tools should be limited. A practical first version can allow:

- `read_file`
- `write_file`
- `run_shell`

If the implementation adds a dedicated `create_plot` tool later, the plot agent should use that instead of raw shell commands.

Implementation detail:

Current `agent` tool schema lists `type` as an enum of `explore`, `plan`, and `general`. Custom agents already work in backend discovery, but the tool schema should be adjusted so the model can call `agent(type="plot")` reliably. Either remove the enum or dynamically include discovered custom agent names.

## Prompt Contract

The main DataAgent prompt should include:

- The catalog domain index.
- The rule that detailed schema must be requested through `get_data_catalog` before writing SQL for a domain.
- The rule that SQL must be executed only through `execute_sql`.
- The rule that failed SQL should be revised using the backend error message, not worked around with unsafe commands.
- The rule that plots are optional and should be used only when they clarify the answer.
- The rule that plot sub-agent receives result artifacts, not database credentials.

The prompt should not include full table definitions once the catalog registry exists, except for tiny demos where startup-time injection is acceptable.

## Suggested Module Layout

```text
src/mini_claude/dataagent/
  __init__.py
  catalog.py          # load and validate catalog YAML files
  config.py           # database and safety settings
  sql_safety.py       # parse, classify, allowlist, limit enforcement
  postgres.py         # read-only PostgreSQL execution
  artifacts.py        # save query results and chart outputs
  tools.py            # get_data_catalog and execute_sql tool definitions/handlers

.claude/data_catalog/
  sales.yaml          # example domain catalog

.claude/agents/
  plot.md             # plotting sub-agent system prompt
```

The existing `tools.py` should import and register DataAgent tool definitions and dispatch handlers without mixing SQL-specific code into the generic tool implementation.

## Configuration

Use environment variables for secrets:

```text
DATAAGENT_PG_DSN=postgresql://readonly_user:password@localhost:5432/dbname
```

Use non-secret project config for behavior:

```yaml
catalog_dir: .claude/data_catalog
default_row_limit: 200
max_row_limit: 1000
statement_timeout_ms: 10000
lock_timeout_ms: 1000
artifact_dir: .mini-claude/dataagent/artifacts
```

If a DSN includes credentials, it must not be committed.

## Error Handling

Backend errors should be safe and actionable:

- Unknown table: name the rejected table and suggest calling `get_data_catalog`.
- Unknown column: name the rejected column and table if known.
- Unsafe SQL: state that only read-only single-statement SQL is allowed.
- Timeout: explain the query exceeded the timeout and suggest narrowing filters.
- Too many rows: explain the row cap and suggest aggregation or filtering.

Errors should not leak credentials or raw connection strings.

## Testing Strategy

Unit tests:

- Catalog loader accepts valid domain files.
- Catalog loader rejects duplicate domain names and malformed table definitions.
- SQL safety rejects write statements.
- SQL safety rejects multi-statement input.
- SQL safety rejects out-of-catalog tables and columns.
- SQL safety enforces or injects limit.
- SQL safety accepts normal `SELECT` and `WITH ... SELECT` queries.

Integration tests:

- Use a local or containerized PostgreSQL test database.
- Verify the read-only role cannot write even if application validation misses something.
- Verify `execute_sql` returns structured result artifacts.
- Verify timeout and row cap behavior.

Agent behavior tests:

- Given a question and catalog, the agent calls `get_data_catalog` before `execute_sql`.
- Given a chart-worthy result, the agent can call `agent(type="plot")`.
- Plot sub-agent does not receive database access.

## Milestones

1. Add catalog registry and one example catalog.
2. Add DataAgent configuration loader.
3. Add SQL safety validator.
4. Add PostgreSQL read-only executor.
5. Register `get_data_catalog` and `execute_sql`.
6. Add DataAgent prompt section with catalog index.
7. Add `plot` custom sub-agent prompt.
8. Adjust `agent` tool schema for custom agent names.
9. Add focused tests for catalog, SQL safety, and tool execution.

## Open Decisions

- Which SQL parser library to use for PostgreSQL-aware parsing.
- Exact catalog config path if `.claude/data_catalog` should not be used.
- Whether first plotting version uses a generic `run_shell` Python script or a dedicated plotting tool.
- Whether result artifacts are JSON, CSV, or both.
