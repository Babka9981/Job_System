import re
from dataclasses import dataclass

from jobs.vacancies.services import sanitize_source_text


MARKER = re.compile(r"(?im)^(?:вакансия|vacancy|job)\s*(?:#\d+)?\s*:\s*(?P<title>[^\n]+)\s*$")
COMPANY = re.compile(r"(?im)^(?:компания|company)\s*:\s*(?P<company>[^\n]+)\s*$")


@dataclass(frozen=True)
class ParsedVacancy:
    title: str
    company: str
    description: str


@dataclass(frozen=True)
class ParseResult:
    vacancies: tuple[ParsedVacancy, ...]
    needs_review: bool = False
    reason: str = ""


def parse_post(text):
    cleaned = sanitize_source_text(text or "")
    markers = list(MARKER.finditer(cleaned))
    if not markers:
        return ParseResult((), True, "Нет однозначных маркеров «Vacancy:» или «Вакансия:».")
    vacancies = []
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(cleaned)
        block = cleaned[marker.start():end].strip(" \n-")
        title = sanitize_source_text(marker.group("title"))
        company_match = COMPANY.search(block)
        company = sanitize_source_text(company_match.group("company")) if company_match else ""
        if not title:
            return ParseResult((), True, "Не удалось однозначно определить название вакансии.")
        vacancies.append(ParsedVacancy(title, company, block))
    return ParseResult(tuple(vacancies))

