"""hive_state — the single state layer for .company/ JSON.

Every read or write of .company/ JSON in the runtime goes through this
package. It owns:

  * per-file locking (flock on a dedicated .locks/ sidecar),
  * one atomic-write convention (tmp + os.replace, single tmp name),
  * schema validation on write (a violating write fails loudly and
    leaves the file untouched),
  * the typed accessors the ticket scripts, the sprint lifecycle, the
    MCP tools, and the CLI all share (ticket_create/move/approve/...,
    round_append, ...).

Bash scripts do not carry JSON mutation of their own: they call this
module's CLI (``python -m hive_state <op> ...``, see ``__main__``) or
import the accessors directly.
"""

from hive_state.core import (  # noqa: F401
    CompanyError,
    SchemaViolation,
    apply_jq,
    atomic_mkdirs,
    atomic_remove,
    atomic_rename,
    atomic_write,
    company_dir,
    jsonl_append,
    read_json,
)
from hive_state.rounds import (  # noqa: F401
    round_append,
    round_archive,
    round_close,
    round_get,
)
from hive_state.tickets import (  # noqa: F401
    MoveDenied,
    NotFound,
    find_ticket,
    ticket_approve,
    ticket_archive,
    ticket_bounce,
    ticket_claim,
    ticket_comment,
    ticket_create,
    ticket_get,
    ticket_move,
    ticket_rollback_conflict,
)
