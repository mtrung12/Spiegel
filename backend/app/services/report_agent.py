"""
Report agent service.
Generates the simulation report in a ReACT loop, built on LangChain + Zep.

What it does:
1. Writes the report from the simulation requirement and the Zep graph
2. Plans the outline first, then generates section by section
3. Runs a multi-round ReACT reason-and-reflect loop per section
4. Supports chatting with the user, calling retrieval tools as needed
"""

import os
import json
import time
import re
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..config import Config
from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger
from ..utils.locale import get_language_instruction, t
from .zep_tools import (
    ZepToolsService, 
    SearchResult, 
    InsightForgeResult, 
    PanoramaResult,
    InterviewResult
)

logger = get_logger('mirofish.report_agent')


class ReportLogger:
    """
    Detailed logger for the report agent.

    Writes agent_log.jsonl into the report folder, one line per action. Each
    line is a complete JSON object carrying the timestamp, the action type and
    the full detail.
"""
    
    def __init__(self, report_id: str):
        """
        Initialise the logger.

        Args:
            report_id: Report ID, which determines the log file path
"""
        self.report_id = report_id
        self.log_file_path = os.path.join(
            Config.UPLOAD_FOLDER, 'reports', report_id, 'agent_log.jsonl'
        )
        self.start_time = datetime.now()
        self._ensure_log_file()
    
    def _ensure_log_file(self):
        """Make sure the log file's directory exists."""
        log_dir = os.path.dirname(self.log_file_path)
        os.makedirs(log_dir, exist_ok=True)
    
    def _get_elapsed_time(self) -> float:
        """Return the elapsed time since start, in seconds."""
        return (datetime.now() - self.start_time).total_seconds()
    
    def log(
        self, 
        action: str, 
        stage: str,
        details: Dict[str, Any],
        section_title: str = None,
        section_index: int = None
    ):
        """
        Write one log record.

        Args:
            action: Action type, e.g. 'start', 'tool_call', 'llm_response', 'section_complete'
            stage: Current stage, e.g. 'planning', 'generating', 'completed'
            details: Full detail dict, untruncated
            section_title: Current section title (optional)
            section_index: Current section index (optional)
"""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "elapsed_seconds": round(self._get_elapsed_time(), 2),
            "report_id": self.report_id,
            "action": action,
            "stage": stage,
            "section_title": section_title,
            "section_index": section_index,
            "details": details
        }
        
        # Append to the JSONL file
        with open(self.log_file_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
    
    def log_start(self, simulation_id: str, graph_id: str, simulation_requirement: str):
        """Log the start of report generation."""
        self.log(
            action="report_start",
            stage="pending",
            details={
                "simulation_id": simulation_id,
                "graph_id": graph_id,
                "simulation_requirement": simulation_requirement,
                "message": t('report.taskStarted')
            }
        )
    
    def log_planning_start(self):
        """Log the start of outline planning."""
        self.log(
            action="planning_start",
            stage="planning",
            details={"message": t('report.planningStart')}
        )
    
    def log_planning_context(self, context: Dict[str, Any]):
        """Log the context gathered during planning."""
        self.log(
            action="planning_context",
            stage="planning",
            details={
                "message": t('report.fetchSimContext'),
                "context": context
            }
        )
    
    def log_planning_complete(self, outline_dict: Dict[str, Any]):
        """Log the completed outline."""
        self.log(
            action="planning_complete",
            stage="planning",
            details={
                "message": t('report.planningComplete'),
                "outline": outline_dict
            }
        )
    
    def log_section_start(self, section_title: str, section_index: int):
        """Log the start of a section."""
        self.log(
            action="section_start",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={"message": t('report.sectionStart', title=section_title)}
        )
    
    def log_react_thought(self, section_title: str, section_index: int, iteration: int, thought: str):
        """Log a ReACT reasoning step."""
        self.log(
            action="react_thought",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "iteration": iteration,
                "thought": thought,
                "message": t('report.reactThought', iteration=iteration)
            }
        )
    
    def log_tool_call(
        self, 
        section_title: str, 
        section_index: int,
        tool_name: str, 
        parameters: Dict[str, Any],
        iteration: int
    ):
        """Log a tool call."""
        self.log(
            action="tool_call",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "iteration": iteration,
                "tool_name": tool_name,
                "parameters": parameters,
                "message": t('report.toolCall', toolName=tool_name)
            }
        )
    
    def log_tool_result(
        self,
        section_title: str,
        section_index: int,
        tool_name: str,
        result: str,
        iteration: int
    ):
        """Log a tool result, in full and untruncated."""
        self.log(
            action="tool_result",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "iteration": iteration,
                "tool_name": tool_name,
                "result": result,  # Full result, untruncated
                "result_length": len(result),
                "message": t('report.toolResult', toolName=tool_name)
            }
        )
    
    def log_llm_response(
        self,
        section_title: str,
        section_index: int,
        response: str,
        iteration: int,
        has_tool_calls: bool,
        has_final_answer: bool
    ):
        """Log an LLM response, in full and untruncated."""
        self.log(
            action="llm_response",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "iteration": iteration,
                "response": response,  # Full response, untruncated
                "response_length": len(response),
                "has_tool_calls": has_tool_calls,
                "has_final_answer": has_final_answer,
                "message": t('report.llmResponse', hasToolCalls=has_tool_calls, hasFinalAnswer=has_final_answer)
            }
        )
    
    def log_section_content(
        self,
        section_title: str,
        section_index: int,
        content: str,
        tool_calls_count: int
    ):
        """Log that the section body was produced (not that the section is finished)."""
        self.log(
            action="section_content",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "content": content,  # Full content, untruncated
                "content_length": len(content),
                "tool_calls_count": tool_calls_count,
                "message": t('report.sectionContentDone', title=section_title)
            }
        )
    
    def log_section_full_complete(
        self,
        section_title: str,
        section_index: int,
        full_content: str
    ):
        """
        Log that a section is complete.

        The frontend watches for this record to know a section really finished and
        to pick up its full content.
        """
        self.log(
            action="section_complete",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "content": full_content,
                "content_length": len(full_content),
                "message": t('report.sectionComplete', title=section_title)
            }
        )
    
    def log_report_complete(self, total_sections: int, total_time_seconds: float):
        """Log that the report is complete."""
        self.log(
            action="report_complete",
            stage="completed",
            details={
                "total_sections": total_sections,
                "total_time_seconds": round(total_time_seconds, 2),
                "message": t('report.reportComplete')
            }
        )
    
    def log_error(self, error_message: str, stage: str, section_title: str = None):
        """Log an error."""
        self.log(
            action="error",
            stage=stage,
            section_title=section_title,
            section_index=None,
            details={
                "error": error_message,
                "message": t('report.errorOccurred', error=error_message)
            }
        )


class ReportConsoleLogger:
    """
    Console logger for the report agent.

    Writes console-style records (INFO, WARNING, ...) into console_log.txt in the
    report folder. Unlike agent_log.jsonl, this is plain-text console output.
"""
    
    def __init__(self, report_id: str):
        """
        Initialise the console logger.

        Args:
            report_id: Report ID, which determines the log file path
"""
        self.report_id = report_id
        self.log_file_path = os.path.join(
            Config.UPLOAD_FOLDER, 'reports', report_id, 'console_log.txt'
        )
        self._ensure_log_file()
        self._file_handler = None
        self._setup_file_handler()
    
    def _ensure_log_file(self):
        """Make sure the log file's directory exists."""
        log_dir = os.path.dirname(self.log_file_path)
        os.makedirs(log_dir, exist_ok=True)
    
    def _setup_file_handler(self):
        """Attach a file handler so log records also land in the file."""
        import logging
        
        # Create the file handler
        self._file_handler = logging.FileHandler(
            self.log_file_path,
            mode='a',
            encoding='utf-8'
        )
        self._file_handler.setLevel(logging.INFO)
        
        # Same concise format as the console
        formatter = logging.Formatter(
            '[%(asctime)s] %(levelname)s: %(message)s',
            datefmt='%H:%M:%S'
        )
        self._file_handler.setFormatter(formatter)
        
        # Attach to the report_agent loggers
        loggers_to_attach = [
            'mirofish.report_agent',
            'mirofish.zep_tools',
        ]
        
        for logger_name in loggers_to_attach:
            target_logger = logging.getLogger(logger_name)
            # Do not attach twice
            if self._file_handler not in target_logger.handlers:
                target_logger.addHandler(self._file_handler)
    
    def close(self):
        """Close the file handler and detach it from the loggers."""
        import logging
        
        if self._file_handler:
            loggers_to_detach = [
                'mirofish.report_agent',
                'mirofish.zep_tools',
            ]
            
            for logger_name in loggers_to_detach:
                target_logger = logging.getLogger(logger_name)
                if self._file_handler in target_logger.handlers:
                    target_logger.removeHandler(self._file_handler)
            
            self._file_handler.close()
            self._file_handler = None
    
    def __del__(self):
        """Close the file handler on teardown."""
        self.close()


class ReportStatus(str, Enum):
    """Report status."""
    PENDING = "pending"
    PLANNING = "planning"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ReportSection:
    """A report section."""
    title: str
    content: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "content": self.content
        }

    def to_markdown(self, level: int = 2) -> str:
        """Render as Markdown."""
        md = f"{'#' * level} {self.title}\n\n"
        if self.content:
            md += f"{self.content}\n\n"
        return md


@dataclass
class ReportOutline:
    """Report outline."""
    title: str
    summary: str
    sections: List[ReportSection]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "summary": self.summary,
            "sections": [s.to_dict() for s in self.sections]
        }
    
    def to_markdown(self) -> str:
        """Render as Markdown."""
        md = f"# {self.title}\n\n"
        md += f"> {self.summary}\n\n"
        for section in self.sections:
            md += section.to_markdown()
        return md


@dataclass
class Report:
    """The complete report."""
    report_id: str
    simulation_id: str
    graph_id: str
    simulation_requirement: str
    status: ReportStatus
    outline: Optional[ReportOutline] = None
    markdown_content: str = ""
    created_at: str = ""
    completed_at: str = ""
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "report_id": self.report_id,
            "simulation_id": self.simulation_id,
            "graph_id": self.graph_id,
            "simulation_requirement": self.simulation_requirement,
            "status": self.status.value,
            "outline": self.outline.to_dict() if self.outline else None,
            "markdown_content": self.markdown_content,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "error": self.error
        }


# ═══════════════════════════════════════════════════════════════
# Prompt template constants
# ═══════════════════════════════════════════════════════════════

# -- Tool descriptions --

TOOL_DESC_INSIGHT_FORGE = """\
[Deep insight retrieval - the most powerful search tool]
This is our most capable retrieval function, built for deep analysis. It will:
1. Break your question into sub-questions automatically
2. Search the simulation graph along several dimensions
3. Combine semantic search, entity analysis and relationship-chain tracing
4. Return the broadest, deepest material available

[When to use it]
- You need to analyse a topic in depth
- You need several angles on an event
- You need rich material to support a report section

[What it returns]
- Verbatim relevant facts, ready to quote
- Insight into the core entities
- Relationship-chain analysis"""

TOOL_DESC_PANORAMA_SEARCH = """\
[Breadth search - the panoramic view]
Use this to get the complete picture of the simulation result. It is the best
fit for understanding how an event evolved. It will:
1. Fetch every relevant node and relationship
2. Separate currently valid facts from historical/expired ones
3. Show you how public opinion shifted over time

[When to use it]
- You need the full arc of an event
- You need to compare public opinion across phases
- You need comprehensive entity and relationship information

[What it returns]
- Currently valid facts (the latest simulation state)
- Historical/expired facts (the record of how it changed)
- Every entity involved"""

TOOL_DESC_QUICK_SEARCH = """\
[Quick search - fast lookup]
A lightweight retrieval tool for simple, direct questions.

[When to use it]
- You need to look one specific thing up quickly
- You need to verify a single fact
- Straightforward information retrieval

[What it returns]
- The facts most relevant to your query"""

TOOL_DESC_INTERVIEW_AGENTS = """\
[In-depth interview - real agent interviews, across both platforms]
Calls the OASIS interview API to interview the agents actually running inside
the simulation. This is not an LLM impersonation: it hits the real interview
endpoint and returns the agents' own answers. By default it interviews on both
Twitter and Reddit at once, for a fuller range of views.

How it works:
1. Read the persona files to learn about every simulation agent
2. Pick the agents most relevant to the interview topic (students, media,
   officials, and so on)
3. Write the interview questions automatically
4. Call /api/simulation/interview/batch to run the real interview on both platforms
5. Combine the answers into a multi-perspective analysis

[When to use it]
- You need to know how different roles see the event (what do students think?
  what does the media say? what is the official line?)
- You need opinions and positions from several sides
- You need the simulation agents' own answers, straight from the OASIS environment
- You want the report to come alive with an interview transcript

[What it returns]
- Who each interviewee is
- Each agent's answers on Twitter and on Reddit
- Key quotes, ready to quote directly
- An interview summary and a comparison of the views

[Important] The OASIS simulation environment must be running for this to work."""

# -- Outline planning prompts --

PLAN_SYSTEM_PROMPT = """\
You write "future prediction reports" and you have a god's-eye view of the
simulated world: you can see every agent's behaviour, statements and interactions.

[Core idea]
We built a simulated world and injected a specific "simulation requirement" into
it as the variable. How that world then evolved is our prediction of what may
happen. What you are looking at is not "experimental data" - it is a rehearsal
of the future.

[Your task]
Write a future prediction report that answers:
1. Under the conditions we set, what happened next?
2. How did each kind of agent (each group of people) react and act?
3. What future trends and risks does this simulation reveal that deserve attention?

[What the report is]
- YES: a simulation-based prediction of the future, showing "if this, then what"
- YES: focused on the predicted outcome - how the event unfolds, how groups
  react, what emerges, what could go wrong
- YES: agent behaviour in the simulated world IS the prediction of how real
  people will behave
- NO: an analysis of the present-day real world
- NO: a vague general survey of public opinion

[Section count]
- At least 2 sections, at most 5
- No sub-sections; write each section as one complete piece
- Keep it tight and focused on the core predictive findings
- You design the section structure yourself, based on what the prediction shows

Output the report outline as JSON, in this shape:
{
    "title": "report title",
    "summary": "report summary (one sentence capturing the core predictive finding)",
    "sections": [
        {
            "title": "section title",
            "description": "what the section covers"
        }
    ]
}

Note: the sections array must hold at least 2 and at most 5 elements."""

PLAN_USER_PROMPT_TEMPLATE = """\
[Predicted scenario]
The variable we injected into the simulated world (the simulation requirement): {simulation_requirement}

[Scale of the simulated world]
- Entities taking part: {total_nodes}
- Relationships formed between them: {total_edges}
- Entity type distribution: {entity_types}
- Active agents: {total_entities}

[A sample of the future facts the simulation predicted]
{related_facts_json}

Take the god's-eye view of this rehearsal of the future:
1. Under the conditions we set, what state did the future end up in?
2. How did each group of people (each agent) react and act?
3. What future trends does this simulation reveal that deserve attention?

Design the section structure that best fits what the prediction shows.

[Reminder] Section count: at least 2, at most 5. Keep it tight and focused on
the core predictive findings."""

# -- Section generation prompts --

SECTION_SYSTEM_PROMPT_TEMPLATE = """\
You write "future prediction reports" and you are writing one section of one.

Report title: {report_title}
Report summary: {report_summary}
Predicted scenario (the simulation requirement): {simulation_requirement}

The section you are writing now: {section_title}

═══════════════════════════════════════════════════════════════
[Core idea]
═══════════════════════════════════════════════════════════════

The simulated world is a rehearsal of the future. We injected specific
conditions into it (the simulation requirement), and the behaviour and
interactions of the agents inside it ARE the prediction of how real people will
behave.

Your task is to:
- Show what happened next under the conditions we set
- Predict how each group of people (each agent) reacted and acted
- Surface the future trends, risks and opportunities worth attention

NO: do not write an analysis of the present-day real world
YES: focus on "what the future looks like" - the simulation result is the
     predicted future

═══════════════════════════════════════════════════════════════
[The rules that matter most - you must follow them]
═══════════════════════════════════════════════════════════════

1. [You must call tools to observe the simulated world]
   - You are observing a rehearsal of the future from a god's-eye view
   - Every statement must come from events and agent behaviour inside the
     simulated world
   - Do not write report content out of your own knowledge
   - Call tools at least 3 times per section (at most 5) to observe the
     simulated world, which stands in for the future

2. [You must quote the agents' own words and actions]
   - What an agent says and does is the prediction of how real people will behave
   - Present those predictions as quotes, for example:
     > "One group will say: ...verbatim content..."
   - Those quotes are the core evidence for the prediction

3. [Language consistency - quoted material must be translated into the report language]
   - Tool output may be phrased in a different language from the report
   - The whole report must be written in the language the user specified
   - When you quote tool output written in another language, translate it into
     the report language before writing it down
   - Keep the meaning intact and make the phrasing read naturally
   - This applies to body text and to quote blocks (> format) alike

4. [Present the prediction faithfully]
   - The report must reflect the simulation results that stand in for the future
   - Do not add information the simulation does not contain
   - Where the information is thin, say so plainly

5. [Never fabricate data]
   - NO: do not invent usernames, quotes, statistics or interaction counts
   - NO: do not include a <tool_result> block in your reply - only the system
     supplies tool results
   - YES: only cite entities, quotes and numbers that genuinely appear in the
     tool results
   - If the tool results contain nothing relevant, say so honestly rather than
     making something up

═══════════════════════════════════════════════════════════════
[Formatting rules - critically important]
═══════════════════════════════════════════════════════════════

[One section = the smallest unit of content]
- Each section is the smallest block the report is split into
- NO Markdown headings anywhere inside a section (#, ##, ###, ####)
- NO section heading at the top of your content
- The section title is added by the system; you write body text only
- Organise the content with **bold**, paragraph breaks, quotes and lists -
  never with headings

[Correct example]
```
This section analyses how public opinion spread. A close reading of the
simulation data shows that...

**The initial flashpoint**

Weibo was where the story broke, and carried the bulk of the first wave:

> "Weibo accounted for 68% of the initial volume..."

**The amplification phase**

Douyin pushed the story further:

- Strong visual impact
- High emotional resonance
```

[Incorrect example]
```
## Executive summary        <- wrong: do not add any heading
### 1. The initial phase    <- wrong: do not use ### for sub-sections
#### 1.1 Detailed analysis  <- wrong: do not use #### either

This section analyses...
```

═══════════════════════════════════════════════════════════════
[Available retrieval tools] (call 3-5 times per section)
═══════════════════════════════════════════════════════════════

{tools_description}

[Advice - mix the tools, do not lean on just one]
- insight_forge: deep analysis; decomposes the question and retrieves facts and
  relationships along several dimensions
- panorama_search: wide-angle view; the whole arc, the timeline, how things evolved
- quick_search: verify one specific point fast
- interview_agents: interview the simulation agents for first-person views and
  genuine reactions from different roles

═══════════════════════════════════════════════════════════════
[Workflow]
═══════════════════════════════════════════════════════════════

Each reply may do exactly one of the following, never both:

Option A - call a tool:
Write your reasoning, then call one tool in this format:
<tool_call>
{{"name": "tool name", "parameters": {{"parameter": "value"}}}}
</tool_call>
The system runs the tool and hands you the result. You must not write the tool
result yourself.

Option B - produce the final content:
Once the tools have given you enough, write the section starting with
"Final Answer:".

Strictly forbidden:
- A reply containing both a tool call and a Final Answer
- Writing your own tool result (Observation); every tool result is injected by
  the system
- More than one tool call per reply

═══════════════════════════════════════════════════════════════
[What the section must contain]
═══════════════════════════════════════════════════════════════

1. Content must rest on simulation data retrieved through the tools
2. Quote the source material generously to show what the simulation produced
3. Use Markdown, but no headings:
   - **Bold** for emphasis, in place of sub-headings
   - Lists (- or 1. 2. 3.) to organise points
   - Blank lines between paragraphs
   - NO #, ##, ###, #### or any other heading syntax
4. [Quote formatting - a quote must stand alone]
   A quote must be its own paragraph, with a blank line before and after. Never
   inline it inside a paragraph:

   Correct:
   ```
   The institution's response was seen as lacking substance.

   > "The institution's playbook looks rigid and slow against the pace of social media."

   That judgement reflects the general public frustration.
   ```

   Incorrect:
   ```
   The institution's response was seen as lacking substance. > "The playbook..." That judgement reflects...
   ```
5. Stay logically coherent with the other sections
6. [Avoid repetition] Read the completed sections below carefully and do not
   restate the same information
7. [Once more] Add no headings. Use **bold** in place of sub-headings."""

SECTION_USER_PROMPT_TEMPLATE = """\
Sections completed so far (read them carefully; do not repeat them):
{previous_content}

═══════════════════════════════════════════════════════════════
[Current task] Write the section: {section_title}
═══════════════════════════════════════════════════════════════

[Reminders]
1. Read the completed sections above carefully and do not restate them
2. Call a tool for simulation data before you start writing
3. Mix the tools; do not lean on just one
4. The content must come from the retrieval results, not from your own knowledge

[Formatting warning - must be followed]
- Do not write any heading (#, ##, ###, #### - none of them)
- Do not open with "{section_title}"
- The section title is added by the system
- Write body text directly, using **bold** in place of sub-headings

Begin:
1. First reason (Thought) about what this section needs
2. Then call a tool (Action) to get simulation data
3. Once you have enough, write the Final Answer (body text only, no headings)"""

# -- Message templates used inside the ReACT loop --

REACT_OBSERVATION_TEMPLATE = """\
Observation (retrieval result):

═══ Output of tool {tool_name} ═══
{result}

═══════════════════════════════════════════════════════════════
Tool calls so far: {tool_calls_count}/{max_tool_calls} (used: {used_tools_str}){unused_hint}
- If this is enough: write the section starting with "Final Answer:" and quote
  the material above
- If you need more: call one more tool
═══════════════════════════════════════════════════════════════"""

REACT_INSUFFICIENT_TOOLS_MSG = (
    "[Note] You have only made {tool_calls_count} tool call(s); at least "
    "{min_tool_calls} are required. Call another tool for more simulation data "
    "before writing the Final Answer.{unused_hint}"
)

REACT_INSUFFICIENT_TOOLS_MSG_ALT = (
    "You have made {tool_calls_count} tool call(s) so far; at least "
    "{min_tool_calls} are required. Call a tool to get simulation data.{unused_hint}"
)

REACT_TOOL_LIMIT_MSG = (
    "The tool call limit has been reached ({tool_calls_count}/{max_tool_calls}); no more calls are allowed. "
    'Write the section now from what you already have, starting with "Final Answer:".'
)

REACT_UNUSED_TOOLS_HINT = "\n💡 You have not used: {unused_list}. Try a different tool for another angle."

REACT_FORCE_FINAL_MSG = "The tool call limit has been reached. Write the section now, starting with Final Answer:."

# -- Chat prompt --

CHAT_SYSTEM_PROMPT_TEMPLATE = """\
You are a concise, efficient assistant for simulation predictions.

[Background]
Prediction conditions: {simulation_requirement}

[The analysis report already generated]
{report_content}

[Rules]
1. Answer from the report above wherever you can
2. Answer directly; skip long deliberation
3. Only call a tool when the report does not cover the question
4. Keep answers short, clear and well organised

[Available tools] (use only when needed, 1-2 calls at most)
{tools_description}

[Tool call format]
<tool_call>
{{"name": "tool name", "parameters": {{"parameter": "value"}}}}
</tool_call>

[Answer style]
- Short and direct; no essays
- Use > to quote key material
- Lead with the conclusion, then explain why"""

CHAT_OBSERVATION_SUFFIX = "\n\nAnswer the question concisely."


# ═══════════════════════════════════════════════════════════════
# ReportAgent - main class
# ═══════════════════════════════════════════════════════════════


class ReportAgent:
    """
    Report agent - generates the simulation report.

    Runs a ReACT (reasoning + acting) loop:
    1. Planning: read the simulation requirement and plan the report outline
    2. Generation: write section by section, calling tools as often as needed
    3. Reflection: check the content for completeness and accuracy
    """
    
    # Maximum tool calls per section
    MAX_TOOL_CALLS_PER_SECTION = 5
    
    # Maximum reflection rounds
    MAX_REFLECTION_ROUNDS = 3
    
    # Maximum tool calls during a chat
    MAX_TOOL_CALLS_PER_CHAT = 2
    
    def __init__(
        self, 
        graph_id: str,
        simulation_id: str,
        simulation_requirement: str,
        llm_client: Optional[LLMClient] = None,
        zep_tools: Optional[ZepToolsService] = None
    ):
        """
        Initialise the report agent.

        Args:
            graph_id: Graph ID
            simulation_id: Simulation ID
            simulation_requirement: Description of the simulation requirement
            llm_client: LLM client (optional)
            zep_tools: Zep tool service (optional)
"""
        self.graph_id = graph_id
        self.simulation_id = simulation_id
        self.simulation_requirement = simulation_requirement
        
        self.llm = llm_client or LLMClient()
        self.zep_tools = zep_tools or ZepToolsService()
        
        # Tool definitions
        self.tools = self._define_tools()
        
        # Structured logger, initialised in generate_report
        self.report_logger: Optional[ReportLogger] = None
        # Console logger, initialised in generate_report
        self.console_logger: Optional[ReportConsoleLogger] = None
        
        logger.info(t('report.agentInitDone', graphId=graph_id, simulationId=simulation_id))
    
    def _define_tools(self) -> Dict[str, Dict[str, Any]]:
        """Define the available tools."""
        return {
            "insight_forge": {
                "name": "insight_forge",
                "description": TOOL_DESC_INSIGHT_FORGE,
                "parameters": {
                    "query": "the question or topic you want to analyse in depth",
                    "report_context": "context for the current report section (optional; sharpens sub-question generation)"
                }
            },
            "panorama_search": {
                "name": "panorama_search",
                "description": TOOL_DESC_PANORAMA_SEARCH,
                "parameters": {
                    "query": "search query, used for relevance ranking",
                    "include_expired": "include expired/historical content (default True)"
                }
            },
            "quick_search": {
                "name": "quick_search",
                "description": TOOL_DESC_QUICK_SEARCH,
                "parameters": {
                    "query": "search query string",
                    "limit": "number of results (optional, default 10)"
                }
            },
            "interview_agents": {
                "name": "interview_agents",
                "description": TOOL_DESC_INTERVIEW_AGENTS,
                "parameters": {
                    "interview_topic": "the interview topic or brief, e.g. 'find out what students think about the dormitory formaldehyde incident'",
                    "max_agents": "maximum number of agents to interview (optional, default 5, max 10)"
                }
            }
        }
    
    def _execute_tool(self, tool_name: str, parameters: Dict[str, Any], report_context: str = "") -> str:
        """
        Run a tool call.

        Args:
            tool_name: Tool name
            parameters: Tool parameters
            report_context: Report context (used by InsightForge)

        Returns:
            The tool result, as text
"""
        logger.info(t('report.executingTool', toolName=tool_name, params=parameters))
        
        try:
            if tool_name == "insight_forge":
                query = parameters.get("query", "")
                ctx = parameters.get("report_context", "") or report_context
                result = self.zep_tools.insight_forge(
                    graph_id=self.graph_id,
                    query=query,
                    simulation_requirement=self.simulation_requirement,
                    report_context=ctx
                )
                return result.to_text()
            
            elif tool_name == "panorama_search":
                # Breadth search - the whole picture
                query = parameters.get("query", "")
                include_expired = parameters.get("include_expired", True)
                if isinstance(include_expired, str):
                    include_expired = include_expired.lower() in ['true', '1', 'yes']
                result = self.zep_tools.panorama_search(
                    graph_id=self.graph_id,
                    query=query,
                    include_expired=include_expired
                )
                return result.to_text()
            
            elif tool_name == "quick_search":
                # Quick search - fast lookup
                query = parameters.get("query", "")
                limit = parameters.get("limit", 10)
                if isinstance(limit, str):
                    limit = int(limit)
                result = self.zep_tools.quick_search(
                    graph_id=self.graph_id,
                    query=query,
                    limit=limit
                )
                return result.to_text()
            
            elif tool_name == "interview_agents":
                # In-depth interview - calls the real OASIS interview API for the
                # agents' own answers, across both platforms
                interview_topic = parameters.get("interview_topic", parameters.get("query", ""))
                max_agents = parameters.get("max_agents", 5)
                if isinstance(max_agents, str):
                    max_agents = int(max_agents)
                max_agents = min(max_agents, 10)
                result = self.zep_tools.interview_agents(
                    simulation_id=self.simulation_id,
                    interview_requirement=interview_topic,
                    simulation_requirement=self.simulation_requirement,
                    max_agents=max_agents
                )
                return result.to_text()
            
            # ========== Legacy tool names, redirected to the current tools ==========
            
            elif tool_name == "search_graph":
                # Redirect to quick_search
                logger.info(t('report.redirectToQuickSearch'))
                return self._execute_tool("quick_search", parameters, report_context)
            
            elif tool_name == "get_graph_statistics":
                result = self.zep_tools.get_graph_statistics(self.graph_id)
                return json.dumps(result, ensure_ascii=False, indent=2)
            
            elif tool_name == "get_entity_summary":
                entity_name = parameters.get("entity_name", "")
                result = self.zep_tools.get_entity_summary(
                    graph_id=self.graph_id,
                    entity_name=entity_name
                )
                return json.dumps(result, ensure_ascii=False, indent=2)
            
            elif tool_name == "get_simulation_context":
                # Redirect to insight_forge, which is the stronger tool
                logger.info(t('report.redirectToInsightForge'))
                query = parameters.get("query", self.simulation_requirement)
                return self._execute_tool("insight_forge", {"query": query}, report_context)
            
            elif tool_name == "get_entities_by_type":
                entity_type = parameters.get("entity_type", "")
                nodes = self.zep_tools.get_entities_by_type(
                    graph_id=self.graph_id,
                    entity_type=entity_type
                )
                result = [n.to_dict() for n in nodes]
                return json.dumps(result, ensure_ascii=False, indent=2)
            
            else:
                return f"Unknown tool: {tool_name}. Use one of: insight_forge, panorama_search, quick_search"
                
        except Exception as e:
            logger.error(t('report.toolExecFailed', toolName=tool_name, error=str(e)))
            return f"Tool call failed: {str(e)}"
    
    # Valid tool names, used to validate the bare-JSON fallback parse
    VALID_TOOL_NAMES = {"insight_forge", "panorama_search", "quick_search", "interview_agents"}

    def _parse_tool_calls(self, response: str) -> List[Dict[str, Any]]:
        """
        Parse a tool call out of an LLM response.

        Supported formats, in priority order:
        1. <tool_call>{"name": "tool_name", "parameters": {...}}</tool_call>
        2. Bare JSON (the whole response, or one line, is a tool-call JSON object)
        """
        tool_calls = []

        # Format 1: XML style, the standard form
        xml_pattern = r'<tool_call>\s*(\{.*?\})\s*</tool_call>'
        for match in re.finditer(xml_pattern, response, re.DOTALL):
            try:
                call_data = json.loads(match.group(1))
                tool_calls.append(call_data)
            except json.JSONDecodeError:
                pass

        if tool_calls:
            return tool_calls

        # Format 2: fallback for a model that emits bare JSON with no <tool_call>
        # wrapper. Only tried when format 1 missed, so JSON inside the body text
        # is not mistaken for a call.
        stripped = response.strip()
        if stripped.startswith('{') and stripped.endswith('}'):
            try:
                call_data = json.loads(stripped)
                if self._is_valid_tool_call(call_data):
                    tool_calls.append(call_data)
                    return tool_calls
            except json.JSONDecodeError:
                pass

        # The response may be reasoning text followed by bare JSON; take the last object
        json_pattern = r'(\{"(?:name|tool)"\s*:.*?\})\s*$'
        match = re.search(json_pattern, stripped, re.DOTALL)
        if match:
            try:
                call_data = json.loads(match.group(1))
                if self._is_valid_tool_call(call_data):
                    tool_calls.append(call_data)
            except json.JSONDecodeError:
                pass

        return tool_calls

    def _is_valid_tool_call(self, data: dict) -> bool:
        """Validate that the parsed JSON really is a tool call."""
        # Accept both {"name": ..., "parameters": ...} and {"tool": ..., "params": ...}
        tool_name = data.get("name") or data.get("tool")
        if tool_name and tool_name in self.VALID_TOOL_NAMES:
            # Normalise onto name / parameters
            if "tool" in data:
                data["name"] = data.pop("tool")
            if "params" in data and "parameters" not in data:
                data["parameters"] = data.pop("params")
            return True
        return False
    
    def _get_tools_description(self) -> str:
        """Render the tool descriptions."""
        desc_parts = ["Available tools:"]
        for name, tool in self.tools.items():
            params_desc = ", ".join([f"{k}: {v}" for k, v in tool["parameters"].items()])
            desc_parts.append(f"- {name}: {tool['description']}")
            if params_desc:
                desc_parts.append(f"  Parameters: {params_desc}")
        return "\n".join(desc_parts)

    @staticmethod
    def _strip_fake_tool_results(response: str) -> str:
        """Strip any <tool_result> blocks the LLM fabricated in its response.

        When the LLM generates a <tool_call> block and then continues to generate
        a <tool_result> block in the same response, we must strip the fake result
        before appending to message history. The real tool result will be injected
        separately by the system.
        """
        tag_pattern = re.compile(r'</?tool_result\b[^>]*>', flags=re.IGNORECASE)
        parts = []
        cursor = 0
        depth = 0

        for match in tag_pattern.finditer(response):
            if depth == 0:
                parts.append(response[cursor:match.start()])

            if match.group(0).lstrip().startswith('</'):
                depth = max(0, depth - 1)
            else:
                depth += 1
            cursor = match.end()

        if depth == 0:
            parts.append(response[cursor:])

        cleaned = ''.join(parts)
        # Treat a malformed opening tag without a closing `>` as unsafe too.
        cleaned = re.sub(r'<tool_result\b.*$', '', cleaned, flags=re.IGNORECASE | re.DOTALL)
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
        return cleaned.strip()

    def plan_outline(
        self, 
        progress_callback: Optional[Callable] = None
    ) -> ReportOutline:
        """
        Plan the report outline.

        Uses the LLM to read the simulation requirement and lay out the report.

        Args:
            progress_callback: Progress callback

        Returns:
            ReportOutline: the outline
"""
        logger.info(t('report.startPlanningOutline'))
        
        if progress_callback:
            progress_callback("planning", 0, t('progress.analyzingRequirements'))
        
        # Gather the simulation context first
        context = self.zep_tools.get_simulation_context(
            graph_id=self.graph_id,
            simulation_requirement=self.simulation_requirement
        )
        
        if progress_callback:
            progress_callback("planning", 30, t('progress.generatingOutline'))
        
        system_prompt = f"{PLAN_SYSTEM_PROMPT}\n\n{get_language_instruction()}"
        user_prompt = PLAN_USER_PROMPT_TEMPLATE.format(
            simulation_requirement=self.simulation_requirement,
            total_nodes=context.get('graph_statistics', {}).get('total_nodes', 0),
            total_edges=context.get('graph_statistics', {}).get('total_edges', 0),
            entity_types=list(context.get('graph_statistics', {}).get('entity_types', {}).keys()),
            total_entities=context.get('total_entities', 0),
            related_facts_json=json.dumps(context.get('related_facts', [])[:10], ensure_ascii=False, indent=2),
        )

        try:
            response = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3
            )
            
            if progress_callback:
                progress_callback("planning", 80, t('progress.parsingOutline'))
            
            # Parse the outline
            sections = []
            for section_data in response.get("sections", []):
                sections.append(ReportSection(
                    title=section_data.get("title", ""),
                    content=""
                ))
            
            outline = ReportOutline(
                title=response.get("title", "Simulation analysis report"),
                summary=response.get("summary", ""),
                sections=sections
            )
            
            if progress_callback:
                progress_callback("planning", 100, t('progress.outlinePlanComplete'))
            
            logger.info(t('report.outlinePlanDone', count=len(sections)))
            return outline
            
        except Exception as e:
            logger.error(t('report.outlinePlanFailed', error=str(e)))
            # Fall back to a default 3-section outline
            return ReportOutline(
                title="Future prediction report",
                summary="Future trends and risks, analysed from the simulation prediction",
                sections=[
                    ReportSection(title="Predicted scenario and core findings"),
                    ReportSection(title="Predicted behaviour of each group"),
                    ReportSection(title="Outlook and risks")
                ]
            )
    
    def _generate_section_react(
        self, 
        section: ReportSection,
        outline: ReportOutline,
        previous_sections: List[str],
        progress_callback: Optional[Callable] = None,
        section_index: int = 0
    ) -> str:
        """
        Generate one section with the ReACT loop.

        The loop:
        1. Thought - work out what information is needed
        2. Action - call a tool to get it
        3. Observation - read the tool result
        4. Repeat until there is enough, or the call limit is hit
        5. Final Answer - write the section

        Args:
            section: The section to generate
            outline: The full outline
            previous_sections: Content of the earlier sections, for coherence
            progress_callback: Progress callback
            section_index: Section index, used in the logs

        Returns:
            The section content, as Markdown
"""
        logger.info(t('report.reactGenerateSection', title=section.title))
        
        # Log the start of the section
        if self.report_logger:
            self.report_logger.log_section_start(section.title, section_index)
        
        system_prompt = SECTION_SYSTEM_PROMPT_TEMPLATE.format(
            report_title=outline.title,
            report_summary=outline.summary,
            simulation_requirement=self.simulation_requirement,
            section_title=section.title,
            tools_description=self._get_tools_description(),
        )
        system_prompt = f"{system_prompt}\n\n{get_language_instruction()}"

        # Build the user prompt - at most 4000 characters per completed section
        if previous_sections:
            previous_parts = []
            for sec in previous_sections:
                # 4000 characters per section at most
                truncated = sec[:4000] + "..." if len(sec) > 4000 else sec
                previous_parts.append(truncated)
            previous_content = "\n\n---\n\n".join(previous_parts)
        else:
            previous_content = "(this is the first section)"
        
        user_prompt = SECTION_USER_PROMPT_TEMPLATE.format(
            previous_content=previous_content,
            section_title=section.title,
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        # ReACT loop
        tool_calls_count = 0
        max_iterations = 5  # Maximum iterations
        min_tool_calls = 3  # Minimum tool calls
        conflict_retries = 0  # Consecutive replies containing both a tool call and a Final Answer
        used_tools = set()  # Tool names called so far
        all_tools = {"insight_forge", "panorama_search", "quick_search", "interview_agents"}

        # Report context, used by InsightForge to generate sub-questions
        report_context = f"Section title: {section.title}\nSimulation requirement: {self.simulation_requirement}"
        
        for iteration in range(max_iterations):
            if progress_callback:
                progress_callback(
                    "generating", 
                    int((iteration / max_iterations) * 100),
                    t('progress.deepSearchAndWrite', current=tool_calls_count, max=self.MAX_TOOL_CALLS_PER_SECTION)
                )
            
            # Call the LLM
            response = self.llm.chat(
                messages=messages,
                temperature=0.5,
                max_tokens=4096
            )

            # The LLM returned None (API error, or empty content)
            if response is None:
                logger.warning(t('report.sectionIterNone', title=section.title, iteration=iteration + 1))
                # Iterations left: push a message and retry
                if iteration < max_iterations - 1:
                    messages.append({"role": "assistant", "content": "(empty response)"})
                    messages.append({"role": "user", "content": "Continue writing the content."})
                    continue
                # Still None on the last iteration: break out and force a close
                break

            logger.debug(f"LLM response: {response[:200]}...")

            # Parse once and reuse
            tool_calls = self._parse_tool_calls(response)
            has_tool_calls = bool(tool_calls)
            has_final_answer = "Final Answer:" in response

            # -- Conflict: the reply held both a tool call and a Final Answer --
            if has_tool_calls and has_final_answer:
                conflict_retries += 1
                logger.warning(
                    t('report.sectionConflict', title=section.title, iteration=iteration+1, conflictCount=conflict_retries)
                )

                if conflict_retries <= 2:
                    # First two times: drop the reply and ask for a new one
                    cleaned_response = ReportAgent._strip_fake_tool_results(response)
                    messages.append({"role": "assistant", "content": cleaned_response})
                    messages.append({
                        "role": "user",
                        "content": (
                            "[Format error] Your reply contained both a tool call and a Final Answer, which is not allowed.\n"
                            "Each reply may do exactly one of the following:\n"
                            "- Call one tool (emit a single <tool_call> block and no Final Answer)\n"
                            "- Produce the final content (start with 'Final Answer:' and include no <tool_call>)\n"
                            "Reply again, doing only one of them."
                        ),
                    })
                    continue
                else:
                    # Third time: degrade - truncate at the first tool call and run it
                    logger.warning(
                        t('report.sectionConflictDowngrade', title=section.title, conflictCount=conflict_retries)
                    )
                    first_tool_end = response.find('</tool_call>')
                    if first_tool_end != -1:
                        response = response[:first_tool_end + len('</tool_call>')]
                        tool_calls = self._parse_tool_calls(response)
                        has_tool_calls = bool(tool_calls)
                    has_final_answer = False
                    conflict_retries = 0

            # Log the LLM response
            if self.report_logger:
                self.report_logger.log_llm_response(
                    section_title=section.title,
                    section_index=section_index,
                    response=response,
                    iteration=iteration + 1,
                    has_tool_calls=has_tool_calls,
                    has_final_answer=has_final_answer
                )

            # -- Case 1: the reply is a Final Answer --
            if has_final_answer:
                cleaned_response = ReportAgent._strip_fake_tool_results(response)
                # Too few tool calls: reject and ask for more retrieval
                if tool_calls_count < min_tool_calls:
                    messages.append({"role": "assistant", "content": cleaned_response})
                    unused_tools = all_tools - used_tools
                    unused_hint = f" (not yet used, worth trying: {', '.join(unused_tools)})" if unused_tools else ""
                    messages.append({
                        "role": "user",
                        "content": REACT_INSUFFICIENT_TOOLS_MSG.format(
                            tool_calls_count=tool_calls_count,
                            min_tool_calls=min_tool_calls,
                            unused_hint=unused_hint,
                        ),
                    })
                    continue

                # Normal completion
                final_answer = cleaned_response.split("Final Answer:")[-1].strip()
                logger.info(t('report.sectionGenDone', title=section.title, count=tool_calls_count))

                if self.report_logger:
                    self.report_logger.log_section_content(
                        section_title=section.title,
                        section_index=section_index,
                        content=final_answer,
                        tool_calls_count=tool_calls_count
                    )
                return final_answer

            # -- Case 2: the reply is a tool call --
            if has_tool_calls:
                # Budget exhausted: say so and demand a Final Answer
                if tool_calls_count >= self.MAX_TOOL_CALLS_PER_SECTION:
                    cleaned_response = ReportAgent._strip_fake_tool_results(response)
                    messages.append({"role": "assistant", "content": cleaned_response})
                    messages.append({
                        "role": "user",
                        "content": REACT_TOOL_LIMIT_MSG.format(
                            tool_calls_count=tool_calls_count,
                            max_tool_calls=self.MAX_TOOL_CALLS_PER_SECTION,
                        ),
                    })
                    continue

                # Only the first tool call is executed
                call = tool_calls[0]
                if len(tool_calls) > 1:
                    logger.info(t('report.multiToolOnlyFirst', total=len(tool_calls), toolName=call['name']))

                if self.report_logger:
                    self.report_logger.log_tool_call(
                        section_title=section.title,
                        section_index=section_index,
                        tool_name=call["name"],
                        parameters=call.get("parameters", {}),
                        iteration=iteration + 1
                    )

                result = self._execute_tool(
                    call["name"],
                    call.get("parameters", {}),
                    report_context=report_context
                )

                if self.report_logger:
                    self.report_logger.log_tool_result(
                        section_title=section.title,
                        section_index=section_index,
                        tool_name=call["name"],
                        result=result,
                        iteration=iteration + 1
                    )

                tool_calls_count += 1
                used_tools.add(call['name'])

                # Build the hint about unused tools
                unused_tools = all_tools - used_tools
                unused_hint = ""
                if unused_tools and tool_calls_count < self.MAX_TOOL_CALLS_PER_SECTION:
                    unused_hint = REACT_UNUSED_TOOLS_HINT.format(unused_list="、".join(unused_tools))

                cleaned_response = ReportAgent._strip_fake_tool_results(response)
                messages.append({"role": "assistant", "content": cleaned_response})
                messages.append({
                    "role": "user",
                    "content": REACT_OBSERVATION_TEMPLATE.format(
                        tool_name=call["name"],
                        result=result,
                        tool_calls_count=tool_calls_count,
                        max_tool_calls=self.MAX_TOOL_CALLS_PER_SECTION,
                        used_tools_str=", ".join(used_tools),
                        unused_hint=unused_hint,
                    ),
                })
                continue

            # -- Case 3: neither a tool call nor a Final Answer --
            cleaned_response = ReportAgent._strip_fake_tool_results(response)
            messages.append({"role": "assistant", "content": cleaned_response})

            if tool_calls_count < min_tool_calls:
                # Too few tool calls: suggest the tools not used yet
                unused_tools = all_tools - used_tools
                unused_hint = f" (not yet used, worth trying: {', '.join(unused_tools)})" if unused_tools else ""

                messages.append({
                    "role": "user",
                    "content": REACT_INSUFFICIENT_TOOLS_MSG_ALT.format(
                        tool_calls_count=tool_calls_count,
                        min_tool_calls=min_tool_calls,
                        unused_hint=unused_hint,
                    ),
                })
                continue

            # Enough tool calls were made and the model produced content without
            # the "Final Answer:" prefix. Take it as the answer rather than spinning.
            logger.info(t('report.sectionNoPrefix', title=section.title, count=tool_calls_count))
            final_answer = cleaned_response

            if self.report_logger:
                self.report_logger.log_section_content(
                    section_title=section.title,
                    section_index=section_index,
                    content=final_answer,
                    tool_calls_count=tool_calls_count
                )
            return final_answer
        
        # Iteration limit reached: force the content out
        logger.warning(t('report.sectionMaxIter', title=section.title))
        messages.append({"role": "user", "content": REACT_FORCE_FINAL_MSG})
        
        response = self.llm.chat(
            messages=messages,
            temperature=0.5,
            max_tokens=4096
        )

        # The forced close may also return None
        if response is None:
            logger.error(t('report.sectionForceFailed', title=section.title))
            final_answer = t('report.sectionGenFailedContent')
        elif "Final Answer:" in response:
            final_answer = response.split("Final Answer:")[-1].strip()
        else:
            final_answer = response
        
        # Log that the section body was produced
        if self.report_logger:
            self.report_logger.log_section_content(
                section_title=section.title,
                section_index=section_index,
                content=final_answer,
                tool_calls_count=tool_calls_count
            )
        
        return final_answer
    
    def generate_report(
        self, 
        progress_callback: Optional[Callable[[str, int, str], None]] = None,
        report_id: Optional[str] = None
    ) -> Report:
        """
        Generate the full report, streaming it out section by section.

        Each section is written to the folder as soon as it is finished, so the
        whole report does not have to complete first.
        File layout:
reports/{report_id}/
            meta.json       - report metadata
            outline.json    - report outline
            progress.json   - generation progress
            section_01.md   - section 1
            section_02.md   - section 2
            ...
            full_report.md  - the complete report

        Args:
            progress_callback: Progress callback (stage, progress, message)
            report_id: Report ID (optional; generated when omitted)

        Returns:
            Report: the complete report
"""
        import uuid
        
        # Generate a report_id when none was supplied
        if not report_id:
            report_id = f"report_{uuid.uuid4().hex[:12]}"
        start_time = datetime.now()
        
        report = Report(
            report_id=report_id,
            simulation_id=self.simulation_id,
            graph_id=self.graph_id,
            simulation_requirement=self.simulation_requirement,
            status=ReportStatus.PENDING,
            created_at=datetime.now().isoformat()
        )
        
        # Titles of the completed sections, for progress tracking
        completed_section_titles = []
        
        try:
            # Create the report folder and write the initial state
            ReportManager._ensure_report_folder(report_id)
            
            # Structured logger (agent_log.jsonl)
            self.report_logger = ReportLogger(report_id)
            self.report_logger.log_start(
                simulation_id=self.simulation_id,
                graph_id=self.graph_id,
                simulation_requirement=self.simulation_requirement
            )
            
            # Console logger (console_log.txt)
            self.console_logger = ReportConsoleLogger(report_id)
            
            ReportManager.update_progress(
                report_id, "pending", 0, t('progress.initReport'),
                completed_sections=[]
            )
            ReportManager.save_report(report)
            
            # Stage 1: plan the outline
            report.status = ReportStatus.PLANNING
            ReportManager.update_progress(
                report_id, "planning", 5, t('progress.startPlanningOutline'),
                completed_sections=[]
            )
            
            # Log the start of planning
            self.report_logger.log_planning_start()
            
            if progress_callback:
                progress_callback("planning", 0, t('progress.startPlanningOutline'))
            
            outline = self.plan_outline(
                progress_callback=lambda stage, prog, msg: 
                    progress_callback(stage, prog // 5, msg) if progress_callback else None
            )
            report.outline = outline
            
            # Log the completed plan
            self.report_logger.log_planning_complete(outline.to_dict())
            
            # Write the outline to disk
            ReportManager.save_outline(report_id, outline)
            ReportManager.update_progress(
                report_id, "planning", 15, t('progress.outlineDone', count=len(outline.sections)),
                completed_sections=[]
            )
            ReportManager.save_report(report)
            
            logger.info(t('report.outlineSavedToFile', reportId=report_id))
            
            # Stage 2: generate section by section, saving as we go
            report.status = ReportStatus.GENERATING
            
            total_sections = len(outline.sections)
            generated_sections = []  # Kept for the next section's context
            
            for i, section in enumerate(outline.sections):
                section_num = i + 1
                base_progress = 20 + int((i / total_sections) * 70)
                
                # Update the progress
                ReportManager.update_progress(
                    report_id, "generating", base_progress,
                    t('progress.generatingSection', title=section.title, current=section_num, total=total_sections),
                    current_section=section.title,
                    completed_sections=completed_section_titles
                )

                if progress_callback:
                    progress_callback(
                        "generating",
                        base_progress,
                        t('progress.generatingSection', title=section.title, current=section_num, total=total_sections)
                    )
                
                # Generate the section body
                section_content = self._generate_section_react(
                    section=section,
                    outline=outline,
                    previous_sections=generated_sections,
                    progress_callback=lambda stage, prog, msg:
                        progress_callback(
                            stage, 
                            base_progress + int(prog * 0.7 / total_sections),
                            msg
                        ) if progress_callback else None,
                    section_index=section_num
                )
                
                section.content = section_content
                generated_sections.append(f"## {section.title}\n\n{section_content}")

                # Save the section
                ReportManager.save_section(report_id, section_num, section)
                completed_section_titles.append(section.title)

                # Log the completed section
                full_section_content = f"## {section.title}\n\n{section_content}"

                if self.report_logger:
                    self.report_logger.log_section_full_complete(
                        section_title=section.title,
                        section_index=section_num,
                        full_content=full_section_content.strip()
                    )

                logger.info(t('report.sectionSaved', reportId=report_id, sectionNum=f"{section_num:02d}"))
                
                # Update the progress
                ReportManager.update_progress(
                    report_id, "generating", 
                    base_progress + int(70 / total_sections),
                    t('progress.sectionDone', title=section.title),
                    current_section=None,
                    completed_sections=completed_section_titles
                )
            
            # Stage 3: assemble the full report
            if progress_callback:
                progress_callback("generating", 95, t('progress.assemblingReport'))
            
            ReportManager.update_progress(
                report_id, "generating", 95, t('progress.assemblingReport'),
                completed_sections=completed_section_titles
            )
            
            # ReportManager assembles the full report
            report.markdown_content = ReportManager.assemble_full_report(report_id, outline)
            report.status = ReportStatus.COMPLETED
            report.completed_at = datetime.now().isoformat()
            
            # Total elapsed time
            total_time_seconds = (datetime.now() - start_time).total_seconds()
            
            # Log the completed report
            if self.report_logger:
                self.report_logger.log_report_complete(
                    total_sections=total_sections,
                    total_time_seconds=total_time_seconds
                )
            
            # Save the final report
            ReportManager.save_report(report)
            ReportManager.update_progress(
                report_id, "completed", 100, t('progress.reportComplete'),
                completed_sections=completed_section_titles
            )
            
            if progress_callback:
                progress_callback("completed", 100, t('progress.reportComplete'))
            
            logger.info(t('report.reportGenDone', reportId=report_id))
            
            # Close the console logger
            if self.console_logger:
                self.console_logger.close()
                self.console_logger = None
            
            return report
            
        except Exception as e:
            logger.error(t('report.reportGenFailed', error=str(e)))
            report.status = ReportStatus.FAILED
            report.error = str(e)
            
            # Log the error
            if self.report_logger:
                self.report_logger.log_error(str(e), "failed")
            
            # Persist the failure state
            try:
                ReportManager.save_report(report)
                ReportManager.update_progress(
                    report_id, "failed", -1, t('progress.reportFailed', error=str(e)),
                    completed_sections=completed_section_titles
                )
            except Exception:
                pass  # A failure to save the failure is ignored
            
            # Close the console logger
            if self.console_logger:
                self.console_logger.close()
                self.console_logger = None
            
            return report
    
    def chat(
        self, 
        message: str,
        chat_history: List[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Chat with the report agent.

        The agent may call retrieval tools on its own to answer the question.

        Args:
            message: The user message
            chat_history: The conversation history

        Returns:
            {
                "response": "the agent reply",
                "tool_calls": [tools that were called],
                "sources": [where the information came from]
            }
        """
        logger.info(t('report.agentChat', message=message[:50]))
        
        chat_history = chat_history or []
        
        # Load the report content generated so far
        report_content = ""
        try:
            report = ReportManager.get_report_by_simulation(self.simulation_id)
            if report and report.markdown_content:
                # Cap the report length so the context does not blow up
                report_content = report.markdown_content[:15000]
                if len(report.markdown_content) > 15000:
                    report_content += "\n\n... [report content truncated] ..."
        except Exception as e:
            logger.warning(t('report.fetchReportFailed', error=e))
        
        system_prompt = CHAT_SYSTEM_PROMPT_TEMPLATE.format(
            simulation_requirement=self.simulation_requirement,
            report_content=report_content if report_content else "(no report yet)",
            tools_description=self._get_tools_description(),
        )
        system_prompt = f"{system_prompt}\n\n{get_language_instruction()}"

        # Build the message list
        messages = [{"role": "system", "content": system_prompt}]
        
        # Add the conversation history
        for h in chat_history[-10:]:  # Cap the history length
            messages.append(h)
        
        # Add the user message
        messages.append({
            "role": "user", 
            "content": message
        })
        
        # ReACT loop, simplified
        tool_calls_made = []
        max_iterations = 2  # Fewer iterations here
        
        for iteration in range(max_iterations):
            response = self.llm.chat(
                messages=messages,
                temperature=0.5
            )
            
            # Parse the tool call
            tool_calls = self._parse_tool_calls(response)
            
            if not tool_calls:
                # No tool call: return the response as-is
                clean_response = re.sub(r'<tool_call>.*?</tool_call>', '', response, flags=re.DOTALL)
                clean_response = re.sub(r'\[TOOL_CALL\].*?\)', '', clean_response)
                clean_response = ReportAgent._strip_fake_tool_results(clean_response)
                
                return {
                    "response": clean_response.strip(),
                    "tool_calls": tool_calls_made,
                    "sources": [tc.get("parameters", {}).get("query", "") for tc in tool_calls_made]
                }
            
            # Run the tool calls, capped
            tool_results = []
            for call in tool_calls[:1]:  # At most one tool call per round
                if len(tool_calls_made) >= self.MAX_TOOL_CALLS_PER_CHAT:
                    break
                result = self._execute_tool(call["name"], call.get("parameters", {}))
                tool_results.append({
                    "tool": call["name"],
                    "result": result[:1500]  # Cap the result length
                })
                tool_calls_made.append(call)
            
            # Append the results to the messages
            cleaned_response = ReportAgent._strip_fake_tool_results(response)
            messages.append({"role": "assistant", "content": cleaned_response})
            observation = "\n".join([f"[{r['tool']} result]\n{r['result']}" for r in tool_results])
            messages.append({
                "role": "user",
                "content": observation + CHAT_OBSERVATION_SUFFIX
            })
        
        # Iteration limit reached: take the final response
        final_response = self.llm.chat(
            messages=messages,
            temperature=0.5
        )
        
        # Clean the response
        clean_response = re.sub(r'<tool_call>.*?</tool_call>', '', final_response, flags=re.DOTALL)
        clean_response = re.sub(r'\[TOOL_CALL\].*?\)', '', clean_response)
        clean_response = ReportAgent._strip_fake_tool_results(clean_response)
        
        return {
            "response": clean_response.strip(),
            "tool_calls": tool_calls_made,
            "sources": [tc.get("parameters", {}).get("query", "") for tc in tool_calls_made]
        }


class ReportManager:
    """
    Report manager.

    Handles persistent storage and retrieval of reports.

    File layout (one file per section):
reports/
      {report_id}/
        meta.json          - report metadata and status
        outline.json       - report outline
        progress.json      - generation progress
        section_01.md      - section 1
        section_02.md      - section 2
        ...
        full_report.md     - the complete report
    """
    
    # Report storage directory
    REPORTS_DIR = os.path.join(Config.UPLOAD_FOLDER, 'reports')
    
    @classmethod
    def _ensure_reports_dir(cls):
        """Make sure the reports root directory exists."""
        os.makedirs(cls.REPORTS_DIR, exist_ok=True)
    
    @classmethod
    def _get_report_folder(cls, report_id: str) -> str:
        """Return the report folder path."""
        return os.path.join(cls.REPORTS_DIR, report_id)
    
    @classmethod
    def _ensure_report_folder(cls, report_id: str) -> str:
        """Make sure the report folder exists and return its path."""
        folder = cls._get_report_folder(report_id)
        os.makedirs(folder, exist_ok=True)
        return folder
    
    @classmethod
    def _get_report_path(cls, report_id: str) -> str:
        """Return the report metadata file path."""
        return os.path.join(cls._get_report_folder(report_id), "meta.json")
    
    @classmethod
    def _get_report_markdown_path(cls, report_id: str) -> str:
        """Return the full-report Markdown path."""
        return os.path.join(cls._get_report_folder(report_id), "full_report.md")
    
    @classmethod
    def _get_outline_path(cls, report_id: str) -> str:
        """Return the outline file path."""
        return os.path.join(cls._get_report_folder(report_id), "outline.json")
    
    @classmethod
    def _get_progress_path(cls, report_id: str) -> str:
        """Return the progress file path."""
        return os.path.join(cls._get_report_folder(report_id), "progress.json")
    
    @classmethod
    def _get_section_path(cls, report_id: str, section_index: int) -> str:
        """Return the Markdown path for one section."""
        return os.path.join(cls._get_report_folder(report_id), f"section_{section_index:02d}.md")
    
    @classmethod
    def _get_agent_log_path(cls, report_id: str) -> str:
        """Return the agent log file path."""
        return os.path.join(cls._get_report_folder(report_id), "agent_log.jsonl")
    
    @classmethod
    def _get_console_log_path(cls, report_id: str) -> str:
        """Return the console log file path."""
        return os.path.join(cls._get_report_folder(report_id), "console_log.txt")
    
    @classmethod
    def get_console_log(cls, report_id: str, from_line: int = 0) -> Dict[str, Any]:
        """
        Read the console log.

        This is the console output produced while the report was generated
        (INFO, WARNING, ...), as opposed to the structured agent_log.jsonl.

        Args:
            report_id: Report ID
            from_line: Line to start from, for incremental reads (0 = the beginning)

        Returns:
            {
                "logs": [the log lines],
                "total_lines": total line count,
                "from_line": the starting line,
                "has_more": whether more log lines remain
            }
        """
        log_path = cls._get_console_log_path(report_id)
        
        if not os.path.exists(log_path):
            return {
                "logs": [],
                "total_lines": 0,
                "from_line": 0,
                "has_more": False
            }
        
        logs = []
        total_lines = 0
        
        with open(log_path, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f):
                total_lines = i + 1
                if i >= from_line:
                    # Keep the raw line, minus the trailing newline
                    logs.append(line.rstrip('\n\r'))
        
        return {
            "logs": logs,
            "total_lines": total_lines,
            "from_line": from_line,
            "has_more": False  # Read through to the end
        }
    
    @classmethod
    def get_console_log_stream(cls, report_id: str) -> List[str]:
        """
        Read the whole console log in one go.

        Args:
            report_id: Report ID

        Returns:
            The log lines
"""
        result = cls.get_console_log(report_id, from_line=0)
        return result["logs"]
    
    @classmethod
    def get_agent_log(cls, report_id: str, from_line: int = 0) -> Dict[str, Any]:
        """
        Read the agent log.

        Args:
            report_id: Report ID
            from_line: Line to start from, for incremental reads (0 = the beginning)

        Returns:
            {
                "logs": [the log entries],
                "total_lines": total line count,
                "from_line": the starting line,
                "has_more": whether more entries remain
            }
        """
        log_path = cls._get_agent_log_path(report_id)
        
        if not os.path.exists(log_path):
            return {
                "logs": [],
                "total_lines": 0,
                "from_line": 0,
                "has_more": False
            }
        
        logs = []
        total_lines = 0
        
        with open(log_path, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f):
                total_lines = i + 1
                if i >= from_line:
                    try:
                        log_entry = json.loads(line.strip())
                        logs.append(log_entry)
                    except json.JSONDecodeError:
                        # Skip lines that failed to parse
                        continue
        
        return {
            "logs": logs,
            "total_lines": total_lines,
            "from_line": from_line,
            "has_more": False  # Read through to the end
        }
    
    @classmethod
    def get_agent_log_stream(cls, report_id: str) -> List[Dict[str, Any]]:
        """
        Read the whole agent log in one go.

        Args:
            report_id: Report ID

        Returns:
            The log entries
"""
        result = cls.get_agent_log(report_id, from_line=0)
        return result["logs"]
    
    @classmethod
    def save_outline(cls, report_id: str, outline: ReportOutline) -> None:
        """
        Save the report outline.

        Called as soon as planning finishes.
"""
        cls._ensure_report_folder(report_id)
        
        with open(cls._get_outline_path(report_id), 'w', encoding='utf-8') as f:
            json.dump(outline.to_dict(), f, ensure_ascii=False, indent=2)
        
        logger.info(t('report.outlineSaved', reportId=report_id))
    
    @classmethod
    def save_section(
        cls,
        report_id: str,
        section_index: int,
        section: ReportSection
    ) -> str:
        """
        Save one section.

        Called as soon as a section finishes, which is what makes the streaming
        section-by-section output work.

        Args:
            report_id: Report ID
            section_index: Section index, 1-based
            section: The section

        Returns:
            The path it was written to
        """
        cls._ensure_report_folder(report_id)

        # Build the section Markdown, stripping any duplicated heading
        cleaned_content = cls._clean_section_content(section.content, section.title)
        md_content = f"## {section.title}\n\n"
        if cleaned_content:
            md_content += f"{cleaned_content}\n\n"

        # Write the file
        file_suffix = f"section_{section_index:02d}.md"
        file_path = os.path.join(cls._get_report_folder(report_id), file_suffix)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(md_content)

        logger.info(t('report.sectionFileSaved', reportId=report_id, fileSuffix=file_suffix))
        return file_path
    
    @classmethod
    def _clean_section_content(cls, content: str, section_title: str) -> str:
        """
        Clean up a section body.

        1. Drop a leading Markdown heading that duplicates the section title
        2. Turn every ### and deeper heading into bold text

        Args:
            content: The raw content
            section_title: The section title

        Returns:
            The cleaned content
"""
        import re
        
        if not content:
            return content
        
        content = content.strip()
        lines = content.split('\n')
        cleaned_lines = []
        skip_next_empty = False
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            
            # Is this a Markdown heading?
            heading_match = re.match(r'^(#{1,6})\s+(.+)$', stripped)
            
            if heading_match:
                level = len(heading_match.group(1))
                title_text = heading_match.group(2).strip()
                
                # Drop a heading that duplicates the section title (within the first 5 lines)
                if i < 5:
                    if title_text == section_title or title_text.replace(' ', '') == section_title.replace(' ', ''):
                        skip_next_empty = True
                        continue
                
                # Turn every heading level (#, ##, ###, ####) into bold text.
                # The section title is added by the system, so the body must carry none.
                cleaned_lines.append(f"**{title_text}**")
                cleaned_lines.append("")  # Blank line
                continue
            
            # Also skip a blank line directly after a dropped heading
            if skip_next_empty and stripped == '':
                skip_next_empty = False
                continue
            
            skip_next_empty = False
            cleaned_lines.append(line)
        
        # Drop leading blank lines
        while cleaned_lines and cleaned_lines[0].strip() == '':
            cleaned_lines.pop(0)
        
        # Drop a leading horizontal rule
        while cleaned_lines and cleaned_lines[0].strip() in ['---', '***', '___']:
            cleaned_lines.pop(0)
            # And the blank line after it
            while cleaned_lines and cleaned_lines[0].strip() == '':
                cleaned_lines.pop(0)
        
        return '\n'.join(cleaned_lines)
    
    @classmethod
    def update_progress(
        cls, 
        report_id: str, 
        status: str, 
        progress: int, 
        message: str,
        current_section: str = None,
        completed_sections: List[str] = None
    ) -> None:
        """
        Update the generation progress.

        The frontend reads progress.json to follow it live.
"""
        cls._ensure_report_folder(report_id)
        
        progress_data = {
            "status": status,
            "progress": progress,
            "message": message,
            "current_section": current_section,
            "completed_sections": completed_sections or [],
            "updated_at": datetime.now().isoformat()
        }
        
        with open(cls._get_progress_path(report_id), 'w', encoding='utf-8') as f:
            json.dump(progress_data, f, ensure_ascii=False, indent=2)
    
    @classmethod
    def get_progress(cls, report_id: str) -> Optional[Dict[str, Any]]:
        """Return the generation progress."""
        path = cls._get_progress_path(report_id)
        
        if not os.path.exists(path):
            return None
        
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    @classmethod
    def get_generated_sections(cls, report_id: str) -> List[Dict[str, Any]]:
        """
        List the sections generated so far.

        Returns information about every section file already written.
"""
        folder = cls._get_report_folder(report_id)
        
        if not os.path.exists(folder):
            return []
        
        sections = []
        for filename in sorted(os.listdir(folder)):
            if filename.startswith('section_') and filename.endswith('.md'):
                file_path = os.path.join(folder, filename)
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                # Parse the section index out of the file name
                parts = filename.replace('.md', '').split('_')
                section_index = int(parts[1])

                sections.append({
                    "filename": filename,
                    "section_index": section_index,
                    "content": content
                })

        return sections
    
    @classmethod
    def assemble_full_report(cls, report_id: str, outline: ReportOutline) -> str:
        """
        Assemble the full report.

        Stitches the saved section files together and normalises the headings.
"""
        folder = cls._get_report_folder(report_id)
        
        # Build the report header
        md_content = f"# {outline.title}\n\n"
        md_content += f"> {outline.summary}\n\n"
        md_content += f"---\n\n"
        
        # Read every section file in order
        sections = cls.get_generated_sections(report_id)
        for section_info in sections:
            md_content += section_info["content"]
        
        # Post-process: normalise headings across the whole report
        md_content = cls._post_process_report(md_content, outline)
        
        # Write the full report
        full_path = cls._get_report_markdown_path(report_id)
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(md_content)
        
        logger.info(t('report.fullReportAssembled', reportId=report_id))
        return md_content
    
    @classmethod
    def _post_process_report(cls, content: str, outline: ReportOutline) -> str:
        """
        Post-process the report body.

        1. Drop duplicated headings
        2. Keep the report title (#) and the section titles (##); demote every
           deeper heading (###, ####, ...)
        3. Collapse stray blank lines and horizontal rules

        Args:
            content: The raw report content
            outline: The report outline

        Returns:
            The processed content
"""
        import re
        
        lines = content.split('\n')
        processed_lines = []
        prev_was_heading = False
        
        # Collect every section title from the outline
        section_titles = set()
        for section in outline.sections:
            section_titles.add(section.title)
        
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            
            # Is this a heading?
            heading_match = re.match(r'^(#{1,6})\s+(.+)$', stripped)
            
            if heading_match:
                level = len(heading_match.group(1))
                title = heading_match.group(2).strip()
                
                # Duplicated heading? (the same heading within 5 lines)
                is_duplicate = False
                for j in range(max(0, len(processed_lines) - 5), len(processed_lines)):
                    prev_line = processed_lines[j].strip()
                    prev_match = re.match(r'^(#{1,6})\s+(.+)$', prev_line)
                    if prev_match:
                        prev_title = prev_match.group(2).strip()
                        if prev_title == title:
                            is_duplicate = True
                            break
                
                if is_duplicate:
                    # Skip the duplicate and the blank line after it
                    i += 1
                    while i < len(lines) and lines[i].strip() == '':
                        i += 1
                    continue
                
                # Heading levels:
                # - # (level 1): keep only the report title
                # - ## (level 2): keep the section titles
                # - ### and deeper (level >= 3): turn into bold text
                
                if level == 1:
                    if title == outline.title:
                        # Keep the report title
                        processed_lines.append(line)
                        prev_was_heading = True
                    elif title in section_titles:
                        # A section title written as #: promote it to ##
                        processed_lines.append(f"## {title}")
                        prev_was_heading = True
                    else:
                        # Any other level-1 heading becomes bold
                        processed_lines.append(f"**{title}**")
                        processed_lines.append("")
                        prev_was_heading = False
                elif level == 2:
                    if title in section_titles or title == outline.title:
                        # Keep the section title
                        processed_lines.append(line)
                        prev_was_heading = True
                    else:
                        # A level-2 heading that is not a section title becomes bold
                        processed_lines.append(f"**{title}**")
                        processed_lines.append("")
                        prev_was_heading = False
                else:
                    # ### and deeper become bold text
                    processed_lines.append(f"**{title}**")
                    processed_lines.append("")
                    prev_was_heading = False
                
                i += 1
                continue
            
            elif stripped == '---' and prev_was_heading:
                # Skip a horizontal rule right after a heading
                i += 1
                continue
            
            elif stripped == '' and prev_was_heading:
                # Keep exactly one blank line after a heading
                if processed_lines and processed_lines[-1].strip() != '':
                    processed_lines.append(line)
                prev_was_heading = False
            
            else:
                processed_lines.append(line)
                prev_was_heading = False
            
            i += 1
        
        # Collapse runs of blank lines, keeping at most two
        result_lines = []
        empty_count = 0
        for line in processed_lines:
            if line.strip() == '':
                empty_count += 1
                if empty_count <= 2:
                    result_lines.append(line)
            else:
                empty_count = 0
                result_lines.append(line)
        
        return '\n'.join(result_lines)
    
    @classmethod
    def save_report(cls, report: Report) -> None:
        """Save the report metadata and the full report."""
        cls._ensure_report_folder(report.report_id)
        
        # Write the metadata JSON
        with open(cls._get_report_path(report.report_id), 'w', encoding='utf-8') as f:
            json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
        
        # Write the outline
        if report.outline:
            cls.save_outline(report.report_id, report.outline)
        
        # Write the full Markdown report
        if report.markdown_content:
            with open(cls._get_report_markdown_path(report.report_id), 'w', encoding='utf-8') as f:
                f.write(report.markdown_content)
        
        logger.info(t('report.reportSaved', reportId=report.report_id))
    
    @classmethod
    def get_report(cls, report_id: str) -> Optional[Report]:
        """Load a report."""
        path = cls._get_report_path(report_id)
        
        if not os.path.exists(path):
            # Legacy layout: a file sitting directly in the reports directory
            old_path = os.path.join(cls.REPORTS_DIR, f"{report_id}.json")
            if os.path.exists(old_path):
                path = old_path
            else:
                return None
        
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Rebuild the Report object
        outline = None
        if data.get('outline'):
            outline_data = data['outline']
            sections = []
            for s in outline_data.get('sections', []):
                sections.append(ReportSection(
                    title=s['title'],
                    content=s.get('content', '')
                ))
            outline = ReportOutline(
                title=outline_data['title'],
                summary=outline_data['summary'],
                sections=sections
            )
        
        # markdown_content is empty: fall back to full_report.md
        markdown_content = data.get('markdown_content', '')
        if not markdown_content:
            full_report_path = cls._get_report_markdown_path(report_id)
            if os.path.exists(full_report_path):
                with open(full_report_path, 'r', encoding='utf-8') as f:
                    markdown_content = f.read()
        
        return Report(
            report_id=data['report_id'],
            simulation_id=data['simulation_id'],
            graph_id=data['graph_id'],
            simulation_requirement=data['simulation_requirement'],
            status=ReportStatus(data['status']),
            outline=outline,
            markdown_content=markdown_content,
            created_at=data.get('created_at', ''),
            completed_at=data.get('completed_at', ''),
            error=data.get('error')
        )
    
    @classmethod
    def get_report_by_simulation(cls, simulation_id: str) -> Optional[Report]:
        """Load the report for a simulation."""
        cls._ensure_reports_dir()
        
        for item in os.listdir(cls.REPORTS_DIR):
            item_path = os.path.join(cls.REPORTS_DIR, item)
            # Current layout: a folder
            if os.path.isdir(item_path):
                report = cls.get_report(item)
                if report and report.simulation_id == simulation_id:
                    return report
            # Legacy layout: a JSON file
            elif item.endswith('.json'):
                report_id = item[:-5]
                report = cls.get_report(report_id)
                if report and report.simulation_id == simulation_id:
                    return report
        
        return None
    
    @classmethod
    def list_reports(cls, simulation_id: Optional[str] = None, limit: int = 50) -> List[Report]:
        """List the reports."""
        cls._ensure_reports_dir()
        
        reports = []
        for item in os.listdir(cls.REPORTS_DIR):
            item_path = os.path.join(cls.REPORTS_DIR, item)
            # Current layout: a folder
            if os.path.isdir(item_path):
                report = cls.get_report(item)
                if report:
                    if simulation_id is None or report.simulation_id == simulation_id:
                        reports.append(report)
            # Legacy layout: a JSON file
            elif item.endswith('.json'):
                report_id = item[:-5]
                report = cls.get_report(report_id)
                if report:
                    if simulation_id is None or report.simulation_id == simulation_id:
                        reports.append(report)
        
        # Newest first
        reports.sort(key=lambda r: r.created_at, reverse=True)
        
        return reports[:limit]
    
    @classmethod
    def delete_report(cls, report_id: str) -> bool:
        """Delete a report, folder and all."""
        import shutil
        
        folder_path = cls._get_report_folder(report_id)
        
        # Current layout: remove the whole folder
        if os.path.exists(folder_path) and os.path.isdir(folder_path):
            shutil.rmtree(folder_path)
            logger.info(t('report.reportFolderDeleted', reportId=report_id))
            return True
        
        # Legacy layout: remove the standalone file
        deleted = False
        old_json_path = os.path.join(cls.REPORTS_DIR, f"{report_id}.json")
        old_md_path = os.path.join(cls.REPORTS_DIR, f"{report_id}.md")
        
        if os.path.exists(old_json_path):
            os.remove(old_json_path)
            deleted = True
        if os.path.exists(old_md_path):
            os.remove(old_md_path)
            deleted = True
        
        return deleted
