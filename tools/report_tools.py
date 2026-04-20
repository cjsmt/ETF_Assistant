import os
from langchain_core.tools import tool

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")

@tool
def generate_report(report_data: str, template_type: str = "weekly_report", role: str = "researcher") -> str:
    """
    Generate a formatted report using a template.
    
    Args:
        report_data: Structured data to fill into the report (text/JSON)
        template_type: Template type (weekly_report, talking_points, approval_form)
        role: User role (researcher, rm, compliance) — determines output format
    """
    template_path = os.path.join(TEMPLATE_DIR, f"{template_type}.md")
    
    if not os.path.exists(template_path):
        return f"Template not found: {template_type}. Available: weekly_report, talking_points, approval_form."

    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()

    return (
        f"=== Report Generated (template={template_type}, role={role}) ===\n\n"
        f"Template loaded from: {template_path}\n\n"
        f"Data provided:\n{report_data}\n\n"
        "Note: In production, this tool fills the template with structured data. "
        "Currently returning raw template + data for LLM to compose the final report."
    )
