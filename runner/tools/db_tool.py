from __future__ import annotations

import os
import re


_ALLOWED_TABLES_ENV = os.environ.get("DB_ALLOWED_TABLES", "")


async def run_db_query(query: str) -> str:
    """Chạy SELECT query (read-only). Chỉ cho phép SELECT trên whitelist tables."""
    q = query.strip().upper()

    if not q.startswith("SELECT"):
        return "[blocked: only SELECT queries allowed]"

    allowed = [t.strip() for t in _ALLOWED_TABLES_ENV.split(",") if t.strip()]
    if allowed:
        tables_in_query = re.findall(r'\bFROM\s+(\w+)', q)
        for t in tables_in_query:
            if t.lower() not in [a.lower() for a in allowed]:
                return f"[blocked: table '{t}' not in allowed list]"

    try:
        import asyncpg
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        rows = await conn.fetch(query)
        await conn.close()
        if not rows:
            return "[no rows returned]"
        # Format đơn giản
        headers = list(rows[0].keys())
        lines = [" | ".join(headers)]
        lines += [" | ".join(str(v) for v in row.values()) for row in rows[:50]]
        if len(rows) > 50:
            lines.append(f"... (+{len(rows)-50} rows)")
        return "\n".join(lines)
    except ImportError:
        return "[error: asyncpg not installed]"
    except Exception as e:
        return f"[error: {e}]"
