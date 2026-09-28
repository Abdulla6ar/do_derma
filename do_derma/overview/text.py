from __future__ import annotations

import html
import re
from typing import Any

import frappe
from frappe import _
from frappe.utils import cstr, format_date, getdate, nowdate, strip_html

from do_derma import api

TEXT_LIMIT = 400
BLOCK_TAGS = re.compile(r"<\s*(?:br|/p|/div|/li|/h\d)\b[^>]*>", re.IGNORECASE)
WHITESPACE = re.compile(r"\s+")
# A full stop only ends a sentence when the next word starts one - "Dr. Salman" and "e.g. acne"
# must not cut the complaint short.
SENTENCE_END = re.compile(r"[.!?](?=\s+[A-Z0-9؀-ۿ])")


def existing_fields(doctype: str, fields: list[str]) -> list[str]:
	meta = frappe.get_meta(doctype)
	return [field for field in fields if field in api.STANDARD_DB_FIELDS or meta.has_field(field)]


def clean(value: Any, limit: int = TEXT_LIMIT) -> str:
	"""Plain text for the drawer: tags out, entities decoded, whitespace collapsed, clipped."""
	text = cstr(value)
	if not text.strip():
		return ""
	text = html.unescape(strip_html(BLOCK_TAGS.sub(" ", text)))
	return clip(WHITESPACE.sub(" ", text).strip(), limit)


def first_sentence(value: Any) -> str:
	text = cstr(value)
	if not text.strip():
		return ""
	text = html.unescape(strip_html(BLOCK_TAGS.sub("\n", text)))
	line = next((line.strip() for line in text.splitlines() if line.strip()), "")
	for match in SENTENCE_END.finditer(line):
		if match.end() >= 20:
			line = line[: match.end()]
			break
	return clip(WHITESPACE.sub(" ", line).strip())


def clip(text: str, limit: int = TEXT_LIMIT) -> str:
	if len(text) <= limit:
		return text
	cut = text[:limit]
	# Break on a word when one is near the end, so the clipped text never ends mid-word.
	space = cut.rfind(" ")
	if space > limit * 0.6:
		cut = cut[:space]
	return cut.rstrip(" ,;:·-—") + "…"


def unique(values) -> list[str]:
	seen: list[str] = []
	for value in values:
		text = cstr(value).strip()
		if text and text not in seen:
			seen.append(text)
	return seen


def join_unique(values, separator: str = ", ") -> str:
	return separator.join(unique(values))


def listed(values: list[str], limit: int, separator: str = ", ") -> str:
	if len(values) <= limit:
		return separator.join(values)
	return separator.join(values[:limit]) + " " + _("+{0} more").format(len(values) - limit)


def short_date(value) -> str:
	date = getdate(value)
	return format_date(date, "d MMM" if date.year == getdate(nowdate()).year else "d MMM yyyy")
