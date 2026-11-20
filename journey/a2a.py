"""MOI A2A reply projection: timeline parts precede the canonical final text."""
from .client import Failure


def knowledge_answer_id(artifact):
    if (artifact.get("metadata") or {}).get("matrixflow_type") != "knowledge.answer":
        return None
    if any(part.get("kind") == "text" and isinstance(part.get("text"), str) and part["text"].strip()
           for part in artifact.get("parts", [])):
        return artifact.get("artifactId")
    return None


def final_reply(task):
    if task["status"]["state"] != "completed":
        raise Failure("task_not_completed")
    message=task["status"].get("message",{})
    if message.get("role") != "agent":
        raise Failure("missing_agent_reply")
    parts=message.get("parts",[])
    # projectA2ATask appends a2aFinalText after all event/timeline parts.
    # Joining every text part would also include interim, repeated or older text.
    if not parts or parts[-1].get("kind") != "text" or not isinstance(parts[-1].get("text"),str):
        raise Failure("missing_final_reply")
    return parts[-1]["text"]
