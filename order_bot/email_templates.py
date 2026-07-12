from __future__ import annotations

import csv
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree


EMAIL_TYPE_ORDER_CONFIRMATION = "订单确认邮件"
EMAIL_TYPE_SHIPPING_CONFIRMATION = "物流邮件"
EMAIL_TYPE_VAT_INVOICE = "VAT发票邮件"
EMAIL_TYPE_CUSTOM = "自定义邮件"

PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
RECIPIENT_ALIASES = ("email", "邮箱", "收件邮箱", "收件人邮箱", "Email", "Email Address")


@dataclass(frozen=True)
class EmailVariable:
    name: str
    aliases: tuple[str, ...]
    required: bool = False


@dataclass(frozen=True)
class EmailTypeSpec:
    name: str
    variables: tuple[EmailVariable, ...]


@dataclass(frozen=True)
class EmailDataTable:
    headers: list[str]
    rows: list[dict[str, str]]


@dataclass(frozen=True)
class EmailTaskValidation:
    ok: bool
    errors: list[str]
    warnings: list[str]
    placeholders: list[str]
    preview: str = ""


def variable(name: str, *aliases: str, required: bool = False) -> EmailVariable:
    return EmailVariable(name=name, aliases=(name, *aliases), required=required)


EMAIL_TYPE_SPECS: dict[str, EmailTypeSpec] = {
    EMAIL_TYPE_ORDER_CONFIRMATION: EmailTypeSpec(
        EMAIL_TYPE_ORDER_CONFIRMATION,
        (
            variable("公司名称", "Company Name", "company_name"),
            variable("订单号", "Order Number", "order_id", required=True),
            variable("下单日期", "Order Date", "order_date"),
            variable("客户姓名", "Customer Name", "customer_name", "full_name"),
            variable("商品描述", "Product Description", "product_description", "product_name", "product", required=True),
            variable("数量", "Quantity", "quantity", required=True),
            variable("含VAT总价", "Total Price Including VAT", "total_price_including_vat", "total_price", "price", required=True),
            variable("配送费", "Delivery Fee", "delivery_fee", "shipping_fee"),
            variable("收货地址", "Delivery Address", "delivery_address", "address", "address_line"),
        ),
    ),
    EMAIL_TYPE_SHIPPING_CONFIRMATION: EmailTypeSpec(
        EMAIL_TYPE_SHIPPING_CONFIRMATION,
        (
            variable("公司名称", "Company Name", "company_name"),
            variable("订单号", "Order Number", "order_id", required=True),
            variable("商品描述", "Product Description", "product_description", "product_name", "product"),
            variable("数量", "Quantity", "quantity"),
            variable("承运商", "Courier", "carrier"),
            variable("物流单号", "Tracking Number", "tracking_number", required=True),
            variable("物流查询链接", "Tracking Link", "tracking_link"),
            variable("收货地址", "Delivery Address", "delivery_address", "address", "address_line"),
        ),
    ),
    EMAIL_TYPE_VAT_INVOICE: EmailTypeSpec(
        EMAIL_TYPE_VAT_INVOICE,
        (
            variable("发票号码", "Invoice Number", "invoice_number", required=True),
            variable("供应时间/税点", "Time of Supply / Tax Point", "tax_point", "time_of_supply", required=True),
            variable("发票开具日期", "Invoice Issue Date", "invoice_issue_date", required=True),
            variable("卖方VAT登记名称及地址", "Supplier’s VAT-registered Name and Address", "supplier_vat_name_address", required=True),
            variable("卖方VAT税号", "Supplier’s VAT Registration Number", "supplier_vat_number", required=True),
            variable("客户名称及地址", "Customer Name and Address", "customer_name_address", required=True),
            variable("商品描述", "Product Description", "product_description", "product_name", "product", required=True),
            variable("数量", "Quantity", "quantity", required=True),
            variable("未税单价", "Unit Price Excluding VAT", "unit_price_excluding_vat", required=True),
            variable("未税金额", "Amount Excluding VAT", "amount_excluding_vat", required=True),
            variable("VAT税率", "VAT Rate", "vat_rate", required=True),
            variable("现金折扣率", "Cash Discount Rate", "cash_discount_rate", required=True),
            variable("未税总额", "Total Excluding VAT", "total_excluding_vat", required=True),
            variable("VAT总额", "Total VAT", "total_vat", required=True),
            variable("含税总额", "Total Including VAT", "total_including_vat", required=True),
        ),
    ),
    EMAIL_TYPE_CUSTOM: EmailTypeSpec(EMAIL_TYPE_CUSTOM, ()),
}


def email_type_names() -> tuple[str, ...]:
    return tuple(EMAIL_TYPE_SPECS)


def placeholder_hint(email_type: str) -> str:
    spec = EMAIL_TYPE_SPECS[email_type]
    if email_type == EMAIL_TYPE_CUSTOM:
        return "自定义邮件变量格式：{{列表文件列名}}，例如 {{客户姓名}}。"
    required = "、".join(item.name for item in spec.variables if item.required)
    optional = "、".join(item.name for item in spec.variables if not item.required) or "无"
    return f"变量格式：{{{{变量名}}}}。必填：{required}。非必填：{optional}。"


def extract_placeholders(template_text: str) -> list[str]:
    seen: set[str] = set()
    placeholders: list[str] = []
    for match in PLACEHOLDER_PATTERN.finditer(template_text or ""):
        name = match.group(1).strip()
        if name and name not in seen:
            seen.add(name)
            placeholders.append(name)
    return placeholders


def read_template(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def load_data_file(path: Path) -> EmailDataTable:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _load_csv(path)
    if suffix == ".xlsx":
        return _load_xlsx(path)
    raise ValueError("数据文件仅支持 .csv 或 .xlsx。")


def validate_email_task(
    *,
    email_type: str,
    data_file: Path | None,
    template_file: Path | None,
    attachment_file: Path | None,
) -> EmailTaskValidation:
    errors: list[str] = []
    warnings: list[str] = []
    placeholders: list[str] = []
    preview = ""
    spec = EMAIL_TYPE_SPECS[email_type]

    data = _load_data_file_or_error(data_file, errors)
    has_template = bool(template_file)
    has_attachment = bool(attachment_file)
    template_text = ""

    if email_type == EMAIL_TYPE_VAT_INVOICE:
        if has_template and has_attachment:
            errors.append("VAT发票邮件的邮件模板文件和附件PDF文件只能二选一，不能同时上传。")
        if not has_template and not has_attachment:
            errors.append("VAT发票邮件必须上传邮件模板文件或附件PDF文件中的一个。")
        if has_attachment and attachment_file and attachment_file.suffix.lower() != ".pdf":
            errors.append("VAT发票邮件选择附件时，附件必须是 PDF 文件。")
    elif not has_template:
        errors.append(f"{email_type} 必须上传邮件模板文件。")

    if has_template and template_file:
        if not template_file.exists():
            errors.append(f"邮件模板文件不存在：{template_file}")
        else:
            template_text = read_template(template_file)
            placeholders = extract_placeholders(template_text)
            if not placeholders:
                warnings.append("模板里没有发现变量，占位格式请使用 {{变量名}}。")

    if data:
        _validate_recipient_column(data.headers, errors)
        if has_template:
            _validate_template_variables(spec, data.headers, placeholders, warnings, errors)
            if data.rows:
                preview = render_template(template_text, data.rows[0], spec)
        if email_type == EMAIL_TYPE_VAT_INVOICE and has_attachment and not has_template:
            warnings.append("VAT发票邮件当前选择了PDF附件模式，本版只校验收件邮箱和PDF附件，不替换PDF里的变量。")

    return EmailTaskValidation(ok=not errors, errors=errors, warnings=warnings, placeholders=placeholders, preview=preview)


def render_template(template_text: str, row: dict[str, str], spec: EmailTypeSpec | None = None) -> str:
    variables = {
        _normalize_key(alias): variable
        for variable in (spec.variables if spec else ())
        for alias in variable.aliases
    }

    def replace_match(match: re.Match[str]) -> str:
        placeholder = match.group(1).strip()
        variable_def = variables.get(_normalize_key(placeholder))
        if variable_def is None:
            value = _value_for_aliases(row, (placeholder,))
        else:
            value = _value_for_aliases(row, variable_def.aliases)
        return value if value is not None else match.group(0)

    return PLACEHOLDER_PATTERN.sub(replace_match, template_text)


def _load_data_file_or_error(path: Path | None, errors: list[str]) -> EmailDataTable | None:
    if not path:
        errors.append("必须上传数据文件。")
        return None
    if not path.exists():
        errors.append(f"数据文件不存在：{path}")
        return None
    try:
        data = load_data_file(path)
    except Exception as exc:
        errors.append(f"数据文件读取失败：{exc}")
        return None
    if not data.headers:
        errors.append("数据文件没有表头。")
    if not data.rows:
        errors.append("数据文件没有可发送的数据行。")
    return data


def _validate_recipient_column(headers: list[str], errors: list[str]) -> None:
    if _find_header(headers, RECIPIENT_ALIASES) is None:
        errors.append("数据文件必须包含收件邮箱列：email / 邮箱 / 收件邮箱。")


def _validate_template_variables(
    spec: EmailTypeSpec,
    headers: list[str],
    placeholders: list[str],
    warnings: list[str],
    errors: list[str],
) -> None:
    placeholder_keys = {_normalize_key(name) for name in placeholders}
    header_keys = {_normalize_key(name) for name in headers}
    variable_by_alias = {
        _normalize_key(alias): variable
        for variable in spec.variables
        for alias in variable.aliases
    }

    for variable_def in spec.variables:
        has_placeholder = any(_normalize_key(alias) in placeholder_keys for alias in variable_def.aliases)
        has_data = _find_header(headers, variable_def.aliases) is not None
        if variable_def.required:
            if not has_placeholder:
                errors.append(f"模板缺少必填变量：{{{{{variable_def.name}}}}}")
            if not has_data:
                errors.append(f"数据文件缺少必填列：{variable_def.name}")
        elif has_placeholder and not has_data:
            warnings.append(f"非必填变量未找到数据列，将不会替换：{variable_def.name}")

    if spec.name == EMAIL_TYPE_CUSTOM:
        for placeholder in placeholders:
            if _normalize_key(placeholder) not in header_keys:
                warnings.append(f"自定义变量未找到数据列，将不会替换：{placeholder}")
        return

    known_aliases = set(variable_by_alias)
    for placeholder in placeholders:
        normalized = _normalize_key(placeholder)
        if normalized not in known_aliases and normalized not in header_keys:
            warnings.append(f"模板变量不在固定类型定义中，且数据文件无同名列：{placeholder}")


def _load_csv(path: Path) -> EmailDataTable:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = [header.strip() for header in (reader.fieldnames or []) if header]
        rows = [{(key or "").strip(): (value or "").strip() for key, value in row.items() if key} for row in reader]
    return EmailDataTable(headers=headers, rows=rows)


def _load_xlsx(path: Path) -> EmailDataTable:
    ns = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as archive:
        shared_strings = _read_shared_strings(archive, ns)
        sheet_name = _first_sheet_path(archive)
        root = ElementTree.fromstring(archive.read(sheet_name))

    rows: list[list[str]] = []
    for row_element in root.findall(".//main:sheetData/main:row", ns):
        values: list[str] = []
        current_col = 0
        for cell in row_element.findall("main:c", ns):
            cell_ref = cell.attrib.get("r", "")
            target_col = _column_index_from_cell_ref(cell_ref)
            while current_col < target_col:
                values.append("")
                current_col += 1
            values.append(_cell_text(cell, shared_strings, ns))
            current_col += 1
        rows.append(values)

    if not rows:
        return EmailDataTable(headers=[], rows=[])
    headers = [str(value).strip() for value in rows[0]]
    data_rows = []
    for row in rows[1:]:
        item = {headers[index]: (row[index].strip() if index < len(row) else "") for index in range(len(headers)) if headers[index]}
        if any(item.values()):
            data_rows.append(item)
    return EmailDataTable(headers=[header for header in headers if header], rows=data_rows)


def _read_shared_strings(archive: zipfile.ZipFile, ns: dict[str, str]) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    values = []
    for item in root.findall("main:si", ns):
        text_parts = [node.text or "" for node in item.findall(".//main:t", ns)]
        values.append("".join(text_parts))
    return values


def _first_sheet_path(archive: zipfile.ZipFile) -> str:
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    rels_root = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    ns = {
        "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "pkg": "http://schemas.openxmlformats.org/package/2006/relationships",
    }
    first_sheet = workbook.find("main:sheets/main:sheet", ns)
    if first_sheet is None:
        raise ValueError("xlsx 没有工作表。")
    rel_id = first_sheet.attrib[f"{{{ns['rel']}}}id"]
    for relationship in rels_root.findall("pkg:Relationship", ns):
        if relationship.attrib.get("Id") == rel_id:
            target = relationship.attrib["Target"].lstrip("/")
            return target if target.startswith("xl/") else f"xl/{target}"
    raise ValueError("xlsx 工作表关系缺失。")


def _cell_text(cell, shared_strings: list[str], ns: dict[str, str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//main:t", ns)).strip()
    value_node = cell.find("main:v", ns)
    if value_node is None or value_node.text is None:
        return ""
    raw_value = value_node.text
    if cell_type == "s":
        index = int(raw_value)
        return shared_strings[index].strip() if index < len(shared_strings) else ""
    return raw_value.strip()


def _column_index_from_cell_ref(cell_ref: str) -> int:
    letters = "".join(ch for ch in cell_ref if ch.isalpha())
    index = 0
    for letter in letters:
        index = index * 26 + ord(letter.upper()) - ord("A") + 1
    return max(0, index - 1)


def _value_for_aliases(row: dict[str, str], aliases: tuple[str, ...]) -> str | None:
    for alias in aliases:
        normalized_alias = _normalize_key(alias)
        for key, value in row.items():
            if _normalize_key(key) == normalized_alias:
                return value
    return None


def _find_header(headers: list[str], aliases: tuple[str, ...]) -> str | None:
    normalized_headers = {_normalize_key(header): header for header in headers}
    for alias in aliases:
        match = normalized_headers.get(_normalize_key(alias))
        if match is not None:
            return match
    return None


def _normalize_key(value: str) -> str:
    return re.sub(r"[\s_/\-：:（）()]+", "", value or "").casefold()
