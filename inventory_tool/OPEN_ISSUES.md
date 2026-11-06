# Open Issues / Design Decisions

Running log of problems and design decisions raised during development, so
nothing gets lost between sessions just because it wasn't brought up again
right away. Add a new entry whenever a real concern or decision comes up -
resolve by moving the entry to "Resolved" with a short note on what was
decided/built, not by deleting it.

## Open

### 1. Sub-assembly-scoped outsourcing ("Auslagerung") needs to account for shared materials

A material can appear in multiple BOM branches/sub-assemblies (e.g. an
insulator needed 2x in the "inner part" and 2x elsewhere in the same
prototype - 4x total). Outsourcing happens per sub-assembly, into one box
per sub-assembly (organizational requirement from the user's management) -
so the quantity pulled for a given sub-assembly request must be scoped to
that sub-assembly's own need (e.g. 2), not the globally aggregated total
across the whole BOM (e.g. 4). Pulling the global total into one box would
put material meant for a different sub-assembly/box in the wrong place.

What's still needed: a sub-assembly-scoped request view (similar to the
colleague's `WarehouseRequest` function - pick a sub-assembly material
number + a unit count for that request, compute demand only over that
branch of the BOM), PLUS an informational hint on each line showing "this
material is also needed elsewhere in the full BOM, total X" so the user
doesn't have to manually remember/track that for a later, separate request.

Additional requirement (confirms the direction above, adds detail): the
outsourcing email itself only ever needs 4 fields - material number,
description, quantity, storage location. Today the user has to manually
hide columns and copy just that slice out of a much wider report table
every time, which is repetitive busywork. The request view should
generate exactly that minimal set directly, scoped to the chosen
sub-assembly, so no manual column-hiding/copying is needed.

Preferred flow (matches the colleague's existing two-step design - worth
keeping rather than collapsing to one click): (1) generate/stage the
minimal-fields request as a reviewable table in Excel first (easier to
check "is everything right" in a table than inside an email body), (2) a
separate, explicit action turns the reviewed staged table into the actual
email draft. Not a one-click "pick sub-assembly -> email sent" flow.

### 2. Persistent working file - manual comments/status must survive refreshes

Currently every report run creates a brand new Excel file, so any manual
annotations (status overrides, owner, comments) are lost on the next run.
The user wants one working file that updates in place, where edits made
directly in the sheet (comments, manual status) are preserved across
refreshes - matching the "Status" sheet persistence pattern already proven
in the colleague's VBA tool (merge by material/part number, keep columns
the user edited, before the sheet is rebuilt).

### 3. Design principle: live automation must never replace the manual file-import fallback

Confirmed by a real incident: Windchill was down for several days, and the
colleague's tool - which only offers a live "fetch from Windchill" button
with no way to load a previously saved BOM export instead - became
completely unusable during that time. Our tool's current file-dialog-based
import (BOM/SAP/order list chosen from disk, regardless of where the file
came from) has no such hard dependency.

Decision: when live automation (Windchill/SAP fetch buttons) is added
later, it must only ADD a faster path on top of the manual file import,
never replace or require it. The manual import must always keep working
on its own.

## Resolved

(none yet)
