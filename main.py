from fastapi import FastAPI, Request, HTTPException
from fpdf import FPDF
import os
from datetime import datetime, timedelta
import json
import re
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from typing import Dict, Any, Optional, List
from pathlib import Path
import glob
import base64
import requests

# ============= LOGGING SETUP =============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('packing_slip.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
# =========================================

app = FastAPI(title="Packing Slip Generator", version="2.0")

# ============= GLOBAL STATE =============
class ConfigManager:
    """Centralized config management"""
    def __init__(self):
        self.config_map: Optional[Dict] = None
        self.configs_cache: Dict[str, Dict] = {}
        self.base_path = Path(__file__).parent
    
    def load_config_map(self) -> Dict:
        """Load config map file"""
        config_paths = [
            self.base_path / 'configs' / 'config_map.json',
            self.base_path / 'config_map.json'
        ]
        
        for path in config_paths:
            if path.exists():
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        config_map = json.load(f)
                        logger.info(f"Config map loaded from: {path}")
                        logger.info(f"Registered forms: {len(config_map.get('forms', {}))}")
                        return config_map
                except Exception as e:
                    logger.error(f"Error loading {path}: {e}")
        
        raise FileNotFoundError("config_map.json not found")
    
    def load_form_config(self, config_file: str) -> Dict:
        """Load individual form config"""
        if config_file in self.configs_cache:
            return self.configs_cache[config_file]
        
        config_paths = [
            self.base_path / 'configs' / config_file,
            self.base_path / config_file
        ]
        
        for path in config_paths:
            if path.exists():
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        config = json.load(f)
                        self.configs_cache[config_file] = config
                        logger.info(f"Config loaded: {config_file}")
                        return config
                except Exception as e:
                    logger.error(f"Error loading {path}: {e}")
        
        raise FileNotFoundError(f"Config file not found: {config_file}")
    
    def get_config_for_form(self, form_id: str) -> Dict:
        """Get config for specific form ID"""
        if not self.config_map:
            raise RuntimeError("Config map not initialized")
        
        # Try exact match
        if form_id in self.config_map.get('forms', {}):
            form_info = self.config_map['forms'][form_id]
            logger.info(f"Config found for form {form_id}: {form_info['name']}")
            return self.load_form_config(form_info['config_file'])
        
        # Use default
        default_config = self.config_map.get('default_config', 'wholesale_order.json')
        logger.warning(f"Form {form_id} not registered, using default")
        return self.load_form_config(default_config)
    
    def initialize(self):
        """Initialize config manager"""
        self.config_map = self.load_config_map()
        
        # Create SINGLE packing_slips folder
        packing_slips_dir = self.base_path / 'packing_slips'
        packing_slips_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Packing slips directory: {packing_slips_dir}")

# Global config manager instance
config_manager = ConfigManager()
# ========================================


# ============= CLEANUP FUNCTION =============
def cleanup_old_pdfs():
    """Delete PDFs older than 24 hours from packing_slips folder"""
    try:
        packing_slips_dir = Path(__file__).parent / 'packing_slips'
        
        if not packing_slips_dir.exists():
            return
        
        cutoff_time = datetime.now() - timedelta(hours=24)
        deleted_count = 0
        
        # Find all PDF files
        for pdf_file in packing_slips_dir.glob('*.pdf'):
            try:
                # Get file modification time
                file_mtime = datetime.fromtimestamp(pdf_file.stat().st_mtime)
                
                # Delete if older than 24 hours
                if file_mtime < cutoff_time:
                    pdf_file.unlink()
                    deleted_count += 1
                    logger.info(f"Deleted old PDF: {pdf_file.name}")
            except Exception as e:
                logger.error(f"Error deleting {pdf_file.name}: {e}")
        
        if deleted_count > 0:
            logger.info(f"Cleanup complete: {deleted_count} old PDFs deleted")
    
    except Exception as e:
        logger.error(f"Cleanup error: {e}")
# ============================================


# ============= UTILITY FUNCTIONS =============
def clean_text(text: Any) -> str:
    """Clean unicode characters from text"""
    if not isinstance(text, str):
        return str(text)
    
    replacements = {
        '\u2013': '-', '\u2014': '-',
        '\u2018': "'", '\u2019': "'",
        '\u201c': '"', '\u201d': '"',
    }
    
    for old, new in replacements.items():
        text = text.replace(old, new)
    
    return text


def extract_product_code(product_name: str) -> str:
    """
    Extract product code from name (HDGFXXX format)
    Handles various formats:
    - With (GST) before code: "Product (GST) - HDGF593"
    - With (GST) but no code: "Product (GST)"
    - Code in parentheses: "Product (HDGF593)"
    - Code after dash: "Product - HDGF593"
    - No code at all: "Product"
    """
    if not product_name:
        return ""
    
    # Step 1: Look for actual product code pattern (HDGF followed by digits)
    # This matches codes like HDGF593, HDGF594, etc.
    code_pattern = r'([A-Z]{4}\d{3,})'
    
    # Try to find HDGFXXX pattern anywhere in the string
    match = re.search(code_pattern, product_name)
    if match:
        return match.group(1)
    
    # Step 2: If no HDGFXXX pattern found, check for codes in parentheses
    # but EXCLUDE common words like GST, AUD, etc.
    paren_match = re.search(r'\(([A-Z0-9]+)\)', product_name)
    if paren_match:
        potential_code = paren_match.group(1)
        # Filter out non-code values
        excluded_terms = ['GST', 'AUD', 'USD', 'TAX', 'INC', 'EXC']
        if potential_code not in excluded_terms and len(potential_code) >= 4:
            # Check if it looks like a product code (mix of letters and numbers)
            if re.match(r'^[A-Z]{2,}[0-9]+$', potential_code):
                return potential_code
    
    # Step 3: No valid code found
    return ""


def clean_product_name(product_name: str) -> str:
    """Remove code and extra info from product name"""
    if not product_name:
        return ""
    
    cleaned = re.sub(r'\s*\([A-Z0-9]+\)\s*', '', product_name)
    cleaned = re.sub(r'\s*-\s*[A-Z]{4}\d+\s*', '', cleaned)
    cleaned = re.sub(r'RRP\s*\$\d+.*$', '', cleaned)
    
    return cleaned.strip()


def extract_batch_code(value: Any) -> str:
    """Extract batch code, filtering out price/quantity info"""
    if not value:
        return ""
    
    if isinstance(value, str):
        if value.startswith(('Amount:', 'Quantity:', 'Price:', '$')):
            return ""
        return value.strip()
    
    if isinstance(value, list):
        codes = []
        for item in value:
            item_str = str(item).strip()
            if not item_str.startswith(('Amount:', 'Quantity:', 'Price:', '$')):
                if not item_str.isdigit() and re.match(r'^[A-Z0-9][A-Z0-9\-_]*$', item_str, re.I):
                    codes.append(item_str)
        return ', '.join(codes)
    
    return str(value).strip()


def wrap_text(text: str, max_width_mm: int, font_size: int = 10) -> List[str]:
    """Wrap text to fit within column width"""
    if not text:
        return [""]
    
    chars_per_line = int(max_width_mm * 0.35 * (10 / font_size))
    words = text.split()
    lines = []
    current_line = ""
    
    for word in words:
        test_line = current_line + (" " if current_line else "") + word
        
        if len(test_line) <= chars_per_line:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            current_line = word if len(word) <= chars_per_line else word[:chars_per_line]
    
    if current_line:
        lines.append(current_line)
    
    return lines if lines else [""]
# =============================================


# ============= FIELD EXTRACTORS =============
def extract_date_field(date_obj: Any) -> str:
    """Extract formatted date from object"""
    if not isinstance(date_obj, dict):
        return str(date_obj)
    
    day = date_obj.get('day', 'N/A')
    month = date_obj.get('month', 'N/A')
    year = date_obj.get('year', 'N/A')
    return f"{day}-{month}-{year}"


def extract_address_field(address_obj: Any) -> str:
    """Extract formatted address from object"""
    if not isinstance(address_obj, dict):
        return str(address_obj)
    
    parts = [
        address_obj.get('addr_line1', ''),
        address_obj.get('addr_line2', ''),
        address_obj.get('city', ''),
        address_obj.get('state', ''),
        address_obj.get('postal', '')
    ]
    
    address = ', '.join(filter(None, parts))
    return address if address else "N/A"


def extract_phone_field(phone_obj: Any) -> str:
    """Extract phone number from object"""
    if not isinstance(phone_obj, dict):
        return str(phone_obj)
    
    if phone_obj.get("full"):
        return phone_obj["full"]
    
    area = phone_obj.get("area", "").replace("+", "").replace(" ", "").strip()
    phone = phone_obj.get("phone", "").strip()
    
    if area and phone:
        return area + phone
    
    return "N/A"


def extract_field_value(raw_data: Dict, field_config: Dict) -> str:
    """Extract field value based on config"""
    # Static value
    if "static_value" in field_config:
        return field_config["static_value"]
    
    jotform_field = field_config.get("jotform_field")
    
    # Multiple possible fields
    if isinstance(jotform_field, list):
        for field_id in jotform_field:
            value = raw_data.get(field_id)
            if value and value != "N/A":
                return clean_text(str(value))
        return ""
    
    # Single field
    if not jotform_field or jotform_field not in raw_data:
        return ""
    
    value = raw_data[jotform_field]
    field_type = field_config.get("field_type", "text")
    
    # Process by type
    if field_type == "date_object":
        return extract_date_field(value)
    elif field_type == "address_object":
        return extract_address_field(value)
    elif field_type == "phone_object":
        return extract_phone_field(value)
    elif field_type == "phone_simple":
        return str(value) if value else "N/A"
    
    return clean_text(str(value)) if value else ""


def extract_products(raw_data: Dict, config: Dict) -> List[Dict]:
    """Extract products from webhook data"""
    product_config = config['products']
    products_field = product_config["jotform_field"]
    products_key = product_config["products_key"]
    
    if products_field not in raw_data:
        logger.warning(f"Products field '{products_field}' not found")
        return []
    
    products = raw_data[products_field].get(products_key, [])
    logger.info(f"Found {len(products)} products")
    
    items = []
    for product in products:
        item = {}
        
        for column in product_config["columns"]:
            col_name = column["name"]
            jotform_key = column["jotform_key"]
            
            # Get value
            value = product.get(jotform_key)
            
            # Try fallback keys
            if not value and "fallback_keys" in column:
                for fallback_key in column["fallback_keys"]:
                    value = product.get(fallback_key)
                    if value:
                        break
            
            # Special processing
            if col_name == "batch_code":
                value = extract_batch_code(value)
            elif col_name == "qty":
                try:
                    value = int(float(str(value))) if value else 0
                except (ValueError, TypeError):
                    value = 0
            elif "process_function" in column and value:
                func_name = column["process_function"]
                if func_name == "clean_product_name":
                    value = clean_product_name(value)
                elif func_name == "extract_product_code":
                    value = extract_product_code(value)
            
            item[col_name] = value if value else ""
        
        items.append(item)
    
    return items


def extract_form_id(raw_data: Dict) -> Optional[str]:
    """Extract form ID from webhook data"""
    # Try direct fields
    for field in ['formID', 'form_id']:
        if field in raw_data and raw_data[field]:
            return str(raw_data[field])
    
    # Try extracting from slug/path
    for field in ['slug', 'path']:
        if field in raw_data:
            match = re.search(r'submit/(\d+)', raw_data[field])
            if match:
                return match.group(1)
    
    return None
# ============================================


def send_email_with_pdf(pdf_path: str, config: Dict, invoice_no: str, order_data: Dict = None) -> bool:
    email_config = config['email']

    try:
        sender = os.getenv('EMAIL_SENDER')
        api_key = os.getenv('RESEND_API_KEY')

        if not sender or not api_key:
            logger.error("EMAIL_SENDER or RESEND_API_KEY missing")
            return False

        # ---------- RECIPIENT LOGIC (UNCHANGED) ----------
        recipients = list(email_config['recipients'])

        send_to_factory = order_data.get('send_to_factory', 'No') if order_data else 'No'
        factory_email = email_config.get('factory_email', '')

        if send_to_factory.lower() == 'yes' and factory_email:
            if factory_email not in recipients:
                recipients.append(factory_email)
        elif send_to_factory.lower() == 'no':
            if factory_email in recipients:
                recipients.remove(factory_email)

        # ---------- DYNAMIC DATA EXTRACTION (UNCHANGED) ----------
        delivery_method = ""
        order_date = ""
        customer_note = ""

        if order_data:
            for key in ['delivery_method', 'order_type', 'fulfillment_method']:
                if order_data.get(key):
                    delivery_method = order_data[key]
                    break

            for key in ['order_date', 'delivery_date', 'pickup_date', 'date']:
                if order_data.get(key):
                    order_date = order_data[key]
                    break

            for key in ['customer_note', 'note', 'special_instructions', 'comments']:
                if order_data.get(key):
                    customer_note = order_data[key]
                    break

        # ---------- SUBJECT (UNCHANGED) ----------
        subject = email_config.get('subject', 'New Order - {invoice_no}')
        subject = subject.replace('{invoice_no}', invoice_no)

        if delivery_method or order_date:
            subject = f"Order for {delivery_method} {order_date} - {invoice_no}".strip()

        # ---------- BODY (PLAIN + HTML SAME AS BEFORE) ----------
        body_parts = [
            "Hi guys,",
            "",
            "Please see the order below."
        ]

        if delivery_method:
            body_parts.append(f"For {delivery_method} on {order_date}" if order_date else f"For {delivery_method}")
        elif order_date:
            body_parts.append(f"For {order_date}")

        if customer_note:
            body_parts.append("")
            body_parts.append(f"Note from customer: {customer_note}")

        custom_body = email_config.get('email_body', '')
        if custom_body:
            body_parts.append("")
            body_parts.append(custom_body.replace('{invoice_no}', invoice_no))

        signature_text = email_config.get('signature', '')
        if signature_text:
            body_parts.append("")
            body_parts.append(signature_text.replace('{invoice_no}', invoice_no))

        plain_body = "\n".join(body_parts)

        # ---------- HTML ----------
        html_body = plain_body.replace("\n", "<br>")

        # ---------- PDF ----------
        if not Path(pdf_path).exists():
            raise FileNotFoundError(pdf_path)

        with open(pdf_path, "rb") as f:
            encoded_pdf = base64.b64encode(f.read()).decode("utf-8")

        # ---------- RESEND API ----------
        response = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "from": sender,
                "to": recipients,
                "subject": subject,
                "html": html_body,
                "text": plain_body,
                "attachments": [
                    {
                        "filename": Path(pdf_path).name,
                        "content": encoded_pdf
                    }
                ]
            },
            timeout=30
        )

        if response.status_code not in (200, 201):
            logger.error(f"Resend failed: {response.text}")
            return False

        logger.info(f"Email sent successfully via Resend → {recipients}")
        return True

    except Exception as e:
        logger.error(f"Email failed: {e}", exc_info=True)
        return False
# =========================================
# =========================================


# ============= PDF GENERATOR (FIXED - NO CUSTOM FONTS) =============
def create_packing_slip(order_data: Dict, config: Dict) -> str:
    """Generate PDF packing slip in single packing_slips folder"""
    pdf = FPDF('P', 'mm', 'A4')
    pdf.add_page()
    
    pdf_config = config['pdf']
    style = pdf_config['style']
    
    # FIXED: Use built-in Arial font only (no custom font loading)
    # This avoids the cached path issue with fpdf library
    regular_font = "Arial"
    bold_font = "Arial"
    
    # Optional: Try to load DejaVu fonts if they exist, but don't fail if they don't
    base_path = Path(__file__).parent
    font_loaded = False
    
    # Only try if fonts actually exist in the deployment
    possible_font_locations = [
        (base_path / "DejaVuSans.ttf", base_path / "DejaVuSans-Bold.ttf"),
        (base_path / "dejavu-sans" / "DejaVuSans.ttf", base_path / "dejavu-sans" / "DejaVuSans-Bold.ttf"),
    ]
    
    for regular_path, bold_path in possible_font_locations:
        if regular_path.exists() and bold_path.exists():
            try:
                # Check if font files are readable
                with open(regular_path, 'rb') as f:
                    f.read(10)  # Test read
                with open(bold_path, 'rb') as f:
                    f.read(10)  # Test read
                
                # Now try to add fonts
                pdf.add_font('DejaVu', '', str(regular_path.resolve()), uni=True)
                pdf.add_font('DejaVu', 'B', str(bold_path.resolve()), uni=True)
                
                regular_font = "DejaVu"
                bold_font = "DejaVu"
                font_loaded = True
                logger.info(f"Successfully loaded fonts from: {regular_path}")
                break
            except Exception as e:
                logger.warning(f"Failed to load fonts from {regular_path}: {e}")
                continue
    
    if not font_loaded:
        logger.info("Using built-in Arial fonts (Unicode fonts not available)")
    
    # Logo
    logo_paths = [
        base_path / pdf_config['logo_path'],
        base_path / "logos" / Path(pdf_config['logo_path']).name,
        base_path / Path(pdf_config['logo_path']).name
    ]
    
    logo_height = 0
    for logo_path in logo_paths:
        if logo_path.exists():
            try:
                pdf.image(str(logo_path), x=10, y=5, w=190)
                logo_height = 40
                logger.info(f"Logo loaded from: {logo_path}")
                break
            except Exception as e:
                logger.warning(f"Failed to load logo from {logo_path}: {e}")
    
    if logo_height > 0:
        pdf.ln(logo_height + 2)
    
    # Title
    pdf.set_font(bold_font, 'B', style['title_font_size'])
    pdf.cell(0, 8, pdf_config['title'], ln=True, align="C")
    
    # Title ke baad spacing (configurable)
    spacing_after_title = style.get('spacing_after_title', 8)
    pdf.ln(spacing_after_title)
    
    # Header fields
    header_fields = {
        k: v for k, v in config['fields'].items()
        if v.get("show_in_pdf") and v.get("pdf_section") == "header"
    }
    
    for field_key, field_config in sorted(header_fields.items(), key=lambda x: x[1].get("order", 999)):
        label = field_config["label"]
        value = order_data.get(field_key, "")
        
        if not value or value == "N/A":
            continue
        
        # Check conditional display logic
        if "conditional" in field_config:
            conditional = field_config["conditional"]
            depends_on_field = conditional.get("depends_on")
            show_when_values = conditional.get("show_when", [])
            
            # Get the value of the field this depends on
            depends_on_value = order_data.get(depends_on_field, "")
            
            # Check if current value matches any of the show_when conditions
            should_show = any(
                depends_on_value.lower().strip() == condition.lower().strip()
                for condition in show_when_values
            )
            
            if not should_show:
                logger.debug(f"Skipping {field_key} due to conditional logic")
                continue
        
        # Label width from config (default 40)
        label_width = style.get('label_width', 40)
        
        pdf.set_font(bold_font, 'B', style['header_font_size'])
        pdf.cell(label_width, style['line_height'], f"{label}:", border=0)
        
        pdf.set_font(regular_font, '', style['header_font_size'])
        
        if field_config.get("multiline"):
            pdf.multi_cell(0, style['line_height'], value)
        else:
            pdf.cell(0, style['line_height'], value, ln=True)
    
    # Table se pehle spacing (configurable)
    spacing_before_table = style.get('spacing_before_table', 5)
    pdf.ln(spacing_before_table)
    
    # Products table
    product_config = config['products']
    pdf.set_font(bold_font, 'B', style['header_font_size'])
    pdf.set_draw_color(*style['table_border_color'])
    pdf.set_line_width(0.8)
    pdf.set_fill_color(*style['table_header_color'])
    
    for column in product_config["columns"]:
        pdf.cell(column["width"], 7, column["label"], border=1, fill=True, align='C')
    pdf.ln()
    
    # Table rows
    pdf.set_font(regular_font, '', style['table_font_size'])
    pdf.set_line_width(0.5)
    
    for idx, item in enumerate(order_data['items']):
        color_idx = idx % len(style['alternate_row_colors'])
        pdf.set_fill_color(*style['alternate_row_colors'][color_idx])
        
        # Calculate row height
        max_lines = 1
        for column in product_config["columns"]:
            if column["name"] in ["product_name", "batch_code"]:
                lines = wrap_text(item.get(column["name"], ""), column["width"] - 2, style['table_font_size'])
                max_lines = max(max_lines, len(lines))
        
        row_height = max(7, max_lines * 5)
        
        start_x, start_y = pdf.get_x(), pdf.get_y()
        
        # Check page overflow
        if start_y + row_height > 270:
            pdf.add_page()
            start_y = pdf.get_y()
        
        x_offset = 0
        for column in product_config["columns"]:
            col_name = column["name"]
            col_width = column["width"]
            col_align = column.get("align", "L")
            value = str(item.get(col_name, ""))
            
            if col_name in ["product_name", "batch_code"] and value:
                # Multi-line cell
                pdf.rect(start_x + x_offset, start_y, col_width, row_height, 'FD')
                lines = wrap_text(value, col_width - 2, style['table_font_size'])
                
                y_offset = (row_height - len(lines) * 5) / 2
                for line_idx, line in enumerate(lines):
                    pdf.set_xy(start_x + x_offset + 1, start_y + y_offset + (line_idx * 5))
                    pdf.cell(col_width - 2, 5, line, border=0, fill=False, align=col_align)
            else:
                # Single line cell
                pdf.set_xy(start_x + x_offset, start_y)
                pdf.cell(col_width, row_height, value, border=1, fill=True, align=col_align)
            
            x_offset += col_width
        
        pdf.set_xy(start_x, start_y + row_height)
    
    # Total
    pdf.ln(3)
    pdf.set_draw_color(0, 0, 0)
    pdf.set_font(bold_font, 'B', 12)
    
    total_qty = sum(int(item.get('qty', 0)) for item in order_data['items'])
    
    total_width = sum(c["width"] for c in product_config["columns"][:-1])
    last_col_width = product_config["columns"][-1]["width"]
    
    pdf.cell(total_width, 7, "Total Quantity:", border=0, align='R')
    pdf.cell(last_col_width, 7, str(total_qty), border=0, align='C', ln=True)
    
    # Save to SINGLE packing_slips folder
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    invoice_no = order_data.get('invoice_no', 'UNKNOWN')
    
    # Single folder path - NO subfolders
    packing_slips_dir = base_path / 'packing_slips'
    packing_slips_dir.mkdir(parents=True, exist_ok=True)
    
    pdf_file = packing_slips_dir / f"packing_slip_{invoice_no}_{timestamp}.pdf"
    pdf.output(str(pdf_file))
    
    logger.info(f"PDF created: {pdf_file}")
    return str(pdf_file)

# =========================================


# ============= API ENDPOINTS =============
@app.on_event("startup")
async def startup_event():
    """Initialize on startup"""
    try:
        config_manager.initialize()
        logger.info("Application started successfully")
        logger.info(f"Environment check - EMAIL_SENDER: {'SET' if os.getenv('EMAIL_SENDER') else 'NOT SET'}")
    except Exception as e:
        logger.error(f"Startup failed: {e}", exc_info=True)
        raise


@app.post("/jotform/webhook")
async def webhook_handler(request: Request):
    """Main webhook endpoint with auto-cleanup"""
    try:
        # CLEANUP OLD PDFs BEFORE PROCESSING NEW ORDER (24 hours auto-delete)
        cleanup_old_pdfs()
        
        # Parse request
        form = await request.form()
        data = dict(form)
        raw = json.loads(data.get("rawRequest", "{}"))
        
        # Extract form ID
        form_id = extract_form_id(raw)
        if not form_id:
            raise HTTPException(status_code=400, detail="Could not determine form_id")
        
        logger.info(f"Processing form: {form_id}")
        
        # Load config
        config = config_manager.get_config_for_form(form_id)
        
        # Generate invoice number
        invoice_no = f"INV-{data.get('submissionID', '0000')[:8]}"
        
        # Extract invoice from form if available
        for field_key, field_config in config['fields'].items():
            if 'invoice' in field_key.lower():
                jotform_field = field_config.get('jotform_field')
                if jotform_field and jotform_field in raw:
                    invoice_no = raw[jotform_field].replace('# ', '')
                    break
        
        # Extract order data
        order_data = {"invoice_no": invoice_no}
        for field_key, field_config in config['fields'].items():
            order_data[field_key] = extract_field_value(raw, field_config)
        
        # Extract products
        order_data["items"] = extract_products(raw, config)
        
        # Calculate total
        order_data["total_amount"] = sum(
            float(item.get("subtotal", 0))
            for item in order_data["items"]
            if "subtotal" in item
        )
        
        logger.info(f"Order extracted - Invoice: {invoice_no}, Items: {len(order_data['items'])}")
        
        # Generate PDF in single packing_slips folder
        pdf_path = create_packing_slip(order_data, config)
        
        # Send email using environment variables
        email_sent = send_email_with_pdf(pdf_path, config, invoice_no, order_data)
        
        return {
            "status": "success",
            "form_id": form_id,
            "invoice_no": invoice_no,
            "pdf_path": pdf_path,
            "items_count": len(order_data['items']),
            "email_sent": email_sent
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/")
async def root():
    """Health check"""
    return {
        "status": "online",
        "version": "2.0",
        "registered_forms": len(config_manager.config_map.get('forms', {})),
        "cached_configs": len(config_manager.configs_cache),
        "env_variables": {
            "EMAIL_SENDER": "SET" if os.getenv('EMAIL_SENDER') else "NOT SET",
            "EMAIL_PASSWORD": "SET" if os.getenv('EMAIL_PASSWORD') else "NOT SET"
        }
    }


@app.get("/forms")
async def list_forms():
    """List all registered forms"""
    forms = []
    for form_id, info in config_manager.config_map.get('forms', {}).items():
        forms.append({
            "form_id": form_id,
            "name": info['name'],
            "config_file": info['config_file']
        })
    return {"forms": forms}


@app.get("/cleanup")
async def manual_cleanup():
    """Manual cleanup endpoint for testing"""
    cleanup_old_pdfs()
    return {"status": "cleanup completed"}
# =========================================
# Run with: uvicorn main:app --host 0.0.0.0 --port 8000 --reload