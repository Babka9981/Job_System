import re
from dataclasses import dataclass


_SECTION_MARKER = re.compile(r"^\[(Блок|Страница)\s+(\d+)\](?:\s?(.*))$")


@dataclass(frozen=True)
class ResumePreviewBlock:
    label: str
    text: str


def build_resume_preview(text: str) -> tuple[ResumePreviewBlock, ...]:
    """Build a display-only CV outline without normalising the stored text."""
    blocks: list[ResumePreviewBlock] = []
    label = ""
    lines: list[str] = []

    def append_current() -> None:
        if label or lines:
            blocks.append(ResumePreviewBlock(label=label, text="\n".join(lines)))

    for line in text.splitlines():
        marker = _SECTION_MARKER.fullmatch(line)
        if marker:
            append_current()
            label = f"{marker.group(1)} {marker.group(2)}"
            lines = [marker.group(3)] if marker.group(3) else []
        else:
            lines.append(line)
    append_current()
    return tuple(blocks)
