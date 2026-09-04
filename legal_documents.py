"""Immutable, server-side registry for published Spyon legal documents.

The versioned DOCX files in ``docs/legal/current`` are the single source for
the current Russian text. Historical definitions remain addressable so an
existing acceptance always points to the exact document it recorded.
The SHA-256 recorded for an acceptance is calculated from the exact versioned
DOCX byte stream.  A browser never supplies a document number, version or hash.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from html import escape
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from markupsafe import Markup


ROOT = Path(__file__).resolve().parent
DOCX_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
LEGAL_DOCUMENT_TYPE_TITLES = {
    "offer": "Публичная оферта SPYON",
    "tariff_policy": "Тарифная политика и условия обслуживания SPYON",
    "acceptable_use": "Правила допустимого использования SPYON",
    "personal_data_consent": "Согласие на сбор и обработку персональных данных",
    "privacy": "Политика конфиденциальности SPYON",
}
LEGAL_DOCUMENT_TYPES = frozenset({
    *LEGAL_DOCUMENT_TYPE_TITLES,
    # Historical lifecycle types are retained for immutable audit evidence.
    "terms",
    "cookies",
})
OFFER_ACCEPTANCE_TEXT = (
    "Я подтверждаю, что уполномочен действовать от имени указанной компании, "
    "ознакомился и принимаю условия Публичной оферты SPYON, Тарифной политики "
    "и Правил допустимого использования."
)
PRIVACY_ACCEPTANCE_TEXT = (
    "Я ознакомился(ась) с Политикой конфиденциальности SPYON и даю согласие "
    "на сбор и обработку моих персональных данных в соответствии с Согласием "
    "на сбор и обработку персональных данных."
)
LEGACY_OFFER_ACCEPTANCE_TEXT = (
    "Я подтверждаю, что уполномочен действовать от имени указанной компании, "
    "ознакомился и принимаю условия Публичной оферты Spyon."
)
LEGACY_PRIVACY_ACCEPTANCE_TEXT = (
    "Я ознакомился(ась) с Политикой конфиденциальности Spyon и даю согласие "
    "на сбор и обработку моих персональных данных на изложенных в ней условиях."
)


@dataclass(frozen=True)
class LegalDefinition:
    document_type: str
    number: str
    version: str
    title: str
    filename: str
    route_slug: str
    pdf_filename: str
    effective_at: str
    acceptance_text: str
    expected_sha256: str

    @property
    def source_path(self) -> Path:
        return ROOT / "docs" / "legal" / self.filename

    @property
    def pdf_path(self) -> Path:
        return ROOT / "static" / "legal" / self.pdf_filename


class LegalDocumentRegistry:
    """Whitelist of published documents and versions.

    Adding a future revision is intentionally explicit: retain the current
    definition and add another version entry instead of replacing a file.
    """

    _definitions = (
        LegalDefinition(
            "offer", "SPYON-OF-001", "1.0", "Публичная оферта Spyon",
            "Spyon_Публичная_оферта_v1.0_для_согласования.docx",
            "offer", "offer/1.0.pdf", "2026-08-31T00:00:00+00:00",
            LEGACY_OFFER_ACCEPTANCE_TEXT,
            "64ec228d143b624438d31ba13519efda16e3d183e4d4ca280351e6ed03e567c0",
        ),
        LegalDefinition(
            "privacy", "SPYON-PD-001", "1.0",
            "Политика конфиденциальности Spyon",
            "Spyon_Политика_конфиденциальности_и_согласие_v1.0_для_согласования.docx",
            "privacy", "privacy/1.0.pdf", "2026-08-31T00:00:00+00:00",
            LEGACY_PRIVACY_ACCEPTANCE_TEXT,
            "90da2b84982c11ae60318a286845ea594e76f9dfb58039bc3a560544b1fabb39",
        ),
        LegalDefinition(
            "offer", "SPYON-OF-001", "04.09.2026", "Публичная оферта SPYON",
            "current/public-offer.docx", "offer", "current/public-offer.pdf",
            "2026-09-04T00:00:00+00:00", OFFER_ACCEPTANCE_TEXT,
            "f1326386dfedb21cb71028373183416ae3e32de0edf26b31071f1b4eec14581e",
        ),
        LegalDefinition(
            "tariff_policy", "SPYON-TP-001", "04.09.2026",
            "Тарифная политика и условия обслуживания SPYON",
            "current/tariff-policy.docx", "tariff-policy",
            "current/tariff-policy.pdf", "2026-09-04T00:00:00+00:00",
            OFFER_ACCEPTANCE_TEXT,
            "6e365017d496c28708e34bf89afb0d244b30e9c162f38c13960c8c933a22af5a",
        ),
        LegalDefinition(
            "acceptable_use", "SPYON-AU-001", "04.09.2026",
            "Правила допустимого использования SPYON",
            "current/acceptable-use.docx", "acceptable-use",
            "current/acceptable-use.pdf", "2026-09-04T00:00:00+00:00",
            OFFER_ACCEPTANCE_TEXT,
            "18a2b3b0a3c1b170bee140240af13d6a78762312987cd831784110f341f494e9",
        ),
        LegalDefinition(
            "personal_data_consent", "SPYON-PC-001", "04.09.2026",
            "Согласие на сбор и обработку персональных данных",
            "current/personal-data-consent.docx", "personal-data-consent",
            "current/personal-data-consent.pdf", "2026-09-04T00:00:00+00:00",
            PRIVACY_ACCEPTANCE_TEXT,
            "7c6a44ceb0eb12c3ed619d9b9399bba9a0243844ff7928c0d417583e3a507415",
        ),
        LegalDefinition(
            "privacy", "SPYON-PR-001", "04.09.2026",
            "Политика конфиденциальности SPYON",
            "current/privacy-policy.docx", "privacy",
            "current/privacy-policy.pdf", "2026-09-04T00:00:00+00:00",
            PRIVACY_ACCEPTANCE_TEXT,
            "c5c11e99b144e5967705c75ef0cc259462832ee2323d6c7dac5a5b5a42a0076f",
        ),
    )

    def __init__(self) -> None:
        self._by_key = {(item.document_type, item.version): item for item in self._definitions}

    def definitions(self) -> tuple[LegalDefinition, ...]:
        return self._definitions

    def get(self, document_type: str, version: str | None = None) -> LegalDefinition | None:
        normalized_type = str(document_type or "").strip().casefold()
        slug_match = next(
            (item for item in reversed(self._definitions) if item.route_slug == normalized_type),
            None,
        )
        if slug_match is not None:
            normalized_type = slug_match.document_type
        if version is not None:
            return self._by_key.get((normalized_type, str(version).strip()))
        matches = [item for item in self._definitions if item.document_type == normalized_type]
        return matches[-1] if matches else None

    def current_documents(self) -> list[LegalDefinition]:
        return [
            self.get(document_type)
            for document_type in LEGAL_DOCUMENT_TYPE_TITLES
        ]  # type: ignore[list-item]

    @staticmethod
    def _paragraph_text(node: ET.Element) -> str:
        result: list[str] = []
        for child in node.iter():
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "t" and child.text:
                result.append(child.text)
            elif tag == "tab":
                result.append("\t")
            elif tag in {"br", "cr"}:
                result.append("\n")
        return "".join(result).strip()

    @staticmethod
    def _style_name(node: ET.Element) -> str:
        style = node.find("./w:pPr/w:pStyle", DOCX_NS)
        return str(style.get("{%(w)s}val" % DOCX_NS) or "") if style is not None else ""

    @staticmethod
    def _is_list(node: ET.Element) -> bool:
        return node.find("./w:pPr/w:numPr", DOCX_NS) is not None

    def blocks(self, definition: LegalDefinition) -> list[dict[str, Any]]:
        """Read the DOCX body without editing or paraphrasing its text."""
        with ZipFile(definition.source_path) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
        body = root.find("w:body", DOCX_NS)
        if body is None:
            raise ValueError("В юридическом DOCX отсутствует тело документа.")
        values: list[dict[str, Any]] = []
        for node in body:
            tag = node.tag.rsplit("}", 1)[-1]
            if tag == "p":
                style = self._style_name(node).casefold()
                is_heading = "heading" in style or "заголов" in style
                lines = [line.strip() for line in self._paragraph_text(node).splitlines()]
                for text in filter(None, lines):
                    manual_list = text.startswith(("- ", "• "))
                    values.append({
                        "kind": (
                            "heading" if is_heading else
                            "list" if self._is_list(node) or manual_list else
                            "paragraph"
                        ),
                        "text": text[2:].strip() if manual_list else text,
                    })
            elif tag == "tbl":
                rows: list[list[str]] = []
                for row in node.findall("./w:tr", DOCX_NS):
                    cells = [self._paragraph_text(cell) for cell in row.findall("./w:tc", DOCX_NS)]
                    if any(cells):
                        rows.append(cells)
                if rows:
                    values.append({"kind": "table", "rows": rows})
        return values

    def sha256(self, definition: LegalDefinition) -> str:
        digest = sha256(definition.source_path.read_bytes()).hexdigest()

        if digest != definition.expected_sha256:
            raise RuntimeError(
                "Versioned legal document integrity check failed."
            )

        return digest

    def metadata(self, definition: LegalDefinition) -> dict[str, Any]:
        return {
            "type": definition.document_type,
            "number": definition.number,
            "version": definition.version,
            "title": definition.title,
            "slug": definition.route_slug,
            "effective_at": definition.effective_at,
            "sha256": self.sha256(definition),
            "pdf_available": definition.pdf_path.is_file(),
        }

    def acceptance_records(
        self, *, ip_address: str, user_agent: str, locale: str, source: str = "registration"
    ) -> list[dict[str, str]]:
        normalized_locale = str(locale or "ru").casefold()
        if normalized_locale not in {"ru", "kk", "en"}:
            normalized_locale = "ru"
        return [
            {
                "document_type": item.document_type,
                "document_number": item.number,
                "document_version": item.version,
                "document_sha256": self.sha256(item),
                "accepted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "ip_address": str(ip_address or "")[:128],
                "user_agent": str(user_agent or "")[:1024],
                "locale": normalized_locale,
                "acceptance_text": item.acceptance_text,
                "source": source,
            }
            for item in self.current_documents()
        ]

    def html(self, definition: LegalDefinition) -> Markup:
        """Render semantic, escaped HTML from the immutable DOCX source."""
        result: list[str] = []
        list_open = False
        for block in self.blocks(definition):
            kind = str(block["kind"])
            if kind == "list":
                if not list_open:
                    result.append("<ul>")
                    list_open = True
                result.append(f"<li>{escape(str(block['text']))}</li>")
                continue
            if list_open:
                result.append("</ul>")
                list_open = False
            if kind == "heading":
                result.append(f"<h2>{escape(str(block['text']))}</h2>")
            elif kind == "table":
                result.append("<div class=\"legal-table-wrap\"><table><tbody>")
                for row in block["rows"]:
                    result.append("<tr>" + "".join(f"<td>{escape(str(cell))}</td>" for cell in row) + "</tr>")
                result.append("</tbody></table></div>")
            else:
                result.append(f"<p>{escape(str(block['text']))}</p>")
        if list_open:
            result.append("</ul>")
        return Markup("\n".join(result))

    def accepted_documents_for_user(self, conn: Any, user_id: int, tenant_id: int | None) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for definition in self.current_documents():
            row = conn.execute(
                """SELECT accepted_at FROM legal_acceptances
                   WHERE user_id=? AND document_type=? AND document_version=?
                   ORDER BY accepted_at DESC LIMIT 1""",
                (int(user_id), definition.document_type, definition.version),
            ).fetchone()
            item = self.metadata(definition)
            item["accepted_at"] = row["accepted_at"] if row else None
            item["tenant_id"] = tenant_id
            result.append(item)
        return result


LEGAL_DOCUMENTS = LegalDocumentRegistry()
