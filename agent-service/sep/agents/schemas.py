"""Typed outputs of every agent. Pydantic validates what the LLM returns."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Confidence = Literal["high", "medium", "low"]
Verdict = Literal["applies", "applies_with_changes", "not_applicable", "unknown"]


class MissingInfo(BaseModel):
    question: str = Field(description="Question to ask the customer")
    why: str = Field(description="Why the answer matters for the investigation")


class IntakeResult(BaseModel):
    summary: str = Field(description="One or two sentence technical restatement of the problem")
    module_code: str = Field(description="Most likely affected module code from the provided list")
    affected_integrations: list[str] = Field(default_factory=list, description="Integration codes involved")
    symptoms: list[str] = Field(default_factory=list)
    error_codes: list[str] = Field(default_factory=list, description="Error codes / messages quoted by the customer")
    customer_goal: str = Field(description="What the customer is trying to achieve")
    suspected_trigger: str = Field(default="", description="Recent change that may have triggered it (upgrade, config change, ...) or empty")
    severity_suggestion: Literal["S1", "S2", "S3", "S4"] = "S3"
    severity_reason: str = ""
    missing_info: list[MissingInfo] = Field(default_factory=list, description="Max 4 most valuable missing facts")
    clarification_message: str = Field(default="", description="Polite message to the customer asking for the missing info")
    search_queries: list[str] = Field(default_factory=list, description="2-4 search queries for historical knowledge")


class Candidate(BaseModel):
    type: Literal["case", "solution"]
    number: str = Field(description="CS-#### or SOL-###")
    relevance: Confidence
    why_it_matches: str


class RetrievalResult(BaseModel):
    candidates: list[Candidate] = Field(default_factory=list)
    notes: str = ""


class ApplicabilityItem(BaseModel):
    number: str
    verdict: Verdict
    reason: str = Field(description="Short, factual reason referring to versions, modules, track record, release changes")
    adaptations: str = Field(default="", description="What must be changed for this case, if anything")


class ApplicabilityResult(BaseModel):
    items: list[ApplicabilityItem] = Field(default_factory=list)


class Hypothesis(BaseModel):
    title: str
    likelihood: Confidence
    reasoning: str
    citations: list[str] = Field(default_factory=list, description="Case/solution numbers supporting it")
    confirm_by: str = Field(description="Concrete check that confirms or rules it out")


class NextStep(BaseModel):
    action: str = Field(description="Concrete check the engineer should do next")
    why: str
    expected_finding: str = Field(description="What the engineer will see if the leading hypothesis is right")
    citations: list[str] = Field(default_factory=list)


class InvestigationPlan(BaseModel):
    assessment: str = Field(description="2-4 sentences: what is most likely going on and why")
    no_relevant_history: bool = Field(default=False, description="True if history does not contain a relevant solution")
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    next_steps: list[NextStep] = Field(default_factory=list)


class SolutionDraft(BaseModel):
    title: str
    issue: str = Field(description="KCS Issue: symptoms in the customer's words")
    environment_conditions: str = Field(description="KCS Environment: versions, modules, integrations where it applies")
    cause: str
    resolution_steps: str = Field(description="Numbered, concrete steps")
    verification_steps: str = ""
    min_version: str | None = None
    max_version: str | None = None
    module_codes: list[str] = Field(default_factory=list)
    root_cause_code: str | None = None


class KnowledgeDecision(BaseModel):
    action: Literal["link", "update", "create"] = Field(
        description="link = existing solution fits as-is; update = existing solution needs extension; create = new knowledge")
    target_solution: str | None = Field(default=None, description="SOL-### for link/update")
    reasoning: str
    changes_summary: str = Field(default="", description="For update/create: what knowledge is added")
    solution: SolutionDraft | None = Field(default=None, description="Full proposed solution text for update/create")


class ProblemProposal(BaseModel):
    title: str
    description: str
    suspected_cause: str
    workaround: str = ""
    case_numbers: list[str]
    related_release_change: str = ""
    confidence: Confidence


class ProblemProposals(BaseModel):
    problems: list[ProblemProposal] = Field(default_factory=list)


class AskAnswer(BaseModel):
    answer: str
    citations: list[str] = Field(default_factory=list)
