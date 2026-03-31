"""
Seven-mode failure taxonomy for evaluation runs.

The taxonomy maps each run to one mode using a strict priority order.
The modes are mutually exclusive and are intended for diagnostic analysis
of retrieval, edit quality, and execution outcomes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class FailureMode(str, Enum):
                                                    
    COMPLETE_HALLUCINATION    = "complete_hallucination"
    CONTEXT_INSUFFICIENT      = "context_insufficient"
    WRONG_FILES_TARGETED      = "wrong_files_targeted"
    PARTIAL_FIX               = "partial_fix"
    SHALLOW_FIX               = "shallow_fix"
    RETRIEVAL_SUCCESS_FAIL    = "retrieval_success_task_failure"
    INEFFICIENT_PROGRESS      = "inefficient_progress"
    SUCCESS                   = "success"


@dataclass(frozen=True)
class FailureModeInfo:
    mode: FailureMode
    description: str
    diagnostic_signal: str                             
    remediation_direction: str                                     
    severity: int                                                   


FAILURE_MODE_REGISTRY: dict[FailureMode, FailureModeInfo] = {
    FailureMode.COMPLETE_HALLUCINATION: FailureModeInfo(
        mode=FailureMode.COMPLETE_HALLUCINATION,
        description=(
            "The agent produced changes that are unrelated to the task. "
            "It neither read the required context nor modified the expected "
            "files. The output suggests the agent misunderstood the prompt "
            "or was unable to ground its reasoning in the provided codebase."
        ),
        diagnostic_signal=(
            "No required context files read, no expected changed files modified, "
            "result text contains plausible-sounding but irrelevant content."
        ),
        remediation_direction=(
            "Task prompt grounding; agent may need stronger task description "
            "or initial file listing to orient its search."
        ),
        severity=1,
    ),

    FailureMode.CONTEXT_INSUFFICIENT: FailureModeInfo(
        mode=FailureMode.CONTEXT_INSUFFICIENT,
        description=(
            "The agent produced changes but never read one or more required "
            "context files. Its fix is based on an incomplete understanding "
            "of the system — it addresses the surface symptom without "
            "understanding the architectural constraint that determines the "
            "correct location and form of the fix."
        ),
        diagnostic_signal=(
            "CCS < 1.0, retrievalSuccess=false, agent made modifications "
            "that are syntactically plausible but semantically incorrect "
            "relative to the constraint defined in the missing context."
        ),
        remediation_direction=(
            "Context retrieval strategy; agent's search scope may be too "
            "narrow, or it may not be following import chains far enough "
            "from the entry point of its investigation."
        ),
        severity=2,
    ),

    FailureMode.WRONG_FILES_TARGETED: FailureModeInfo(
        mode=FailureMode.WRONG_FILES_TARGETED,
        description=(
            "The agent modified files that are not in the expected change set, "
            "while leaving expected files unmodified. It identified an incorrect "
            "location for the fix — typically because it stopped its investigation "
            "at an intermediate file that exhibits the symptom rather than "
            "continuing to the root cause."
        ),
        diagnostic_signal=(
            "unexpectedWrites is non-empty, agentModifiedAny=true but "
            "allFilesModified=false, files modified are semantically adjacent "
            "to but not the same as expected changed files."
        ),
        remediation_direction=(
            "Root cause attribution; agent may need to distinguish between "
            "the file that exhibits the bug and the file that contains the "
            "authoritative fix location."
        ),
        severity=3,
    ),

    FailureMode.PARTIAL_FIX: FailureModeInfo(
        mode=FailureMode.PARTIAL_FIX,
        description=(
            "The agent correctly identified some but not all files that require "
            "changes. For tasks requiring coordinated multi-file edits, a partial "
            "fix is equivalent to no fix — the system is in an inconsistent state "
            "where some components have been updated and others have not."
        ),
        diagnostic_signal=(
            "allFilesModified=false, modifiedFileCount < changedFiles.length, "
            "modified files show correct changes but unmodified files still "
            "contain the pre-fix code."
        ),
        remediation_direction=(
            "Multi-file coordination; agent may need to search more broadly "
            "for call sites and dependent modules after identifying the "
            "primary fix location."
        ),
        severity=3,
    ),

    FailureMode.SHALLOW_FIX: FailureModeInfo(
        mode=FailureMode.SHALLOW_FIX,
        description=(
            "The agent modified the expected files but with changes too minimal "
            "to represent a genuine fix. This indicates the agent found the right "
            "location but either did not understand what change was needed or "
            "abandoned the fix prematurely. Distinguishable from partial fix "
            "by the presence of modifications that are present but trivially small."
        ),
        diagnostic_signal=(
            "hasTrivialWrites=true, deltaChars < MIN_DELTA_CHARS for all "
            "modified files. Agent made token writes (e.g., adding a comment, "
            "changing a variable name) without implementing the actual fix."
        ),
        remediation_direction=(
            "Solution depth; agent correctly locates the fix but cannot "
            "generate a complete implementation. May indicate inability to "
            "synthesise the constraint from required context into code."
        ),
        severity=4,
    ),

    FailureMode.RETRIEVAL_SUCCESS_FAIL: FailureModeInfo(
        mode=FailureMode.RETRIEVAL_SUCCESS_FAIL,
        description=(
            "The agent read all required context files and modified all expected "
            "files with substantive changes, but the implementation is incorrect. "
            "The required constraint symbols are absent from the written code, "
            "indicating the agent read the context but did not correctly apply "
            "the constraints it discovered. This is the most informationally "
            "rich failure mode — it isolates a reasoning failure from a "
            "retrieval failure."
        ),
        diagnostic_signal=(
            "retrievalSuccess=true, allFilesModified=true, hasTrivialWrites=false, "
            "symbolsPass=false. Agent read globals.py, understood there is a proxy, "
            "but still placed the fix in the wrong location."
        ),
        remediation_direction=(
            "Reasoning quality; agent discovers the context but cannot correctly "
            "synthesise it into implementation decisions. Suggests a gap between "
            "comprehension and generation at the architectural level."
        ),
        severity=5,
    ),

    FailureMode.INEFFICIENT_PROGRESS: FailureModeInfo(
        mode=FailureMode.INEFFICIENT_PROGRESS,
        description=(
            "The agent eventually produced a correct solution but took significantly "
            "more tool calls than necessary. This is a success variant — the task "
            "passed — but the inefficiency is logged because it signals planning "
            "weakness and has practical cost implications for deployment."
        ),
        diagnostic_signal=(
            "taskSuccess=true, toolCallCount > 40, PES < 0.4. Agent read files "
            "multiple times, made intermediate edits that were later revised, "
            "or explored dead-end paths before finding the correct one."
        ),
        remediation_direction=(
            "Planning and search strategy; agent should form a more directed "
            "investigation plan before beginning tool use rather than "
            "exploring breadth-first."
        ),
        severity=6,
    ),

    FailureMode.SUCCESS: FailureModeInfo(
        mode=FailureMode.SUCCESS,
        description="All gates passed. Task completed correctly.",
        diagnostic_signal="taskSuccess=true",
        remediation_direction="N/A",
        severity=7,
    ),
}


def classify_failure_mode(log_entry: dict[str, Any]) -> FailureModeInfo:
    """
    Classify a single eval log entry into one of the seven failure modes.

    The classification applies strict priority ordering to ensure each run
    maps to exactly one mode. The order progresses from most fundamental
    failure (hallucination) to least severe (inefficiency).
    """
    retrieval_success  = log_entry.get("retrievalSuccess", False)
    agent_modified_any = log_entry.get("agentModifiedAny", False)
    all_files_modified = log_entry.get("allFilesModified", False)
    has_trivial_writes = log_entry.get("hasTrivialWrites", False)
    unexpected_writes  = log_entry.get("unexpectedWrites", [])
    symbols_pass       = not log_entry.get("symbolResults") or all(
        r.get("found", False) or r.get("inconclusive", False)
        for r in log_entry.get("symbolResults", [])
    )
    content_verifiable = log_entry.get("contentVerifiable", True)
    tool_count         = log_entry.get("toolCallCount", 0)
    task_success       = log_entry.get("taskSuccess", False)

                                                                     
    if agent_modified_any and unexpected_writes and not all_files_modified:
        return FAILURE_MODE_REGISTRY[FailureMode.WRONG_FILES_TARGETED]

                                                          
    if not retrieval_success and not agent_modified_any:
        return FAILURE_MODE_REGISTRY[FailureMode.COMPLETE_HALLUCINATION]

                                   
    if not retrieval_success:
        return FAILURE_MODE_REGISTRY[FailureMode.CONTEXT_INSUFFICIENT]

                                              
    if not agent_modified_any:
        return FAILURE_MODE_REGISTRY[FailureMode.SHALLOW_FIX]

                                                    
    if has_trivial_writes:
        return FAILURE_MODE_REGISTRY[FailureMode.SHALLOW_FIX]

                                                                          
    if not all_files_modified:
        return FAILURE_MODE_REGISTRY[FailureMode.PARTIAL_FIX]

                                                        
    if unexpected_writes and not task_success:
        return FAILURE_MODE_REGISTRY[FailureMode.WRONG_FILES_TARGETED]

                                                                              
    if not symbols_pass and content_verifiable:
        return FAILURE_MODE_REGISTRY[FailureMode.RETRIEVAL_SUCCESS_FAIL]

                                          
    if task_success and tool_count > 40:
        return FAILURE_MODE_REGISTRY[FailureMode.INEFFICIENT_PROGRESS]

              
    if task_success:
        return FAILURE_MODE_REGISTRY[FailureMode.SUCCESS]

                                                             
    return FAILURE_MODE_REGISTRY[FailureMode.CONTEXT_INSUFFICIENT]


def format_failure_report(log_entries: list[dict]) -> str:
    """
    Format a human-readable failure mode distribution report.
    """
    from collections import Counter
    modes = [classify_failure_mode(e) for e in log_entries]
    counts = Counter(m.mode for m in modes)
    n = len(log_entries)

    lines = ["Failure Mode Distribution", "=" * 40]
    for mode in FailureMode:
        count = counts.get(mode, 0)
        pct   = 100 * count / max(1, n)
        info  = FAILURE_MODE_REGISTRY[mode]
        lines.append(f"  {mode.value:<35} {count:3d} ({pct:5.1f}%)")
        if count > 0 and mode != FailureMode.SUCCESS:
            lines.append(f"    → {info.remediation_direction}")
    lines.append(f"\n  Total: {n}")
    return "\n".join(lines)
