Here you go:

---

## 🗺️ What I Learned Building with Coral

I came into this hackathon having never used Coral before. Here's what I actually had to figure out:

**The YAML spec format took the most time.** Getting the `backend: file` + JSONL format combination right wasn't obvious from the docs alone. I had to understand that Coral doesn't read CSV directly. It expects JSONL, which meant writing `convert_mock_to_jsonl.py` to transform my mock data into the right format before Coral could even see it.

**The `coral sql` verb, not `coral query`.** Small thing, but it tripped me up early. The CLI syntax changed in v0.4.1 and some older examples online use the wrong verb.

**Cross-source JOINs require matching column names across your source specs.** My first JOIN failed silently because `discord.messages_summary` didn't match the registered table name `discord.messages`. The error wasn't obvious. It just returned zero rows. I learned to always verify table names with `coral source list` before writing JOIN queries.

**MCP integration is powerful but needs patience.** Wiring `coral mcp start` alongside the FastAPI backend required understanding process lifecycle. Coral runs as a separate process that your backend talks to.

The biggest insight: Coral's value isn't just convenience. It's that your agent never has to touch raw API responses. The data resolves inside Coral, clean and structured, before your LLM ever sees it. That changes how you think about agent architecture entirely.
