"""Project-wide control priors shared by Concord M1, M2 and M3.

Human behavior supplies computational priors for control.  This contract is
deliberately stage-neutral: each milestone records what deserved attention,
whether extra deliberation was justified, why work continued or stopped, how
the search scope changed, and which prior experience affected the decision.
"""

from pydantic import BaseModel, Field


class ControlPriorDecision(BaseModel):
    """Auditable human-behavior priors applied to one control decision."""

    attention_focus: list[str] = Field(default_factory=list)
    deliberation: str = "routine"
    stopping: str = "continue"
    search_control: str = "maintain"
    experience_refs: list[str] = Field(default_factory=list)


__all__ = ["ControlPriorDecision"]
