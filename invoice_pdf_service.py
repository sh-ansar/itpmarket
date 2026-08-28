from __future__ import annotations

import hashlib
import os
import re
import tempfile
from datetime import datetime
from decimal import (
    Decimal,
    InvalidOperation,
    ROUND_HALF_UP,
)
from html import escape
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.utils import ImageReader
from reportlab.lib.enums import (
    TA_CENTER,
    TA_LEFT,
    TA_RIGHT,
)
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import (
    ParagraphStyle,
    getSampleStyleSheet,
)
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


class InvoicePDFError(ValueError):
    pass


FONT_REGULAR_NAME = "SpyonInvoiceRegular"
FONT_BOLD_NAME = "SpyonInvoiceBold"


def _money(value: Any) -> Decimal:
    try:
        result = Decimal(
            str(value)
        )
    except (
        InvalidOperation,
        TypeError,
        ValueError,
    ) as exc:
        raise InvoicePDFError(
            "Invalid monetary value."
        ) from exc

    if not result.is_finite():
        raise InvoicePDFError(
            "Invalid monetary value."
        )

    return result.quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )


def format_money(value: Any) -> str:
    amount = _money(value)

    text = (
        f"{amount:,.2f}"
        .replace(",", " ")
    )

    return text.replace(
        ".",
        ",",
    )


def _plural(
    number: int,
    forms: tuple[str, str, str],
) -> str:
    number = abs(number) % 100

    if 11 <= number <= 19:
        return forms[2]

    tail = number % 10

    if tail == 1:
        return forms[0]

    if 2 <= tail <= 4:
        return forms[1]

    return forms[2]


def _triad_words(
    number: int,
    feminine: bool = False,
) -> list[str]:
    if not 0 <= number <= 999:
        raise ValueError(
            "triad must be between 0 and 999"
        )

    hundreds = (
        "",
        "\u0441\u0442\u043e",
        "\u0434\u0432\u0435\u0441\u0442\u0438",
        "\u0442\u0440\u0438\u0441\u0442\u0430",
        "\u0447\u0435\u0442\u044b\u0440\u0435\u0441\u0442\u0430",
        "\u043f\u044f\u0442\u044c\u0441\u043e\u0442",
        "\u0448\u0435\u0441\u0442\u044c\u0441\u043e\u0442",
        "\u0441\u0435\u043c\u044c\u0441\u043e\u0442",
        "\u0432\u043e\u0441\u0435\u043c\u044c\u0441\u043e\u0442",
        "\u0434\u0435\u0432\u044f\u0442\u044c\u0441\u043e\u0442",
    )

    tens = (
        "",
        "",
        "\u0434\u0432\u0430\u0434\u0446\u0430\u0442\u044c",
        "\u0442\u0440\u0438\u0434\u0446\u0430\u0442\u044c",
        "\u0441\u043e\u0440\u043e\u043a",
        "\u043f\u044f\u0442\u044c\u0434\u0435\u0441\u044f\u0442",
        "\u0448\u0435\u0441\u0442\u044c\u0434\u0435\u0441\u044f\u0442",
        "\u0441\u0435\u043c\u044c\u0434\u0435\u0441\u044f\u0442",
        "\u0432\u043e\u0441\u0435\u043c\u044c\u0434\u0435\u0441\u044f\u0442",
        "\u0434\u0435\u0432\u044f\u043d\u043e\u0441\u0442\u043e",
    )

    teens = (
        "\u0434\u0435\u0441\u044f\u0442\u044c",
        "\u043e\u0434\u0438\u043d\u043d\u0430\u0434\u0446\u0430\u0442\u044c",
        "\u0434\u0432\u0435\u043d\u0430\u0434\u0446\u0430\u0442\u044c",
        "\u0442\u0440\u0438\u043d\u0430\u0434\u0446\u0430\u0442\u044c",
        "\u0447\u0435\u0442\u044b\u0440\u043d\u0430\u0434\u0446\u0430\u0442\u044c",
        "\u043f\u044f\u0442\u043d\u0430\u0434\u0446\u0430\u0442\u044c",
        "\u0448\u0435\u0441\u0442\u043d\u0430\u0434\u0446\u0430\u0442\u044c",
        "\u0441\u0435\u043c\u043d\u0430\u0434\u0446\u0430\u0442\u044c",
        "\u0432\u043e\u0441\u0435\u043c\u043d\u0430\u0434\u0446\u0430\u0442\u044c",
        "\u0434\u0435\u0432\u044f\u0442\u043d\u0430\u0434\u0446\u0430\u0442\u044c",
    )

    units_male = (
        "",
        "\u043e\u0434\u0438\u043d",
        "\u0434\u0432\u0430",
        "\u0442\u0440\u0438",
        "\u0447\u0435\u0442\u044b\u0440\u0435",
        "\u043f\u044f\u0442\u044c",
        "\u0448\u0435\u0441\u0442\u044c",
        "\u0441\u0435\u043c\u044c",
        "\u0432\u043e\u0441\u0435\u043c\u044c",
        "\u0434\u0435\u0432\u044f\u0442\u044c",
    )

    units_female = (
        "",
        "\u043e\u0434\u043d\u0430",
        "\u0434\u0432\u0435",
        "\u0442\u0440\u0438",
        "\u0447\u0435\u0442\u044b\u0440\u0435",
        "\u043f\u044f\u0442\u044c",
        "\u0448\u0435\u0441\u0442\u044c",
        "\u0441\u0435\u043c\u044c",
        "\u0432\u043e\u0441\u0435\u043c\u044c",
        "\u0434\u0435\u0432\u044f\u0442\u044c",
    )

    words: list[str] = []

    hundred = number // 100

    if hundred:
        words.append(
            hundreds[hundred]
        )

    remainder = number % 100

    if 10 <= remainder <= 19:
        words.append(
            teens[remainder - 10]
        )
        return words

    ten = remainder // 10

    if ten:
        words.append(
            tens[ten]
        )

    unit = remainder % 10

    if unit:
        units = (
            units_female
            if feminine
            else units_male
        )

        words.append(
            units[unit]
        )

    return words


def amount_in_words(
    value: Any,
    currency: str = "KZT",
) -> str:
    amount = _money(value)

    if amount < 0:
        raise InvoicePDFError(
            "Negative invoice amount is not supported."
        )

    whole = int(amount)

    fraction = int(
        (
            amount
            - Decimal(whole)
        )
        * 100
    )

    groups = (
        (
            1_000_000_000,
            False,
            (
                "\u043c\u0438\u043b\u043b\u0438\u0430\u0440\u0434",
                "\u043c\u0438\u043b\u043b\u0438\u0430\u0440\u0434\u0430",
                "\u043c\u0438\u043b\u043b\u0438\u0430\u0440\u0434\u043e\u0432",
            ),
        ),
        (
            1_000_000,
            False,
            (
                "\u043c\u0438\u043b\u043b\u0438\u043e\u043d",
                "\u043c\u0438\u043b\u043b\u0438\u043e\u043d\u0430",
                "\u043c\u0438\u043b\u043b\u0438\u043e\u043d\u043e\u0432",
            ),
        ),
        (
            1_000,
            True,
            (
                "\u0442\u044b\u0441\u044f\u0447\u0430",
                "\u0442\u044b\u0441\u044f\u0447\u0438",
                "\u0442\u044b\u0441\u044f\u0447",
            ),
        ),
    )

    words: list[str] = []

    remainder = whole

    for divider, feminine, forms in groups:
        group = remainder // divider

        if not group:
            continue

        words.extend(
            _triad_words(
                group,
                feminine=feminine,
            )
        )

        words.append(
            _plural(
                group,
                forms,
            )
        )

        remainder %= divider

    if remainder:
        words.extend(
            _triad_words(
                remainder
            )
        )

    if not words:
        words.append(
            "\u043d\u043e\u043b\u044c"
        )

    currency_code = str(
        currency or "KZT"
    ).strip().upper()

    if currency_code == "KZT":
        suffix = (
            "\u0442\u0435\u043d\u0433\u0435 "
            f"{fraction:02d} "
            "\u0442\u0438\u044b\u043d"
        )
    else:
        suffix = (
            f"{currency_code} "
            f"{fraction:02d}/100"
        )

    result = (
        " ".join(words)
        + " "
        + suffix
    )

    return (
        result[0].upper()
        + result[1:]
    )


def _font_pair() -> tuple[Path, Path]:
    env_regular = str(
        os.getenv(
            "SPYON_PDF_FONT_REGULAR",
            "",
        )
        or ""
    ).strip()

    env_bold = str(
        os.getenv(
            "SPYON_PDF_FONT_BOLD",
            "",
        )
        or ""
    ).strip()

    candidates = []

    if env_regular:
        candidates.append(
            (
                Path(env_regular),
                Path(
                    env_bold
                    or env_regular
                ),
            )
        )

    candidates.extend(
        [
            (
                Path(
                    "C:/Windows/Fonts/arial.ttf"
                ),
                Path(
                    "C:/Windows/Fonts/arialbd.ttf"
                ),
            ),
            (
                Path(
                    "C:/Windows/Fonts/calibri.ttf"
                ),
                Path(
                    "C:/Windows/Fonts/calibrib.ttf"
                ),
            ),
            (
                Path(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
                ),
                Path(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
                ),
            ),
            (
                Path(
                    "/System/Library/Fonts/Supplemental/Arial.ttf"
                ),
                Path(
                    "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
                ),
            ),
        ]
    )

    for regular, bold in candidates:
        if regular.is_file():
            if not bold.is_file():
                bold = regular

            return (
                regular,
                bold,
            )

    raise InvoicePDFError(
        "Unicode PDF font was not found."
    )


def _register_fonts() -> tuple[str, str]:
    regular_path, bold_path = (
        _font_pair()
    )

    registered = set(
        pdfmetrics.getRegisteredFontNames()
    )

    if FONT_REGULAR_NAME not in registered:
        pdfmetrics.registerFont(
            TTFont(
                FONT_REGULAR_NAME,
                str(regular_path),
            )
        )

    if FONT_BOLD_NAME not in registered:
        pdfmetrics.registerFont(
            TTFont(
                FONT_BOLD_NAME,
                str(bold_path),
            )
        )

    pdfmetrics.registerFontFamily(
        "SpyonInvoice",
        normal=FONT_REGULAR_NAME,
        bold=FONT_BOLD_NAME,
    )

    return (
        FONT_REGULAR_NAME,
        FONT_BOLD_NAME,
    )


def _format_date(value: Any) -> str:
    text = str(
        value or ""
    ).strip()

    if not text:
        return "-"

    try:
        moment = datetime.fromisoformat(
            text.replace(
                "Z",
                "+00:00",
            )
        )
    except ValueError:
        return text

    return moment.strftime(
        "%d.%m.%Y"
    )


def _safe_filename(
    invoice_number: str,
) -> str:
    value = re.sub(
        r"[^A-Za-z0-9._-]+",
        "-",
        str(invoice_number),
    ).strip(
        ".-_"
    )

    if not value:
        value = "invoice"

    return value[:96]


def _invariant_canvas(
    *args: Any,
    **kwargs: Any,
) -> canvas.Canvas:
    kwargs["invariant"] = 1

    return canvas.Canvas(
        *args,
        **kwargs,
    )


class InvoicePDFService:
    def __init__(
        self,
        output_dir: Path | str = (
            Path("output")
            / "invoices"
        ),
        logo_path: Path | str | None = None,
        stamp_path: Path | str | None = None,
    ) -> None:
        self.output_dir = Path(
            output_dir
        )

        self.logo_path = Path(
            logo_path
            or os.getenv(
                "SPYON_INVOICE_LOGO_PATH",
                "static/billing/itp_mining_logo.png",
            )
        )

        self.stamp_path = Path(
            stamp_path
            or os.getenv(
                "SPYON_INVOICE_STAMP_PATH",
                "data/billing-assets/itp_mining_stamp.png",
            )
        )

    @staticmethod
    def _styles() -> dict[str, ParagraphStyle]:
        regular, bold = (
            _register_fonts()
        )

        sample = getSampleStyleSheet()

        return {
            "body": ParagraphStyle(
                "InvoiceBody",
                parent=sample["Normal"],
                fontName=regular,
                fontSize=9,
                leading=12,
                textColor=colors.HexColor(
                    "#181818"
                ),
            ),
            "small": ParagraphStyle(
                "InvoiceSmall",
                parent=sample["Normal"],
                fontName=regular,
                fontSize=7.5,
                leading=9.5,
                textColor=colors.HexColor(
                    "#555555"
                ),
            ),
            "title": ParagraphStyle(
                "InvoiceTitle",
                parent=sample["Normal"],
                fontName=bold,
                fontSize=16,
                leading=20,
                textColor=colors.HexColor(
                    "#111111"
                ),
                alignment=TA_LEFT,
            ),
            "brand": ParagraphStyle(
                "InvoiceBrand",
                parent=sample["Normal"],
                fontName=bold,
                fontSize=19,
                leading=21,
                textColor=colors.HexColor(
                    "#111111"
                ),
            ),
            "label": ParagraphStyle(
                "InvoiceLabel",
                parent=sample["Normal"],
                fontName=bold,
                fontSize=8.5,
                leading=11,
            ),
            "right": ParagraphStyle(
                "InvoiceRight",
                parent=sample["Normal"],
                fontName=regular,
                fontSize=9,
                leading=11,
                alignment=TA_RIGHT,
            ),
            "right_bold": ParagraphStyle(
                "InvoiceRightBold",
                parent=sample["Normal"],
                fontName=bold,
                fontSize=10,
                leading=12,
                alignment=TA_RIGHT,
            ),
            "center_small": ParagraphStyle(
                "InvoiceCenterSmall",
                parent=sample["Normal"],
                fontName=regular,
                fontSize=7.5,
                leading=9,
                alignment=TA_CENTER,
            ),
        }

    @staticmethod
    def _paragraph(
        value: Any,
        style: ParagraphStyle,
    ) -> Paragraph:
        text = escape(
            str(
                value
                if value not in (
                    None,
                    "",
                )
                else "-"
            )
        )

        text = text.replace(
            "\n",
            "<br/>",
        )

        return Paragraph(
            text,
            style,
        )

    @staticmethod
    def _line_description(
        item: dict[str, Any],
        seller: dict[str, Any],
    ) -> str:
        explicit = str(
            item.get("description")
            or item.get("service_name")
            or ""
        ).strip()

        if explicit:
            return explicit

        service_name = str(
            seller.get("service_name")
            or (
                "\u0410\u0431\u043e\u043d\u0435\u043d\u0442\u0441\u043a\u0430\u044f "
                "\u043f\u043b\u0430\u0442\u0430 "
                "\u0437\u0430 "
                "\u0438\u0441\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u043d\u0438\u0435 "
                "\u043f\u0440\u043e\u0433\u0440\u0430\u043c\u043c\u043d\u043e\u0433\u043e "
                "\u043f\u0440\u043e\u0434\u0443\u043a\u0442\u0430 Spyon"
            )
        ).strip()

        plan_name = str(
            item.get("plan_name")
            or ""
        ).strip()

        if plan_name:
            return (
                service_name
                + " - "
                + plan_name
            )

        return service_name

    def generate(
        self,
        invoice: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(invoice, dict):
            raise InvoicePDFError(
                "Invoice snapshot must be a dictionary."
            )

        invoice_number = str(
            invoice.get("invoice_number")
            or ""
        ).strip()

        if not invoice_number:
            raise InvoicePDFError(
                "Invoice number is required."
            )

        seller = invoice.get(
            "seller_snapshot"
        )
        buyer = invoice.get(
            "buyer_snapshot"
        )
        line_items = invoice.get(
            "line_items"
        )

        if not isinstance(seller, dict):
            raise InvoicePDFError(
                "Seller snapshot is required."
            )

        if not isinstance(buyer, dict):
            raise InvoicePDFError(
                "Buyer snapshot is required."
            )

        if (
            not isinstance(line_items, list)
            or not line_items
        ):
            raise InvoicePDFError(
                "Invoice line items are required."
            )

        if not str(
            seller.get("name") or ""
        ).strip():
            raise InvoicePDFError(
                "Seller name is required."
            )

        if not str(
            buyer.get("name") or ""
        ).strip():
            raise InvoicePDFError(
                "Buyer name is required."
            )

        if not self.logo_path.is_file():
            raise InvoicePDFError(
                "ITP Mining logo was not found."
            )

        if not self.stamp_path.is_file():
            raise InvoicePDFError(
                "ITP Mining stamp was not found."
            )

        regular, bold = _register_fonts()

        currency = str(
            invoice.get("currency")
            or "KZT"
        ).strip().upper()

        subtotal = _money(
            invoice.get(
                "subtotal_amount",
                0,
            )
        )
        vat = _money(
            invoice.get(
                "vat_amount",
                0,
            )
        )
        total = _money(
            invoice.get(
                "total_amount",
                0,
            )
        )

        months = invoice.get(
            "months_count",
            1,
        )

        issued_text = str(
            invoice.get("issued_at")
            or ""
        )

        month_names = (
            "",
            "\u044f\u043d\u0432\u0430\u0440\u044f",
            "\u0444\u0435\u0432\u0440\u0430\u043b\u044f",
            "\u043c\u0430\u0440\u0442\u0430",
            "\u0430\u043f\u0440\u0435\u043b\u044f",
            "\u043c\u0430\u044f",
            "\u0438\u044e\u043d\u044f",
            "\u0438\u044e\u043b\u044f",
            "\u0430\u0432\u0433\u0443\u0441\u0442\u0430",
            "\u0441\u0435\u043d\u0442\u044f\u0431\u0440\u044f",
            "\u043e\u043a\u0442\u044f\u0431\u0440\u044f",
            "\u043d\u043e\u044f\u0431\u0440\u044f",
            "\u0434\u0435\u043a\u0430\u0431\u0440\u044f",
        )

        try:
            issued_moment = datetime.fromisoformat(
                issued_text.replace(
                    "Z",
                    "+00:00",
                )
            )

            issued = (
                f"{issued_moment.day} "
                f"{month_names[issued_moment.month]} "
                f"{issued_moment.year} \u0433."
            )
        except ValueError:
            issued = (
                issued_text
                or "-"
            )

        styles = {
            "normal": ParagraphStyle(
                "ITPInvoiceNormal",
                fontName=regular,
                fontSize=7.2,
                leading=8.5,
                textColor=colors.black,
            ),
            "bold": ParagraphStyle(
                "ITPInvoiceBold",
                fontName=bold,
                fontSize=7.2,
                leading=8.5,
                textColor=colors.black,
            ),
            "small_center": ParagraphStyle(
                "ITPInvoiceSmallCenter",
                fontName=regular,
                fontSize=6.2,
                leading=7.1,
                alignment=TA_CENTER,
                textColor=colors.black,
            ),
            "cell_center": ParagraphStyle(
                "ITPInvoiceCellCenter",
                fontName=regular,
                fontSize=6.4,
                leading=7.3,
                alignment=TA_CENTER,
                textColor=colors.black,
            ),
            "cell_center_bold":
                ParagraphStyle(
                    "ITPInvoiceCellCenterBold",
                    fontName=bold,
                    fontSize=6.5,
                    leading=7.4,
                    alignment=TA_CENTER,
                    textColor=colors.black,
                ),
        }

        def draw_paragraph(
            pdf: canvas.Canvas,
            value: str,
            style: ParagraphStyle,
            x: float,
            y: float,
            width: float,
            height: float,
            *,
            middle: bool = False,
        ) -> float:
            paragraph = Paragraph(
                value,
                style,
            )

            _, actual_height = (
                paragraph.wrap(
                    width,
                    height,
                )
            )

            draw_y = (
                y
                + (
                    height
                    - actual_height
                ) / 2
                if middle
                else (
                    y
                    + height
                    - actual_height
                )
            )

            paragraph.drawOn(
                pdf,
                x,
                draw_y,
            )

            return actual_height

        def safe(value: Any) -> str:
            return escape(
                str(
                    value
                    if value not in (
                        None,
                        "",
                    )
                    else "-"
                )
            )

        self.output_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        filename = (
            _safe_filename(
                invoice_number
            )
            + ".pdf"
        )

        final_path = (
            self.output_dir
            / filename
        )

        fd, temporary_name = (
            tempfile.mkstemp(
                prefix=".invoice-",
                suffix=".tmp",
                dir=str(
                    self.output_dir
                ),
            )
        )

        os.close(fd)

        temporary_path = Path(
            temporary_name
        )

        try:
            pdf = canvas.Canvas(
                str(temporary_path),
                pagesize=A4,
                invariant=1,
                pageCompression=1,
            )

            width, height = A4

            pdf.setTitle(
                "Invoice "
                + invoice_number
            )
            pdf.setAuthor(
                "ITP Mining"
            )

            x0 = 20.0
            content_width = 555.0

            # -------------------------------------------------
            # Header: exact structure of the reference invoice
            # -------------------------------------------------

            pdf.drawImage(
                ImageReader(
                    str(
                        self.logo_path
                    )
                ),
                x0,
                height - 67,
                width=65,
                height=19,
                preserveAspectRatio=True,
                anchor="c",
                mask="auto",
            )

            notice = str(
                seller.get(
                    "invoice_notice"
                )
                or (
                    "\u0412\u043d\u0438\u043c\u0430\u043d\u0438\u0435! "
                    "\u041e\u043f\u043b\u0430\u0442\u0430 "
                    "\u0434\u0430\u043d\u043d\u043e\u0433\u043e "
                    "\u0441\u0447\u0435\u0442\u0430 "
                    "\u043e\u0437\u043d\u0430\u0447\u0430\u0435\u0442 "
                    "\u0441\u043e\u0433\u043b\u0430\u0441\u0438\u0435 "
                    "\u0441 "
                    "\u0443\u0441\u043b\u043e\u0432\u0438\u044f\u043c\u0438 "
                    "\u043e\u043f\u043b\u0430\u0442\u044b "
                    "\u0438 "
                    "\u043f\u0440\u0435\u0434\u043e\u0441\u0442\u0430\u0432\u043b\u0435\u043d\u0438\u044f "
                    "\u0434\u043e\u0441\u0442\u0443\u043f\u0430 "
                    "\u043a "
                    "\u043f\u0440\u043e\u0433\u0440\u0430\u043c\u043c\u043d\u043e\u043c\u0443 "
                    "\u043f\u0440\u043e\u0434\u0443\u043a\u0442\u0443. "
                    "\u0423\u0432\u0435\u0434\u043e\u043c\u043b\u0435\u043d\u0438\u0435 "
                    "\u043e\u0431 "
                    "\u043e\u043f\u043b\u0430\u0442\u0435 "
                    "\u043e\u0431\u044f\u0437\u0430\u0442\u0435\u043b\u044c\u043d\u043e."
                )
            )

            draw_paragraph(
                pdf,
                safe(notice),
                styles["small_center"],
                x0 + 72,
                height - 75,
                content_width - 72,
                38,
            )

            # Bank details begin directly below the header.
            table_top = (
                height - 91
            )

            beneficiary_height = 36
            bank_height = 22

            table_bottom = (
                table_top
                - beneficiary_height
                - bank_height
            )

            left_width = 314
            middle_width = 104
            right_width = (
                content_width
                - left_width
                - middle_width
            )

            x1 = x0 + left_width
            x2 = x1 + middle_width
            x3 = x0 + content_width

            pdf.setLineWidth(
                0.5
            )

            for y in (
                table_top,
                table_top
                - beneficiary_height,
                table_bottom,
            ):
                pdf.line(
                    x0,
                    y,
                    x3,
                    y,
                )

            for x in (
                x0,
                x1,
                x2,
                x3,
            ):
                pdf.line(
                    x,
                    table_bottom,
                    x,
                    table_top,
                )

            seller_name = str(
                seller.get("name")
                or ""
            ).strip()

            beneficiary = (
                "<b>"
                "\u0411\u0435\u043d\u0435\u0444\u0438\u0446\u0438\u0430\u0440:"
                "</b><br/>"
                "<b>"
                + safe(seller_name)
                + "</b><br/>"
                "\u0411\u0418\u041d: "
                + safe(
                    seller.get(
                        "registration_number"
                    )
                )
            )

            draw_paragraph(
                pdf,
                beneficiary,
                styles["normal"],
                x0 + 2,
                table_top
                - beneficiary_height
                + 2,
                left_width - 4,
                beneficiary_height - 4,
            )

            draw_paragraph(
                pdf,
                (
                    "<b>\u0418\u0418\u041a</b><br/>"
                    "<b>"
                    + safe(
                        seller.get("iban")
                    )
                    + "</b>"
                ),
                styles[
                    "cell_center"
                ],
                x1 + 2,
                table_top
                - beneficiary_height
                + 2,
                middle_width - 4,
                beneficiary_height - 4,
                middle=True,
            )

            draw_paragraph(
                pdf,
                (
                    "<b>\u041a\u0431\u0435</b><br/>"
                    "<b>"
                    + safe(
                        seller.get("kbe")
                    )
                    + "</b>"
                ),
                styles[
                    "cell_center"
                ],
                x2 + 2,
                table_top
                - beneficiary_height
                + 2,
                right_width - 4,
                beneficiary_height - 4,
                middle=True,
            )

            draw_paragraph(
                pdf,
                (
                    "\u0411\u0430\u043d\u043a "
                    "\u0431\u0435\u043d\u0435\u0444\u0438\u0446\u0438\u0430\u0440\u0430:"
                    "<br/>"
                    + safe(
                        seller.get(
                            "bank_name"
                        )
                    )
                ),
                styles["normal"],
                x0 + 2,
                table_bottom + 2,
                left_width - 4,
                bank_height - 4,
            )

            draw_paragraph(
                pdf,
                (
                    "<b>\u0411\u0418\u041a</b><br/>"
                    "<b>"
                    + safe(
                        seller.get("bic")
                    )
                    + "</b>"
                ),
                styles[
                    "cell_center"
                ],
                x1 + 2,
                table_bottom + 2,
                middle_width - 4,
                bank_height - 4,
                middle=True,
            )

            draw_paragraph(
                pdf,
                (
                    "<b>"
                    "\u041a\u043e\u0434 "
                    "\u043d\u0430\u0437\u043d\u0430\u0447\u0435\u043d\u0438\u044f "
                    "\u043f\u043b\u0430\u0442\u0435\u0436\u0430"
                    "</b><br/>"
                    "<b>"
                    + safe(
                        seller.get(
                            "payment_purpose_code"
                        )
                    )
                    + "</b>"
                ),
                styles[
                    "cell_center"
                ],
                x2 + 2,
                table_bottom + 2,
                right_width - 4,
                bank_height - 4,
                middle=True,
            )

            # -------------------------------------------------
            # Invoice title
            # -------------------------------------------------

            invoice_title_y = (
                table_bottom - 31
            )

            pdf.setFont(
                bold,
                12.3,
            )

            pdf.drawString(
                x0 + 3,
                invoice_title_y,
                (
                    "\u0421\u0447\u0435\u0442 "
                    "\u043d\u0430 "
                    "\u043e\u043f\u043b\u0430\u0442\u0443 "
                    "\u2116 "
                    + invoice_number
                    + " "
                    "\u043e\u0442 "
                    + issued
                ),
            )

            pdf.setLineWidth(
                1.0
            )
            pdf.line(
                x0,
                invoice_title_y - 11,
                x0 + content_width,
                invoice_title_y - 11,
            )

            # -------------------------------------------------
            # Supplier / buyer. No agreement row.
            # -------------------------------------------------

            label_width = 54
            value_x = (
                x0 + label_width
            )
            value_width = (
                content_width
                - label_width
                - 2
            )

            current_y = (
                invoice_title_y - 28
            )

            supplier_bits = [
                (
                    "<b>"
                    "\u0411\u0418\u041d / \u0418\u0418\u041d "
                    + safe(
                        seller.get(
                            "registration_number"
                        )
                    )
                    + "</b>"
                ),
                (
                    "<b>"
                    + safe(seller_name)
                    + "</b>"
                ),
            ]

            seller_address = str(
                seller.get(
                    "legal_address"
                )
                or ""
            ).strip()

            if seller_address:
                supplier_bits.append(
                    "<b>"
                    + safe(
                        seller_address
                    )
                    + "</b>"
                )

            seller_phone = str(
                seller.get("phone")
                or ""
            ).strip()

            if seller_phone:
                supplier_bits.append(
                    "<b>"
                    "\u0442\u0435\u043b.: "
                    + safe(
                        seller_phone
                    )
                    + "</b>"
                )

            pdf.setFont(
                regular,
                7.6,
            )
            pdf.drawString(
                x0 + 2,
                current_y,
                "\u041f\u043e\u0441\u0442\u0430\u0432\u0449\u0438\u043a:",
            )

            supplier_html = (
                ",".join(
                    supplier_bits
                )
            )

            supplier_h = (
                draw_paragraph(
                    pdf,
                    supplier_html,
                    styles["normal"],
                    value_x,
                    current_y - 22,
                    value_width,
                    30,
                )
            )

            current_y -= max(
                31,
                supplier_h + 9,
            )

            pdf.setFont(
                regular,
                7.6,
            )
            pdf.drawString(
                x0 + 2,
                current_y,
                "\u041f\u043e\u043a\u0443\u043f\u0430\u0442\u0435\u043b\u044c:",
            )

            buyer_name = str(
                buyer.get("name")
                or ""
            ).strip()

            buyer_bits = [
                (
                    "<b>"
                    "\u0411\u0418\u041d / \u0418\u0418\u041d "
                    + safe(
                        buyer.get(
                            "registration_number"
                        )
                    )
                    + "</b>"
                ),
                (
                    "<b>"
                    + safe(
                        buyer_name
                    )
                    + "</b>"
                ),
            ]

            buyer_address = str(
                buyer.get(
                    "legal_address"
                )
                or ""
            ).strip()

            if buyer_address:
                buyer_bits.append(
                    "<b>"
                    + safe(
                        buyer_address
                    )
                    + "</b>"
                )

            buyer_html = (
                ",".join(
                    buyer_bits
                )
            )

            buyer_h = draw_paragraph(
                pdf,
                buyer_html,
                styles["normal"],
                value_x,
                current_y - 22,
                value_width,
                30,
            )

            current_y -= max(
                34,
                buyer_h + 12,
            )

            # -------------------------------------------------
            # Item table
            # -------------------------------------------------

            table_top = current_y

            header_height = 11
            row_height = 20

            raw_widths = (
                24,
                300,
                42,
                30,
                70,
                89,
            )

            scale = (
                content_width
                / sum(
                    raw_widths
                )
            )

            widths = [
                value * scale
                for value
                in raw_widths
            ]

            xs = [x0]

            for column_width in widths:
                xs.append(
                    xs[-1]
                    + column_width
                )

            item_count = len(
                line_items
            )

            table_bottom = (
                table_top
                - header_height
                - row_height
                * item_count
            )

            pdf.setLineWidth(
                0.5
            )

            pdf.line(
                x0,
                table_top,
                x0 + content_width,
                table_top,
            )

            pdf.line(
                x0,
                table_top
                - header_height,
                x0 + content_width,
                table_top
                - header_height,
            )

            for row in range(
                item_count + 1
            ):
                y = (
                    table_top
                    - header_height
                    - row_height
                    * row
                )

                pdf.line(
                    x0,
                    y,
                    x0 + content_width,
                    y,
                )

            for x in xs:
                pdf.line(
                    x,
                    table_bottom,
                    x,
                    table_top,
                )

            headers = (
                "\u2116",
                "\u041d\u0430\u0438\u043c\u0435\u043d\u043e\u0432\u0430\u043d\u0438\u0435",
                "\u041a\u043e\u043b-\u0432\u043e",
                "\u0415\u0434.",
                "\u0426\u0435\u043d\u0430",
                "\u0421\u0443\u043c\u043c\u0430",
            )

            for index, header in enumerate(
                headers
            ):
                draw_paragraph(
                    pdf,
                    "<b>"
                    + header
                    + "</b>",
                    styles[
                        "cell_center_bold"
                    ],
                    xs[index] + 1,
                    table_top
                    - header_height
                    + 1,
                    widths[index] - 2,
                    header_height - 2,
                    middle=True,
                )

            for row_index, item in enumerate(
                line_items,
                start=1,
            ):
                if not isinstance(
                    item,
                    dict,
                ):
                    raise InvoicePDFError(
                        "Invalid invoice line item."
                    )

                row_top = (
                    table_top
                    - header_height
                    - row_height
                    * (
                        row_index - 1
                    )
                )

                row_bottom = (
                    row_top
                    - row_height
                )

                quantity = item.get(
                    "quantity",
                    months,
                )

                try:
                    quantity_decimal = Decimal(
                        str(quantity)
                    )
                except (
                    InvalidOperation,
                    TypeError,
                    ValueError,
                ) as exc:
                    raise InvoicePDFError(
                        "Invalid line item quantity."
                    ) from exc

                quantity_text = (
                    f"{quantity_decimal:.3f}"
                    .replace(
                        ".",
                        ",",
                    )
                )

                unit_price = _money(
                    item.get(
                        "unit_price",
                        invoice.get(
                            "unit_price",
                            0,
                        ),
                    )
                )

                line_amount = _money(
                    item.get(
                        "amount",
                        unit_price
                        * quantity_decimal,
                    )
                )

                plan_label = str(
                    item.get("plan_name")
                    or item.get("plan_code")
                    or invoice.get("plan_name")
                    or "Base"
                ).strip()

                months_label = str(
                    item.get("quantity")
                    or invoice.get("months_count")
                    or months
                    or 1
                ).strip()

                service_name = (
                    "\u041f\u043e\u0434\u043f\u0438\u0441\u043a\u0430 Spyon - "
                    f"\u043f\u0430\u043a\u0435\u0442 "
                    f"\u00ab{plan_label}\u00bb, "
                    f"\u0441\u0440\u043e\u043a: "
                    f"{months_label} "
                    "\u043c\u0435\u0441."
                )

                explicit_description = str(
                    item.get("description")
                    or item.get("name")
                    or ""
                ).strip()
                if explicit_description:
                    service_name = explicit_description

                values = (
                    str(row_index),
                    service_name,
                    quantity_text,
                    str(
                        item.get(
                            "unit_label"
                        )
                        or "???"
                    ),
                    format_money(
                        unit_price
                    ),
                    format_money(
                        line_amount
                    ),
                )

                for index, value in enumerate(
                    values
                ):
                    style = (
                        styles["normal"]
                        if index == 1
                        else styles[
                            "cell_center"
                        ]
                    )

                    draw_paragraph(
                        pdf,
                        safe(value),
                        style,
                        xs[index] + 1,
                        row_bottom + 1,
                        widths[index] - 2,
                        row_height - 2,
                        middle=True,
                    )

            # -------------------------------------------------
            # Totals
            # -------------------------------------------------

            totals_y = (
                table_bottom - 13
            )

            pdf.setFont(
                bold,
                7.8,
            )

            pdf.drawRightString(
                x0
                + content_width
                - 67,
                totals_y,
                "\u0418\u0442\u043e\u0433\u043e:",
            )

            pdf.drawRightString(
                x0
                + content_width
                - 3,
                totals_y,
                format_money(total),
            )

            totals_y -= 11

            pdf.drawRightString(
                x0
                + content_width
                - 67,
                totals_y,
                (
                    "\u0412 "
                    "\u0442\u043e\u043c "
                    "\u0447\u0438\u0441\u043b\u0435 "
                    "\u041d\u0414\u0421:"
                ),
            )

            pdf.drawRightString(
                x0
                + content_width
                - 3,
                totals_y,
                format_money(vat),
            )

            # -------------------------------------------------
            # Amount in words
            # -------------------------------------------------

            totals_y -= 29

            pdf.setFont(
                regular,
                7.4,
            )

            pdf.drawString(
                x0 + 2,
                totals_y,
                (
                    "\u0412\u0441\u0435\u0433\u043e "
                    "\u043d\u0430\u0438\u043c\u0435\u043d\u043e\u0432\u0430\u043d\u0438\u0439 "
                    + str(
                        len(line_items)
                    )
                    + ", "
                    "\u043d\u0430 "
                    "\u0441\u0443\u043c\u043c\u0443 "
                    + format_money(total)
                    + " "
                    "\u0442\u0435\u04a3\u0433\u0435"
                ),
            )

            totals_y -= 11

            pdf.setFont(
                bold,
                7.5,
            )

            pdf.drawString(
                x0 + 2,
                totals_y,
                (
                    "\u0412\u0441\u0435\u0433\u043e "
                    "\u043a "
                    "\u043e\u043f\u043b\u0430\u0442\u0435: "
                    + amount_in_words(
                        total,
                        currency,
                    )
                ),
            )

            totals_y -= 10

            pdf.setLineWidth(
                1.0
            )

            pdf.line(
                x0,
                totals_y,
                x0 + content_width,
                totals_y,
            )

            # -------------------------------------------------
            # Executor + stamp
            # -------------------------------------------------

            signature_y = (
                totals_y - 18
            )

            pdf.setFont(
                bold,
                7.6,
            )

            pdf.drawString(
                x0 + 2,
                signature_y,
                "\u0418\u0441\u043f\u043e\u043b\u043d\u0438\u0442\u0435\u043b\u044c",
            )

            pdf.setLineWidth(
                0.5
            )

            pdf.line(
                x0 + 65,
                signature_y - 2,
                x0 + 247,
                signature_y - 2,
            )

            executor = str(
                seller.get(
                    "executor_name"
                )
                or (
                    "\u0422\u0443\u043b\u0443\u0431\u0430\u0435\u0432 "
                    "\u0414.\u0421."
                )
            ).strip()

            pdf.setFont(
                regular,
                7.0,
            )

            pdf.drawString(
                x0 + 249,
                signature_y - 1,
                "/"
                + executor
                + "/",
            )

            pdf.drawImage(
                ImageReader(
                    str(
                        self.stamp_path
                    )
                ),
                x0 + 72,
                signature_y - 70,
                width=176,
                height=120,
                preserveAspectRatio=True,
                anchor="c",
                mask="auto",
            )

            pdf.showPage()
            pdf.save()

            content = (
                temporary_path
                .read_bytes()
            )

            if not content.startswith(
                b"%PDF-"
            ):
                raise InvoicePDFError(
                    "Generated file is not a PDF."
                )

            if b"%%EOF" not in content[-64:]:
                raise InvoicePDFError(
                    "Generated PDF is incomplete."
                )

            digest = hashlib.sha256(
                content
            ).hexdigest()

            temporary_path.replace(
                final_path
            )

            return {
                "path": str(
                    final_path
                ),
                "sha256": digest,
                "size": len(content),
                "invoice_number":
                    invoice_number,
            }

        except Exception:
            temporary_path.unlink(
                missing_ok=True
            )
            raise
