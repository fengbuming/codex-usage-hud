"""Disjoint resident-cache partitions for the renderer main process.

These budgets do not include interpreter/native allocations or claim a total
process ceiling. Desktop bubble owners are deliberately outside this policy.
"""

MIB = 1024 * 1024
SELECTED_HISTORY_BYTES = 2 * MIB
USAGE_TAIL_BYTES = 4 * MIB
SEARCH_CANDIDATE_BYTES = 4 * MIB
SEARCH_EXTRACTION_BYTES = 2 * MIB
