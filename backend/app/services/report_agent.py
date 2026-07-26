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
[Audience interview - real agent interviews, across both channels]
Calls the OASIS interview API to interview the audience agents actually running
inside the simulation. This is not an LLM impersonation: it hits the real
interview endpoint and returns the agents' own answers. By default it interviews
on both Twitter and Reddit at once, for a fuller range of segment views.

How it works:
1. Read the persona files to learn about every audience agent
2. Pick the agents most relevant to the interview topic (a given buyer segment,
   the loudest detractors, the people who amplified the campaign, and so on)
3. Write the interview questions automatically
4. Call /api/simulation/interview/batch to run the real interview on both channels
5. Combine the answers into a multi-segment analysis

[When to use it]
- You need the "why" behind a number the metrics tool gave you (why did this
  segment not convert? what specifically put them off?)
- You need purchase intent, brand perception or objections stated in the
  audience's own words - these are NOT countable from the action log
- You need to compare how different buyer segments read the same creative
- You want verbatim audience quotes for the report

[What it returns]
- Who each respondent is, and which segment they belong to
- Each agent's answers on Twitter and on Reddit
- Key quotes, ready to quote directly
- An interview summary and a comparison of the views

[Important] The OASIS simulation environment must be running for this to work."""

TOOL_DESC_CAMPAIGN_METRICS = """\
[Campaign KPIs - hard numbers, counted not estimated]
Reads the raw action log of the simulation and returns the measured marketing
KPIs. Every figure here is a count, not an LLM estimate, so you may state these
numbers in the report as measurements.

[What it returns]
- Reach / exposure: audience size, agents reached, reach rate, total impressions
- Engagement: engaged agents, engagement rate, passive share, and the split
  across posts, comments, likes, dislikes and follows
- Virality: reposts, quotes, amplifier agents, virality ratio (amplifications
  per authored post), cascade depth in rounds, and the peak round
- Sentiment split: positive vs negative behavioural signals and a net score
- Share of voice: which audience segment authored what share of the conversation
- Segment breakdown: reach, engagement, sentiment and amplification per segment
- The round-by-round curve, so you can see when the campaign peaked and decayed
- The loudest voices driving the conversation

[When to use it]
- ALWAYS call this first when a section reports on reach, engagement, virality,
  sentiment split, share of voice or segment performance
- Whenever you are about to state a number - use the measured figure instead

[What it does NOT cover]
Purchase intent and specific objections are not countable from the action log.
Get those from interview_agents and from what the agents actually wrote.

[Parameters] None required."""

# -- Outline planning prompts --

PLAN_SYSTEM_PROMPT = """\
You are a marketing effectiveness analyst writing a CAMPAIGN ASSESSMENT REPORT.
You have full visibility into a simulated audience: every reaction, post,
comment, like, share and silence is logged and available to you.

[Core idea]
We took a campaign brief - its creative, its message, its target audience - and
released it to a simulated audience built from that audience's real
characteristics. How that audience reacted is our forecast of how the real
market will receive the campaign. What you are looking at is a pre-flight test
of the campaign, not an academic experiment.

[Your task]
Write a campaign assessment that answers the questions a marketing lead has
before spending the budget:
1. How far did the campaign travel, and who did it actually reach?
2. Did the audience engage, ignore it, or push back - and in what proportion?
3. Did it spread on its own, or did it need to be pushed?
4. Which segments bought into the message, and which resisted it?
5. What are the objections, and what should change before launch?

[What the report is]
- YES: an assessment of THIS campaign's likely market performance
- YES: built on marketing KPIs - reach, engagement, sentiment split, virality,
  purchase intent, share of voice, segment performance
- YES: the simulated agents' posts and reactions ARE audience reactions to the
  campaign; treat them as verbatim consumer response
- NO: a generic prediction of the future or a public-opinion survey
- NO: a description of the brand or the market as it exists today
- NO: vague conclusions with no number attached

[Sections to draw from]
Pick the ones the evidence actually supports. Candidates:
- Reach and exposure
- Sentiment split (positive / negative / indifferent)
- Engagement quality
- Virality and the share cascade
- Purchase intent and conversion signals
- Key objections and message risks
- Segment breakdown
- Recommendations before launch

[Section count]
- At least 2 sections, at most 5
- No sub-sections; write each section as one complete piece
- Merge related KPIs into one section rather than spreading them thin
- Always reserve one section for actionable recommendations
- You design the section structure yourself, based on what the results show

Output the report outline as JSON, in this shape:
{
    "title": "report title",
    "summary": "report summary (one sentence stating the campaign's headline result)",
    "sections": [
        {
            "title": "section title",
            "description": "what the section covers"
        }
    ]
}

Note: the sections array must hold at least 2 and at most 5 elements."""

PLAN_USER_PROMPT_TEMPLATE = """\
[Campaign under assessment]
The campaign brief and target audience we released into the simulated market: {simulation_requirement}

[Scale of the simulated audience]
- Audience entities modelled: {total_nodes}
- Relationships between them (the social fabric the message travels through): {total_edges}
- Audience segment types: {entity_types}
- Active audience agents: {total_entities}

[Measured campaign KPIs]
{campaign_metrics}

[A sample of the audience reactions the simulation produced]
{related_facts_json}

Assess this campaign the way a marketing lead would before signing off the spend:
1. What is the headline result - did this campaign land, and how hard?
2. Where did reach, engagement and virality actually come from?
3. Which segments converted, which stayed indifferent, and which pushed back?
4. What has to change before launch?

Design the section structure that best fits what the results show. Lead with the
KPIs that moved, not with a fixed template.

[Reminder] Section count: at least 2, at most 5. Keep every section anchored to
evidence, and reserve one for recommendations."""

# -- Section generation prompts --

SECTION_SYSTEM_PROMPT_TEMPLATE = """\
You are a marketing effectiveness analyst writing one section of a CAMPAIGN
ASSESSMENT REPORT.

Report title: {report_title}
Report summary: {report_summary}
Campaign under assessment (brief and target audience): {simulation_requirement}

The section you are writing now: {section_title}

═══════════════════════════════════════════════════════════════
[Core idea]
═══════════════════════════════════════════════════════════════

The simulated audience is a pre-flight test of this campaign. We released the
campaign's creative and message to an audience built from the target market's
real characteristics, and what those agents posted, shared, liked, ignored and
argued with IS the forecast of how the real market will react.

Your task is to:
- Report how the campaign performed against marketing KPIs
- Explain WHY it performed that way, in the audience's own words
- Show which segments responded and which did not
- Surface the objections and risks that would cost money at launch

NO: do not describe the brand or the market as it exists today
NO: do not write a generic public-opinion survey
YES: assess THIS campaign's likely market performance

═══════════════════════════════════════════════════════════════
[The rules that matter most - you must follow them]
═══════════════════════════════════════════════════════════════

1. [Numbers come from campaign_metrics, never from your own head]
   - campaign_metrics returns COUNTED figures from the action log
   - If your section states any reach, engagement, sentiment, virality, share of
     voice or segment figure, it must be the measured figure from that tool
   - Never estimate, round for effect, or infer a percentage the tool did not give you
   - Percentages that the tool did not return do not belong in the report

2. [You must call tools to observe the audience]
   - Every statement must come from measured KPIs or from the agents' own
     behaviour inside the simulation
   - Do not write report content out of your own marketing knowledge
   - Call tools at least 3 times per section (at most 5)

3. [You must quote the audience's own words]
   - What an agent posts is a consumer reaction to the campaign - the closest
     thing to a verbatim from a focus group
   - Present those reactions as quotes, for example:
     > "One buyer in this segment reacted: ...verbatim content..."
   - Those quotes are what turns a number into an insight: the metric says
     engagement was low, the quote says why

4. [Language consistency - quoted material must be translated into the report language]
   - Tool output may be phrased in a different language from the report
   - The whole report must be written in the language the user specified
   - When you quote tool output written in another language, translate it into
     the report language before writing it down
   - Keep the meaning intact and make the phrasing read naturally
   - This applies to body text and to quote blocks (> format) alike

5. [Report the result faithfully, good or bad]
   - A campaign that underperformed must be reported as underperforming
   - Do not soften a weak number or pad a thin result with optimism
   - Where the evidence is thin, say so plainly rather than filling the gap
   - A finding the marketing lead can act on beats a finding that flatters

6. [Never fabricate data]
   - NO: do not invent usernames, quotes, statistics or interaction counts
   - NO: do not include a <tool_result> block in your reply - only the system
     supplies tool results
   - YES: only cite segments, quotes and numbers that genuinely appear in the
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
The campaign reached 142 of the 180 modelled audience members, a 78.9% reach
rate, but only 41 of those reacted - an engagement rate of 28.9%.

**Where the engagement came from**

Amplification was concentrated in one segment rather than spread across the
audience:

> "Finally a brand that gets what commuting is actually like. Sending this to
> my group chat."

**Where it stalled**

The older segment saw the creative and stayed passive:

- Price framing read as aimed at someone else
- No product benefit stated in the first line
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
- campaign_metrics: the measured KPIs. Call this FIRST in any section that
  reports reach, engagement, sentiment split, virality, share of voice or
  segment performance. Every number you print should come from here.
- insight_forge: deep analysis; decomposes the question and retrieves audience
  reactions and relationships along several dimensions
- panorama_search: wide-angle view; the whole campaign arc, how reaction evolved
  from launch through decay
- quick_search: verify one specific point fast
- interview_agents: interview the audience agents for purchase intent, brand
  perception and objections in their own words - the things no counter can measure

[The pattern that produces a good section]
Measure first, then explain: pull the number from campaign_metrics, then use
insight_forge or interview_agents to find out why the audience behaved that way.
A number with no explanation is a dashboard, not an assessment.

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

1. Content must rest on measured KPIs and audience reactions retrieved through
   the tools
2. Quote the audience generously - verbatim reactions are what make the
   assessment persuasive
3. Where the section allows it, end on what the marketing team should DO about
   the finding, not just what the finding is
4. Use Markdown, but no headings:
   - **Bold** for emphasis, in place of sub-headings
   - Lists (- or 1. 2. 3.) to organise points
   - Blank lines between paragraphs
   - NO #, ##, ###, #### or any other heading syntax
5. [Quote formatting - a quote must stand alone]
   A quote must be its own paragraph, with a blank line before and after. Never
   inline it inside a paragraph:

   Correct:
   ```
   The value message did not survive contact with this segment.

   > "Looks nice but nothing here tells me why it costs twice what I pay now."

   That objection came up repeatedly and is the clearest fix available.
   ```

   Incorrect:
   ```
   The value message did not land. > "Looks nice but..." That objection came up...
   ```
6. Stay logically coherent with the other sections
7. [Avoid repetition] Read the completed sections below carefully and do not
   restate the same information
8. [Once more] Add no headings. Use **bold** in place of sub-headings."""

SECTION_USER_PROMPT_TEMPLATE = """\
Sections completed so far (read them carefully; do not repeat them):
{previous_content}

═══════════════════════════════════════════════════════════════
[Current task] Write the section: {section_title}
═══════════════════════════════════════════════════════════════

[Reminders]
1. Read the completed sections above carefully and do not restate them
2. If this section states any KPI, call campaign_metrics and use its measured
   figures - never your own estimate
3. Then find out WHY: insight_forge or interview_agents for the audience's
   reasoning behind the number
4. Mix the tools; do not lean on just one
5. The content must come from the tool results, not from your own marketing
   knowledge

[Formatting warning - must be followed]
- Do not write any heading (#, ##, ###, #### - none of them)
- Do not open with "{section_title}"
- The section title is added by the system
- Write body text directly, using **bold** in place of sub-headings

Begin:
1. First reason (Thought) about which KPI this section rests on
2. Then call a tool (Action) - campaign_metrics for the numbers, the retrieval
   and interview tools for the reasoning behind them
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
You are a concise marketing analyst answering questions about a campaign
assessment you already produced.

[Campaign assessed]
Campaign brief and target audience: {simulation_requirement}

[The campaign assessment report already generated]
{report_content}

[Rules]
1. Answer from the report above wherever you can
2. Answer directly; skip long deliberation
3. Only call a tool when the report does not cover the question
4. For any KPI the report does not already state, call campaign_metrics rather
   than estimating it
5. Keep answers short, clear and well organised

[Available tools] (use only when needed, 1-2 calls at most)
{tools_description}

[Tool call format]
<tool_call>
{{"name": "tool name", "parameters": {{"parameter": "value"}}}}
</tool_call>

[Answer style]
- Short and direct; no essays
- Quote the audience with > when a verbatim makes the point better than a summary
- Lead with the number or the conclusion, then explain why
- When asked "should we launch this", give a straight answer with the evidence"""

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
            "campaign_metrics": {
                "name": "campaign_metrics",
                "description": TOOL_DESC_CAMPAIGN_METRICS,
                "parameters": {}
            },
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
                    "interview_topic": "the interview topic or brief, e.g. 'find out whether the young-professional segment would actually buy after seeing this campaign, and what is holding them back'",
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
            if tool_name == "campaign_metrics":
                # Counted KPIs from the action log - no LLM, no estimation
                from .campaign_metrics import CampaignMetricsService
                return CampaignMetricsService.compute_as_text(self.simulation_id)

            elif tool_name == "insight_forge":
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
                return (
                    f"Unknown tool: {tool_name}. Use one of: campaign_metrics, "
                    "insight_forge, panorama_search, quick_search, interview_agents"
                )
                
        except Exception as e:
            logger.error(t('report.toolExecFailed', toolName=tool_name, error=str(e)))
            return f"Tool call failed: {str(e)}"
    
    # Valid tool names, used to validate the bare-JSON fallback parse
    VALID_TOOL_NAMES = {
        "campaign_metrics", "insight_forge", "panorama_search",
        "quick_search", "interview_agents",
    }

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
        
        # Plan the outline against the real KPIs, so the sections chosen reflect
        # what actually moved rather than a fixed marketing template.
        from .campaign_metrics import CampaignMetricsService
        campaign_metrics = CampaignMetricsService.compute_as_text(self.simulation_id)

        system_prompt = f"{PLAN_SYSTEM_PROMPT}\n\n{get_language_instruction()}"
        user_prompt = PLAN_USER_PROMPT_TEMPLATE.format(
            simulation_requirement=self.simulation_requirement,
            total_nodes=context.get('graph_statistics', {}).get('total_nodes', 0),
            total_edges=context.get('graph_statistics', {}).get('total_edges', 0),
            entity_types=list(context.get('graph_statistics', {}).get('entity_types', {}).keys()),
            total_entities=context.get('total_entities', 0),
            campaign_metrics=campaign_metrics,
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
                title=response.get("title", "Campaign assessment report"),
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
                title="Campaign assessment report",
                summary="Simulated audience reaction to the campaign, measured against marketing KPIs",
                sections=[
                    ReportSection(title="Reach, engagement and sentiment"),
                    ReportSection(title="Virality and segment breakdown"),
                    ReportSection(title="Key objections and recommendations")
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
        all_tools = set(self.VALID_TOOL_NAMES)

        # Report context, used by InsightForge to generate sub-questions
        report_context = f"Section title: {section.title}\nCampaign brief: {self.simulation_requirement}"
        
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
