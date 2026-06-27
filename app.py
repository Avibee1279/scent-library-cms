import csv
import io
import os
import re
from datetime import datetime
from functools import wraps
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

from flask import (
    Flask, Response, abort, flash, g, redirect, render_template,
    request, send_from_directory, session, url_for
)
from sqlalchemy import (
    create_engine, MetaData, Table, Column, Integer, Text, Float, text, inspect
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

# Cloudinary is used for permanent product photo storage on hosted servers.
# Product data is stored in PostgreSQL when DATABASE_URL is set, or SQLite locally.
try:
    import cloudinary
    import cloudinary.uploader
except ImportError:  # Local fallback if package is not installed yet
    cloudinary = None


BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Render PostgreSQL provides DATABASE_URL. Locally we fall back to SQLite.
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if DATABASE_URL.startswith("postgres://"):
    # SQLAlchemy expects postgresql://
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
if not DATABASE_URL:
    DATABASE_URL = "sqlite:///" + os.path.join(BASE_DIR, "scent_library.db")

UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "CHANGE-ME-before-going-live")
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # 8MB image upload limit

engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
metadata = MetaData()

admins_table = Table(
    "admins", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("username", Text, unique=True, nullable=False),
    Column("password_hash", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text),
)

products_table = Table(
    "products", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", Text, nullable=False),
    Column("brand", Text),
    Column("category", Text, nullable=False, default="Unisex"),
    Column("size", Text),
    Column("sku", Text),
    Column("scent_family", Text),
    Column("stock_qty", Integer, nullable=False, default=0),
    Column("low_stock_threshold", Integer, nullable=False, default=2),
    Column("price", Float, nullable=False, default=0),
    Column("description", Text),
    Column("notes", Text),
    Column("occasion", Text),
    Column("image_filename", Text),
    Column("is_featured", Integer, nullable=False, default=0),
    Column("is_active", Integer, nullable=False, default=1),
    Column("sort_order", Integer, nullable=False, default=0),
    Column("views", Integer, nullable=False, default=0),
    Column("whatsapp_clicks", Integer, nullable=False, default=0),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)


product_images_table = Table(
    "product_images", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("product_id", Integer, nullable=False),
    Column("image_url", Text, nullable=False),
    Column("caption", Text),
    Column("sort_order", Integer, nullable=False, default=0),
    Column("is_primary", Integer, nullable=False, default=0),
    Column("created_at", Text, nullable=False),
)

banners_table = Table(
    "banners", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("title", Text, nullable=False),
    Column("subtitle", Text),
    Column("image_url", Text),
    Column("button_text", Text),
    Column("button_link", Text),
    Column("is_active", Integer, nullable=False, default=1),
    Column("sort_order", Integer, nullable=False, default=0),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)

combos_table = Table(
    "combos", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("title", Text, nullable=False),
    Column("description", Text),
    Column("product_1", Text),
    Column("product_2", Text),
    Column("is_active", Integer, nullable=False, default=1),
    Column("sort_order", Integer, nullable=False, default=0),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)

order_requests_table = Table(
    "order_requests", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("customer_name", Text, nullable=False),
    Column("phone", Text, nullable=False),
    Column("location", Text),
    Column("product_id", Integer),
    Column("product_name", Text),
    Column("message", Text),
    Column("status", Text, nullable=False, default="New"),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)

settings_table = Table(
    "settings", metadata,
    Column("key", Text, primary_key=True),
    Column("value", Text, nullable=False),
)

# Cloudinary config. Add these values in Render Environment Variables.
CLOUDINARY_CLOUD_NAME = os.environ.get("CLOUDINARY_CLOUD_NAME", "").strip()
CLOUDINARY_API_KEY = os.environ.get("CLOUDINARY_API_KEY", "").strip()
CLOUDINARY_API_SECRET = os.environ.get("CLOUDINARY_API_SECRET", "").strip()
USE_CLOUDINARY = bool(CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET and cloudinary)

if USE_CLOUDINARY:
    cloudinary.config(
        cloud_name=CLOUDINARY_CLOUD_NAME,
        api_key=CLOUDINARY_API_KEY,
        api_secret=CLOUDINARY_API_SECRET,
        secure=True,
    )

os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def now_iso():
    return datetime.utcnow().isoformat(timespec="seconds")


class DatabaseSession:
    """Small helper so the app can use PostgreSQL on Render and SQLite locally."""

    def __init__(self):
        self.conn = engine.connect()

    def _prepare(self, sql, params):
        if params is None:
            return sql, {}
        if isinstance(params, dict):
            return sql, params
        if isinstance(params, (tuple, list)):
            values = list(params)
            named = {}
            index = 0

            def repl(_match):
                nonlocal index
                key = f"p{index}"
                if index >= len(values):
                    raise ValueError("Not enough SQL parameters supplied")
                named[key] = values[index]
                index += 1
                return f":{key}"

            return re.sub(r"\?", repl, sql), named
        return sql, params

    def execute(self, sql, params=None):
        sql, params = self._prepare(sql, params)
        result = self.conn.execute(text(sql), params)
        return result.mappings()

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()


def get_db():
    if "db" not in g:
        g.db = DatabaseSession()
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        if exception:
            db.rollback()
        db.close()

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def slugify(value):
    value = (value or "").lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "perfume"


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not session.get("admin_id"):
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)
    return wrapped_view


def setting(key, default=""):
    row = get_db().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def image_url(image_value):
    """Return the correct image URL for Cloudinary URLs or local upload filenames."""
    if not image_value:
        return ""
    image_value = str(image_value).strip()
    if image_value.startswith("http://") or image_value.startswith("https://"):
        return image_value
    return url_for("static", filename="uploads/" + image_value)


def upload_product_image(file_storage, product_name="perfume"):
    """Upload product image to Cloudinary when configured, else local static/uploads."""
    if not file_storage or not file_storage.filename:
        return None

    if not allowed_file(file_storage.filename):
        raise ValueError("Image must be png, jpg, jpeg, webp or gif.")

    if USE_CLOUDINARY:
        result = cloudinary.uploader.upload(
            file_storage,
            folder="scent-library/products",
            public_id=f"{slugify(product_name)}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
            overwrite=True,
            resource_type="image",
        )
        return result.get("secure_url")

    filename = secure_filename(file_storage.filename)
    filename = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{filename}"
    file_storage.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
    return filename


def upload_site_asset(file_storage, asset_name="site-asset"):
    """Upload site assets such as logos to Cloudinary, or local uploads in development."""
    if not file_storage or not file_storage.filename:
        return None

    if not allowed_file(file_storage.filename):
        raise ValueError("Image must be png, jpg, jpeg, webp or gif.")

    if USE_CLOUDINARY:
        result = cloudinary.uploader.upload(
            file_storage,
            folder="scent-library/brand",
            public_id=f"{slugify(asset_name)}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
            overwrite=True,
            resource_type="image",
        )
        return result.get("secure_url")

    filename = secure_filename(file_storage.filename)
    filename = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{filename}"
    file_storage.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
    return filename


@app.context_processor
def inject_globals():
    return {
        "site_name": setting("site_name", "The Scent Library"),
        "hero_title": setting("hero_title", "Find your next signature scent."),
        "tagline": setting("tagline", "Premium perfumes, inspired scents and layering combos in Mauritius."),
        "whatsapp_number": setting("whatsapp_number", "23050000000"),
        "instagram_url": setting("instagram_url", "https://instagram.com/scentlibrary.mu"),
        "currency": setting("currency", "Rs"),
        "business_email": setting("business_email", ""),
        "business_address": setting("business_address", "Mauritius"),
        "footer_note": setting("footer_note", "Curated scents, layering ideas and quick WhatsApp orders."),
        "logo_url": setting("logo_url", ""),
        "show_site_name_in_header": setting("show_site_name_in_header", "1"),
        "google_analytics_id": setting("google_analytics_id", ""),
        "quote_plus": quote_plus,
        "slugify": slugify,
        "image_url": image_url,
    }


def migrate_schema():
    """Add new columns safely when upgrading an existing database."""
    insp = inspect(engine)
    existing_tables = insp.get_table_names()
    if "products" not in existing_tables:
        return
    existing_cols = {col["name"] for col in insp.get_columns("products")}
    migrations = {
        "sku": "ALTER TABLE products ADD COLUMN sku TEXT",
        "scent_family": "ALTER TABLE products ADD COLUMN scent_family TEXT",
        "stock_qty": "ALTER TABLE products ADD COLUMN stock_qty INTEGER DEFAULT 0",
        "low_stock_threshold": "ALTER TABLE products ADD COLUMN low_stock_threshold INTEGER DEFAULT 2",
        "views": "ALTER TABLE products ADD COLUMN views INTEGER DEFAULT 0",
        "whatsapp_clicks": "ALTER TABLE products ADD COLUMN whatsapp_clicks INTEGER DEFAULT 0",
    }
    with engine.begin() as conn:
        for col, statement in migrations.items():
            if col not in existing_cols:
                conn.execute(text(statement))


def init_db():
    """Create database tables and seed default data if database is empty."""
    metadata.create_all(engine)
    migrate_schema()

    with engine.begin() as conn:
        admin_count = conn.execute(text("SELECT COUNT(*) AS c FROM admins")).mappings().fetchone()["c"]
        if admin_count == 0:
            conn.execute(
                text("""
                    INSERT INTO admins (username, password_hash, created_at, updated_at)
                    VALUES (:username, :password_hash, :created_at, :updated_at)
                """),
                {
                    "username": "admin",
                    "password_hash": generate_password_hash("admin123"),
                    "created_at": now_iso(),
                    "updated_at": now_iso(),
                },
            )

        defaults = {
            "site_name": "The Scent Library",
            "hero_title": "Find your next signature scent.",
            "tagline": "Premium perfumes, inspired scents and layering combos in Mauritius.",
            "whatsapp_number": "23050000000",
            "instagram_url": "https://instagram.com/scentlibrary.mu",
            "currency": "Rs",
            "business_email": "",
            "business_address": "Mauritius",
            "delivery_text": "Delivery available in Mauritius. Payment by MCB Juice or cash on delivery depending on location.",
            "homepage_notice": "New arrivals and layering combos available this week.",
            "footer_note": "Curated scents, layering ideas and quick WhatsApp orders.",
            "logo_url": "",
            "show_site_name_in_header": "1",
            "google_analytics_id": "",
        }
        for key, value in defaults.items():
            exists = conn.execute(text("SELECT 1 FROM settings WHERE key = :key"), {"key": key}).fetchone()
            if not exists:
                conn.execute(text("INSERT INTO settings (key, value) VALUES (:key, :value)"), {"key": key, "value": value})

        product_count = conn.execute(text("SELECT COUNT(*) AS c FROM products")).mappings().fetchone()["c"]
        if product_count == 0:
            # Seed demo products. Include all non-null stock columns explicitly so
            # PostgreSQL does not reject the insert when raw SQL is used.
            samples = [
                ("Amber Oud Carbon Edition", "Al Haramain", "Men", "60ml", "AOC-60", "Fresh / Aromatic", 5, 2, 1500, "Fresh, aromatic and modern. Good for office and evening wear.", "bergamot, lavender, woods", "Office / Night", None, 1, 1, 1),
                ("Khamrah Waha", "Lattafa", "Unisex", "100ml", "KW-100", "Sweet / Amber", 5, 2, 1800, "Sweet, warm and addictive. Perfect for colder evenings and special occasions.", "dates, vanilla, amber", "Evening", None, 1, 1, 2),
                ("Pacific Chill Inspiration", "The Scent Library", "Unisex", "50ml", "PC-50", "Fresh / Citrus", 5, 2, 900, "Fresh citrus scent with a clean summer feeling.", "citrus, mint, musk", "Summer / Day", None, 1, 1, 3),
                ("Ombre Nomade Inspiration", "The Scent Library", "Unisex", "50ml", "ON-50", "Oud / Rose", 5, 2, 950, "Deep oud style fragrance for strong projection.", "oud, rose, incense", "Night / Special Occasion", None, 0, 1, 4),
                ("Invictus Parfum Inspiration", "The Scent Library", "Men", "50ml", "IP-50", "Fresh / Sport", 5, 2, 850, "Sporty, clean and powerful masculine scent.", "marine notes, woods, amber", "Daily", None, 0, 1, 5),
                ("Kayali Inspired Mini Set", "The Scent Library", "Women", "5 x 10ml", "KAY-SET", "Sweet / Fruity", 5, 2, 1200, "A discovery set for layering and gifting.", "vanilla, musk, fruits", "Gift / Layering", None, 1, 1, 6),
            ]
            for p in samples:
                conn.execute(
                    text("""
                        INSERT INTO products
                        (name, brand, category, size, sku, scent_family, stock_qty, low_stock_threshold,
                         price, description, notes, occasion, image_filename,
                         is_featured, is_active, sort_order, created_at, updated_at)
                        VALUES (:name, :brand, :category, :size, :sku, :scent_family, :stock_qty, :low_stock_threshold,
                                :price, :description, :notes, :occasion, :image_filename,
                                :is_featured, :is_active, :sort_order, :created_at, :updated_at)
                    """),
                    {
                        "name": p[0], "brand": p[1], "category": p[2], "size": p[3], "sku": p[4],
                        "scent_family": p[5], "stock_qty": p[6], "low_stock_threshold": p[7], "price": p[8],
                        "description": p[9], "notes": p[10], "occasion": p[11], "image_filename": p[12],
                        "is_featured": p[13], "is_active": p[14], "sort_order": p[15],
                        "created_at": now_iso(), "updated_at": now_iso(),
                    },
                )

        combo_count = conn.execute(text("SELECT COUNT(*) AS c FROM combos")).mappings().fetchone()["c"]
        if combo_count == 0:
            combos = [
                ("Fresh Office", "Clean, professional and not too loud.", "Amber Oud Carbon", "Clean musk", 1),
                ("Sweet Night", "Warm, sweet and attractive for evening wear.", "Khamrah Waha", "Vanilla scent", 2),
                ("Oud Statement", "Strong projection for special occasions.", "Ombre style", "Rose / incense", 3),
            ]
            for title, desc, p1, p2, order in combos:
                conn.execute(
                    text("""
                        INSERT INTO combos (title, description, product_1, product_2, is_active, sort_order, created_at, updated_at)
                        VALUES (:title, :description, :product_1, :product_2, 1, :sort_order, :created_at, :updated_at)
                    """),
                    {
                        "title": title, "description": desc, "product_1": p1, "product_2": p2,
                        "sort_order": order, "created_at": now_iso(), "updated_at": now_iso(),
                    },
                )


        banner_count = conn.execute(text("SELECT COUNT(*) AS c FROM banners")).mappings().fetchone()["c"]
        if banner_count == 0:
            conn.execute(
                text("""
                    INSERT INTO banners (title, subtitle, image_url, button_text, button_link, is_active, sort_order, created_at, updated_at)
                    VALUES (:title, :subtitle, :image_url, :button_text, :button_link, 1, 1, :created_at, :updated_at)
                """),
                {
                    "title": "New arrivals now available",
                    "subtitle": "Upload banners from admin to promote perfume drops, gift sets and offers.",
                    "image_url": "",
                    "button_text": "View perfumes",
                    "button_link": "#products",
                    "created_at": now_iso(),
                    "updated_at": now_iso(),
                },
            )

def product_query(active_only=True):
    q = request.args.get("q", "").strip()
    category = request.args.get("category", "All")
    family = request.args.get("family", "All")
    sql = "SELECT * FROM products WHERE 1=1"
    params = []
    if active_only:
        sql += " AND is_active = 1"
    if q:
        sql += " AND (name LIKE ? OR brand LIKE ? OR description LIKE ? OR notes LIKE ? OR occasion LIKE ? OR sku LIKE ? OR scent_family LIKE ?)"
        like = f"%{q}%"
        params.extend([like, like, like, like, like, like, like])
    if category and category != "All":
        sql += " AND category = ?"
        params.append(category)
    if family and family != "All":
        sql += " AND scent_family = ?"
        params.append(family)
    sql += " ORDER BY is_featured DESC, sort_order ASC, name ASC"
    return get_db().execute(sql, params).fetchall(), q, category, family


@app.route("/")
def index():
    products, q, selected_category, selected_family = product_query(active_only=True)
    featured = get_db().execute(
        "SELECT * FROM products WHERE is_active = 1 AND is_featured = 1 ORDER BY sort_order ASC, name ASC LIMIT 6"
    ).fetchall()
    combos = get_db().execute(
        "SELECT * FROM combos WHERE is_active = 1 ORDER BY sort_order ASC, title ASC"
    ).fetchall()
    banners = get_db().execute(
        "SELECT * FROM banners WHERE is_active = 1 ORDER BY sort_order ASC, id DESC"
    ).fetchall()
    categories = ["All", "Men", "Women", "Unisex"]
    families = ["All"] + [row["scent_family"] for row in get_db().execute("SELECT DISTINCT scent_family FROM products WHERE is_active = 1 AND COALESCE(scent_family,'') <> '' ORDER BY scent_family").fetchall()]
    return render_template(
        "index.html",
        products=products,
        featured=featured,
        combos=combos,
        banners=banners,
        categories=categories,
        selected_category=selected_category,
        selected_family=selected_family,
        families=families,
        q=q,
        delivery_text=setting("delivery_text"),
        homepage_notice=setting("homepage_notice"),
    )


@app.route("/product/<int:product_id>/<slug>")
def product_detail(product_id, slug):
    db = get_db()
    product = db.execute("SELECT * FROM products WHERE id = ? AND is_active = 1", (product_id,)).fetchone()
    if not product:
        abort(404)
    db.execute("UPDATE products SET views = COALESCE(views, 0) + 1 WHERE id = ?", (product_id,))
    db.commit()
    gallery = db.execute("SELECT * FROM product_images WHERE product_id = ? ORDER BY sort_order ASC, id ASC", (product_id,)).fetchall()
    related = db.execute(
        "SELECT * FROM products WHERE is_active = 1 AND category = ? AND id <> ? ORDER BY is_featured DESC, sort_order ASC LIMIT 3",
        (product["category"], product_id)
    ).fetchall()
    return render_template("product_detail.html", product=product, gallery=gallery, related=related)


@app.route("/order-request", methods=["POST"])
def order_request():
    customer_name = request.form.get("customer_name", "").strip()
    phone = request.form.get("phone", "").strip()
    location = request.form.get("location", "").strip()
    message = request.form.get("message", "").strip()
    product_id = request.form.get("product_id") or None
    product_name = request.form.get("product_name", "").strip()
    if not customer_name or not phone:
        flash("Please enter your name and phone number.", "error")
        return redirect(request.referrer or url_for("index"))
    db = get_db()
    db.execute(
        """
        INSERT INTO order_requests (customer_name, phone, location, product_id, product_name, message, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 'New', ?, ?)
        """,
        (customer_name, phone, location, product_id, product_name, message, now_iso(), now_iso())
    )
    db.commit()
    flash("Your request has been sent. We will contact you soon.", "success")
    return redirect(url_for("index") + "#contact")



@app.route("/track/whatsapp/product/<int:product_id>")
def track_whatsapp_product(product_id):
    product = get_db().execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not product:
        abort(404)
    get_db().execute("UPDATE products SET whatsapp_clicks = COALESCE(whatsapp_clicks, 0) + 1 WHERE id = ?", (product_id,))
    get_db().commit()
    size = product["size"] or ""
    message = f"Hi, I am interested in {product['name']} {size}. Is it available?"
    return redirect(f"https://wa.me/{setting('whatsapp_number', '23050000000')}?text={quote_plus(message)}")

@app.route("/track/whatsapp/restock/<int:product_id>")
def track_whatsapp_restock(product_id):
    product = get_db().execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not product:
        abort(404)
    get_db().execute("UPDATE products SET whatsapp_clicks = COALESCE(whatsapp_clicks, 0) + 1 WHERE id = ?", (product_id,))
    get_db().commit()
    message = f"Hi, please tell me when {product['name']} is back in stock."
    return redirect(f"https://wa.me/{setting('whatsapp_number', '23050000000')}?text={quote_plus(message)}")



@app.route("/catalogue.pdf")
def download_catalogue():
    """Generate a downloadable PDF catalogue from the live product database."""
    from xml.sax.saxutils import escape
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image,
        KeepTogether, PageBreak
    )

    def clean(value, default=""):
        if value is None:
            return default
        return escape(str(value).strip())

    def image_flowable(image_value, max_width, max_height):
        """Return a ReportLab Image flowable, preserving aspect ratio."""
        if not image_value:
            return None
        image_value = str(image_value).strip()
        try:
            if image_value.startswith("http://") or image_value.startswith("https://"):
                req = Request(image_value, headers={"User-Agent": "Mozilla/5.0"})
                image_bytes = urlopen(req, timeout=8).read()
                image_file = io.BytesIO(image_bytes)
            else:
                image_file = os.path.join(app.config["UPLOAD_FOLDER"], image_value)
                if not os.path.exists(image_file):
                    return None
            reader = ImageReader(image_file)
            iw, ih = reader.getSize()
            if not iw or not ih:
                return None
            scale = min(max_width / float(iw), max_height / float(ih))
            width = iw * scale
            height = ih * scale
            if hasattr(image_file, "seek"):
                image_file.seek(0)
            img = Image(image_file, width=width, height=height)
            img.hAlign = "CENTER"
            return img
        except Exception:
            return None

    products = get_db().execute(
        """
        SELECT * FROM products
        WHERE is_active = 1
        ORDER BY is_featured DESC, sort_order ASC, name ASC
        """
    ).fetchall()

    site = setting("site_name", "The Scent Library")
    tagline_value = setting("tagline", "Premium perfumes, inspired scents and layering combos in Mauritius.")
    currency_value = setting("currency", "Rs")
    whatsapp_value = setting("whatsapp_number", "23050000000")
    instagram_value = setting("instagram_url", "")
    address_value = setting("business_address", "Mauritius")
    logo_value = setting("logo_url", "")

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=14 * mm,
        leftMargin=14 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"{site} Catalogue",
        author=site,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "CatalogueTitle", parent=styles["Title"], fontName="Helvetica-Bold",
        fontSize=26, leading=30, textColor=colors.HexColor("#20150C"), alignment=TA_CENTER,
        spaceAfter=6,
    )
    sub_style = ParagraphStyle(
        "CatalogueSub", parent=styles["BodyText"], fontName="Helvetica",
        fontSize=10.5, leading=15, textColor=colors.HexColor("#6B5A49"), alignment=TA_CENTER,
        spaceAfter=16,
    )
    section_style = ParagraphStyle(
        "Section", parent=styles["Heading2"], fontName="Helvetica-Bold",
        fontSize=14, leading=18, textColor=colors.HexColor("#8A5A24"), spaceBefore=8, spaceAfter=8,
    )
    name_style = ParagraphStyle(
        "ProductName", parent=styles["Heading3"], fontName="Helvetica-Bold",
        fontSize=15, leading=18, textColor=colors.HexColor("#20150C"), spaceAfter=3,
    )
    meta_style = ParagraphStyle(
        "Meta", parent=styles["BodyText"], fontName="Helvetica-Bold",
        fontSize=8.5, leading=11, textColor=colors.HexColor("#8A5A24"), spaceAfter=5,
    )
    body_style = ParagraphStyle(
        "ProductBody", parent=styles["BodyText"], fontName="Helvetica",
        fontSize=9, leading=12.5, textColor=colors.HexColor("#55483B"), spaceAfter=4,
    )
    price_style = ParagraphStyle(
        "Price", parent=styles["BodyText"], fontName="Helvetica-Bold",
        fontSize=12, leading=14, textColor=colors.HexColor("#20150C"), spaceBefore=4,
    )
    small_style = ParagraphStyle(
        "Small", parent=styles["BodyText"], fontName="Helvetica",
        fontSize=8, leading=10.5, textColor=colors.HexColor("#7B6B5C"),
    )
    right_small_style = ParagraphStyle(
        "RightSmall", parent=small_style, alignment=TA_RIGHT,
    )

    story = []
    logo = image_flowable(logo_value, 42 * mm, 18 * mm)
    if logo:
        story.append(logo)
        story.append(Spacer(1, 5 * mm))
    story.append(Paragraph(clean(site), title_style))
    story.append(Paragraph(clean(tagline_value), sub_style))

    info_table = Table(
        [[
            Paragraph(f"<b>WhatsApp:</b> {clean(whatsapp_value)}<br/><b>Instagram:</b> {clean(instagram_value)}", small_style),
            Paragraph(f"<b>Catalogue generated:</b><br/>{datetime.now().strftime('%d %b %Y')}", right_small_style),
        ]],
        colWidths=[95 * mm, 75 * mm],
    )
    info_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFF8EC")),
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#E8D7BE")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 7 * mm))
    story.append(Paragraph("Perfume Catalogue", section_style))

    if not products:
        story.append(Paragraph("No active products are available in the catalogue yet.", body_style))
    else:
        for product in products:
            image_cell = image_flowable(product["image_filename"], 42 * mm, 52 * mm)
            if image_cell is None:
                image_cell = Paragraph("No image", small_style)

            stock_qty = int(product["stock_qty"] or 0)
            stock_text = "Available" if stock_qty > 0 else "Out of stock"
            if stock_qty > 0 and stock_qty <= int(product["low_stock_threshold"] or 2):
                stock_text = "Low stock"
            featured_text = "Best seller" if int(product["is_featured"] or 0) == 1 else ""
            meta_bits = [
                clean(product["brand"] or "The Scent Library"),
                clean(product["category"]),
                clean(product["size"]),
            ]
            if product["scent_family"]:
                meta_bits.append(clean(product["scent_family"]))
            if featured_text:
                meta_bits.append(featured_text)

            details = [
                Paragraph(clean(product["name"]), name_style),
                Paragraph(" · ".join([bit for bit in meta_bits if bit]), meta_style),
                Paragraph(clean(product["description"] or ""), body_style),
            ]
            if product["notes"]:
                details.append(Paragraph(f"<b>Notes:</b> {clean(product['notes'])}", body_style))
            if product["occasion"]:
                details.append(Paragraph(f"<b>Occasion:</b> {clean(product['occasion'])}", body_style))
            details.append(Paragraph(f"{clean(currency_value)} {float(product['price'] or 0):,.0f} &nbsp;&nbsp; | &nbsp;&nbsp; {stock_text}", price_style))
            if product["sku"]:
                details.append(Paragraph(f"SKU: {clean(product['sku'])}", small_style))

            card = Table([[image_cell, details]], colWidths=[50 * mm, 120 * mm])
            card.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFFCF6")),
                ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#E8D7BE")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]))
            story.append(KeepTogether([card, Spacer(1, 5 * mm)]))

    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(
        f"To order, send us a WhatsApp message on {clean(whatsapp_value)}. Prices and stock may change without notice.",
        small_style,
    ))

    def page_footer(canvas, pdf_doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#7B6B5C"))
        canvas.drawString(14 * mm, 9 * mm, site)
        canvas.drawRightString(A4[0] - 14 * mm, 9 * mm, f"Page {pdf_doc.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=page_footer, onLaterPages=page_footer)
    pdf_data = buffer.getvalue()
    buffer.close()
    filename = f"{slugify(site)}-catalogue.pdf"
    return Response(
        pdf_data,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )

@app.route("/sitemap.xml")
def sitemap_xml():
    products = get_db().execute("SELECT id, name, updated_at FROM products WHERE is_active = 1 ORDER BY updated_at DESC").fetchall()
    base_url = request.url_root.rstrip("/")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        f'<url><loc>{base_url}/</loc><changefreq>weekly</changefreq><priority>1.0</priority></url>',
    ]
    for p in products:
        lines.append(f'<url><loc>{base_url}{url_for("product_detail", product_id=p["id"], slug=slugify(p["name"]))}</loc><changefreq>weekly</changefreq><priority>0.8</priority></url>')
    lines.append('</urlset>')
    return Response("\n".join(lines), mimetype="application/xml")


@app.route("/robots.txt")
def robots_txt():
    return Response("User-agent: *\nAllow: /\nSitemap: " + request.url_root.rstrip("/") + "/sitemap.xml\n", mimetype="text/plain")


# ------------------------- Admin -------------------------

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        admin = get_db().execute("SELECT * FROM admins WHERE username = ?", (username,)).fetchone()
        if admin and check_password_hash(admin["password_hash"], password):
            session.clear()
            session["admin_id"] = admin["id"]
            session["admin_username"] = admin["username"]
            flash("Welcome back.", "success")
            return redirect(request.args.get("next") or url_for("admin_dashboard"))
        flash("Invalid username or password.", "error")
    return render_template("admin/login.html")


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("admin_login"))


@app.route("/admin")
@login_required
def admin_dashboard():
    products = get_db().execute("SELECT * FROM products ORDER BY sort_order ASC, updated_at DESC").fetchall()
    stats = {
        "total": get_db().execute("SELECT COUNT(*) AS c FROM products").fetchone()["c"],
        "active": get_db().execute("SELECT COUNT(*) AS c FROM products WHERE is_active = 1").fetchone()["c"],
        "featured": get_db().execute("SELECT COUNT(*) AS c FROM products WHERE is_featured = 1").fetchone()["c"],
        "orders": get_db().execute("SELECT COUNT(*) AS c FROM order_requests WHERE status = 'New'").fetchone()["c"],
        "low_stock": get_db().execute("SELECT COUNT(*) AS c FROM products WHERE is_active = 1 AND stock_qty <= low_stock_threshold").fetchone()["c"],
        "out_of_stock": get_db().execute("SELECT COUNT(*) AS c FROM products WHERE is_active = 1 AND stock_qty <= 0").fetchone()["c"],
        "views": get_db().execute("SELECT COALESCE(SUM(views),0) AS c FROM products").fetchone()["c"],
        "clicks": get_db().execute("SELECT COALESCE(SUM(whatsapp_clicks),0) AS c FROM products").fetchone()["c"],
    }
    recent_orders = get_db().execute("SELECT * FROM order_requests ORDER BY created_at DESC LIMIT 5").fetchall()
    low_stock = get_db().execute("SELECT * FROM products WHERE is_active = 1 AND stock_qty <= low_stock_threshold ORDER BY stock_qty ASC, name ASC LIMIT 6").fetchall()
    top_products = get_db().execute("SELECT * FROM products ORDER BY COALESCE(views,0) DESC, COALESCE(whatsapp_clicks,0) DESC, name ASC LIMIT 6").fetchall()
    return render_template("admin/dashboard.html", products=products, stats=stats, recent_orders=recent_orders, low_stock=low_stock, top_products=top_products)


@app.route("/admin/products/new", methods=["GET", "POST"])
@login_required
def admin_product_new():
    if request.method == "POST":
        return save_product()
    return render_template("admin/product_form.html", product=None, gallery=[], action="Add product")


@app.route("/admin/products/<int:product_id>/edit", methods=["GET", "POST"])
@login_required
def admin_product_edit(product_id):
    product = get_db().execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not product:
        flash("Product not found.", "error")
        return redirect(url_for("admin_dashboard"))
    if request.method == "POST":
        return save_product(product_id)
    gallery = get_db().execute("SELECT * FROM product_images WHERE product_id = ? ORDER BY sort_order ASC, id ASC", (product_id,)).fetchall()
    return render_template("admin/product_form.html", product=product, gallery=gallery, action="Edit product")


def save_product(product_id=None):
    name = request.form.get("name", "").strip()
    if not name:
        flash("Product name is required.", "error")
        return redirect(request.url)

    image_filename = request.form.get("existing_image", "") or None
    file = request.files.get("image")
    if file and file.filename:
        try:
            uploaded_image = upload_product_image(file, name)
            if uploaded_image:
                image_filename = uploaded_image
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(request.url)
        except Exception as exc:
            flash(f"Image upload failed: {exc}", "error")
            return redirect(request.url)

    try:
        price = float(request.form.get("price") or 0)
    except ValueError:
        price = 0
    try:
        sort_order = int(request.form.get("sort_order") or 0)
    except ValueError:
        sort_order = 0
    try:
        stock_qty = int(request.form.get("stock_qty") or 0)
    except ValueError:
        stock_qty = 0
    try:
        low_stock_threshold = int(request.form.get("low_stock_threshold") or 2)
    except ValueError:
        low_stock_threshold = 2

    data = {
        "name": name,
        "brand": request.form.get("brand", "").strip(),
        "category": request.form.get("category", "Unisex"),
        "size": request.form.get("size", "").strip(),
        "sku": request.form.get("sku", "").strip(),
        "scent_family": request.form.get("scent_family", "").strip(),
        "stock_qty": stock_qty,
        "low_stock_threshold": low_stock_threshold,
        "price": price,
        "description": request.form.get("description", "").strip(),
        "notes": request.form.get("notes", "").strip(),
        "occasion": request.form.get("occasion", "").strip(),
        "image_filename": image_filename,
        "is_featured": 1 if request.form.get("is_featured") else 0,
        "is_active": 1 if request.form.get("is_active") else 0,
        "sort_order": sort_order,
        "updated_at": now_iso(),
    }

    db = get_db()
    saved_product_id = product_id
    if product_id:
        db.execute(
            """
            UPDATE products SET
            name=:name, brand=:brand, category=:category, size=:size, sku=:sku, scent_family=:scent_family,
            stock_qty=:stock_qty, low_stock_threshold=:low_stock_threshold, price=:price,
            description=:description, notes=:notes, occasion=:occasion, image_filename=:image_filename,
            is_featured=:is_featured, is_active=:is_active, sort_order=:sort_order, updated_at=:updated_at
            WHERE id=:id
            """,
            {**data, "id": product_id}
        )
        flash("Product updated.", "success")
    else:
        db.execute(
            """
            INSERT INTO products
            (name, brand, category, size, sku, scent_family, stock_qty, low_stock_threshold, price, description, notes, occasion, image_filename, is_featured, is_active, sort_order, created_at, updated_at)
            VALUES (:name, :brand, :category, :size, :sku, :scent_family, :stock_qty, :low_stock_threshold, :price, :description, :notes, :occasion, :image_filename, :is_featured, :is_active, :sort_order, :created_at, :updated_at)
            """,
            {**data, "created_at": now_iso()}
        )
        saved_product_id = db.execute("SELECT id FROM products WHERE name = ? ORDER BY id DESC LIMIT 1", (name,)).fetchone()["id"]
        flash("Product added.", "success")

    # Optional gallery images. These are extra photos shown on the product detail page.
    gallery_files = request.files.getlist("gallery_images")
    next_sort = db.execute("SELECT COALESCE(MAX(sort_order),0) AS m FROM product_images WHERE product_id = ?", (saved_product_id,)).fetchone()["m"] or 0
    for file_item in gallery_files:
        if file_item and file_item.filename:
            try:
                gallery_url = upload_product_image(file_item, name)
                if gallery_url:
                    next_sort += 1
                    db.execute(
                        """
                        INSERT INTO product_images (product_id, image_url, caption, sort_order, is_primary, created_at)
                        VALUES (?, ?, ?, ?, 0, ?)
                        """,
                        (saved_product_id, gallery_url, request.form.get("gallery_caption", "").strip(), next_sort, now_iso())
                    )
                    if not image_filename:
                        db.execute("UPDATE products SET image_filename = ? WHERE id = ?", (gallery_url, saved_product_id))
            except Exception as exc:
                flash(f"One gallery image could not upload: {exc}", "error")

    db.commit()
    return redirect(url_for("admin_product_edit", product_id=saved_product_id))


@app.route("/admin/products/<int:product_id>/delete", methods=["POST"])
@login_required
def admin_product_delete(product_id):
    db = get_db()
    db.execute("DELETE FROM products WHERE id = ?", (product_id,))
    db.commit()
    flash("Product deleted.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/products/export.csv")
@login_required
def export_products():
    products = get_db().execute("SELECT * FROM products ORDER BY sort_order ASC, name ASC").fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Name", "Brand", "Category", "Size", "SKU", "Scent Family", "Stock", "Low Stock Threshold", "Price", "Notes", "Occasion", "Featured", "Active"])
    for p in products:
        writer.writerow([p["name"], p["brand"], p["category"], p["size"], p["sku"], p["scent_family"], p["stock_qty"], p["low_stock_threshold"], p["price"], p["notes"], p["occasion"], p["is_featured"], p["is_active"]])
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=scent-library-products.csv"})


@app.route("/admin/products/import", methods=["GET", "POST"])
@login_required
def import_products():
    if request.method == "POST":
        file = request.files.get("csv_file")
        if not file or not file.filename:
            flash("Choose a CSV file first.", "error")
            return redirect(url_for("import_products"))
        text_stream = io.StringIO(file.stream.read().decode("utf-8-sig"))
        reader = csv.DictReader(text_stream)
        added = 0
        db = get_db()
        for row in reader:
            name = (row.get("name") or row.get("Name") or "").strip()
            if not name:
                continue
            price = float(row.get("price") or row.get("Price") or 0)
            stock_qty = int(float(row.get("stock_qty") or row.get("Stock") or 0))
            low_stock_threshold = int(float(row.get("low_stock_threshold") or row.get("Low Stock Threshold") or 2))
            db.execute(
                """
                INSERT INTO products
                (name, brand, category, size, sku, scent_family, stock_qty, low_stock_threshold, price, description, notes, occasion, image_filename, is_featured, is_active, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    row.get("brand") or row.get("Brand") or "",
                    row.get("category") or row.get("Category") or "Unisex",
                    row.get("size") or row.get("Size") or "",
                    row.get("sku") or row.get("SKU") or "",
                    row.get("scent_family") or row.get("Scent Family") or "",
                    stock_qty, low_stock_threshold, price,
                    row.get("description") or row.get("Description") or "",
                    row.get("notes") or row.get("Notes") or "",
                    row.get("occasion") or row.get("Occasion") or "",
                    row.get("image_url") or row.get("Image URL") or "",
                    1 if str(row.get("featured") or row.get("Featured") or "0").lower() in ["1","yes","true"] else 0,
                    0 if str(row.get("active") or row.get("Active") or "1").lower() in ["0","no","false"] else 1,
                    int(float(row.get("sort_order") or row.get("Sort order") or 0)),
                    now_iso(), now_iso(),
                )
            )
            added += 1
        db.commit()
        flash(f"Imported {added} product(s).", "success")
        return redirect(url_for("admin_dashboard"))
    return render_template("admin/import_products.html")


@app.route("/admin/products/template.csv")
@login_required
def product_import_template():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["name","brand","category","size","sku","scent_family","stock_qty","low_stock_threshold","price","description","notes","occasion","image_url","featured","active","sort_order"])
    writer.writerow(["Example Perfume","Brand","Unisex","50ml","EX-50","Fresh / Citrus",5,2,900,"Short description","citrus, musk","Daily","",1,1,1])
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=product-import-template.csv"})


@app.route("/admin/product-images/<int:image_id>/delete", methods=["POST"])
@login_required
def admin_product_image_delete(image_id):
    row = get_db().execute("SELECT * FROM product_images WHERE id = ?", (image_id,)).fetchone()
    if not row:
        flash("Image not found.", "error")
        return redirect(url_for("admin_dashboard"))
    product_id = row["product_id"]
    get_db().execute("DELETE FROM product_images WHERE id = ?", (image_id,))
    get_db().commit()
    flash("Gallery image removed.", "success")
    return redirect(url_for("admin_product_edit", product_id=product_id))


@app.route("/admin/banners", methods=["GET", "POST"])
@login_required
def admin_banners():
    db = get_db()
    if request.method == "POST":
        banner_id = request.form.get("banner_id")
        title = request.form.get("title", "").strip()
        if not title:
            flash("Banner title is required.", "error")
            return redirect(url_for("admin_banners"))
        image_url_value = request.form.get("existing_image", "").strip()
        file = request.files.get("image")
        if file and file.filename:
            image_url_value = upload_product_image(file, title)
        data = {
            "title": title,
            "subtitle": request.form.get("subtitle", "").strip(),
            "image_url": image_url_value,
            "button_text": request.form.get("button_text", "").strip(),
            "button_link": request.form.get("button_link", "#products").strip(),
            "is_active": 1 if request.form.get("is_active") else 0,
            "sort_order": int(request.form.get("sort_order") or 0),
            "updated_at": now_iso(),
        }
        if banner_id:
            db.execute("""UPDATE banners SET title=:title, subtitle=:subtitle, image_url=:image_url, button_text=:button_text, button_link=:button_link, is_active=:is_active, sort_order=:sort_order, updated_at=:updated_at WHERE id=:id""", {**data, "id": banner_id})
            flash("Banner updated.", "success")
        else:
            db.execute("""INSERT INTO banners (title, subtitle, image_url, button_text, button_link, is_active, sort_order, created_at, updated_at) VALUES (:title, :subtitle, :image_url, :button_text, :button_link, :is_active, :sort_order, :created_at, :updated_at)""", {**data, "created_at": now_iso()})
            flash("Banner added.", "success")
        db.commit()
        return redirect(url_for("admin_banners"))
    banners = db.execute("SELECT * FROM banners ORDER BY sort_order ASC, id DESC").fetchall()
    return render_template("admin/banners.html", banners=banners)


@app.route("/admin/banners/<int:banner_id>/delete", methods=["POST"])
@login_required
def admin_banner_delete(banner_id):
    get_db().execute("DELETE FROM banners WHERE id = ?", (banner_id,))
    get_db().commit()
    flash("Banner deleted.", "success")
    return redirect(url_for("admin_banners"))


@app.route("/admin/export.json")
@login_required
def export_backup_json():
    import json
    data = {
        "products": [dict(x) for x in get_db().execute("SELECT * FROM products ORDER BY id").fetchall()],
        "product_images": [dict(x) for x in get_db().execute("SELECT * FROM product_images ORDER BY id").fetchall()],
        "banners": [dict(x) for x in get_db().execute("SELECT * FROM banners ORDER BY id").fetchall()],
        "combos": [dict(x) for x in get_db().execute("SELECT * FROM combos ORDER BY id").fetchall()],
        "order_requests": [dict(x) for x in get_db().execute("SELECT * FROM order_requests ORDER BY id").fetchall()],
        "settings": [dict(x) for x in get_db().execute("SELECT * FROM settings ORDER BY key").fetchall()],
        "exported_at": now_iso(),
    }
    return Response(json.dumps(data, indent=2), mimetype="application/json", headers={"Content-Disposition": "attachment; filename=scent-library-backup.json"})




@app.route("/admin/orders", methods=["GET", "POST"])
@login_required
def admin_orders():
    if request.method == "POST":
        order_id = request.form.get("order_id")
        status = request.form.get("status", "New")
        get_db().execute("UPDATE order_requests SET status = ?, updated_at = ? WHERE id = ?", (status, now_iso(), order_id))
        get_db().commit()
        flash("Order status updated.", "success")
        return redirect(url_for("admin_orders"))
    status_filter = request.args.get("status", "").strip()
    if status_filter:
        orders = get_db().execute("SELECT * FROM order_requests WHERE status = ? ORDER BY created_at DESC", (status_filter,)).fetchall()
    else:
        orders = get_db().execute("SELECT * FROM order_requests ORDER BY created_at DESC").fetchall()
    return render_template("admin/orders.html", orders=orders, status_filter=status_filter)


@app.route("/admin/orders/<int:order_id>/delete", methods=["POST"])
@login_required
def admin_order_delete(order_id):
    get_db().execute("DELETE FROM order_requests WHERE id = ?", (order_id,))
    get_db().commit()
    flash("Request deleted.", "success")
    return redirect(url_for("admin_orders"))


@app.route("/admin/combos", methods=["GET", "POST"])
@login_required
def admin_combos():
    db = get_db()
    if request.method == "POST":
        combo_id = request.form.get("combo_id")
        title = request.form.get("title", "").strip()
        if not title:
            flash("Combo title is required.", "error")
            return redirect(url_for("admin_combos"))
        data = (
            title,
            request.form.get("description", "").strip(),
            request.form.get("product_1", "").strip(),
            request.form.get("product_2", "").strip(),
            1 if request.form.get("is_active") else 0,
            int(request.form.get("sort_order") or 0),
            now_iso(),
        )
        if combo_id:
            db.execute(
                "UPDATE combos SET title=?, description=?, product_1=?, product_2=?, is_active=?, sort_order=?, updated_at=? WHERE id=?",
                (*data, combo_id)
            )
            flash("Combo updated.", "success")
        else:
            db.execute(
                "INSERT INTO combos (title, description, product_1, product_2, is_active, sort_order, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (*data, now_iso())
            )
            flash("Combo added.", "success")
        db.commit()
        return redirect(url_for("admin_combos"))
    combos = db.execute("SELECT * FROM combos ORDER BY sort_order ASC, title ASC").fetchall()
    return render_template("admin/combos.html", combos=combos)


@app.route("/admin/combos/<int:combo_id>/delete", methods=["POST"])
@login_required
def admin_combo_delete(combo_id):
    get_db().execute("DELETE FROM combos WHERE id = ?", (combo_id,))
    get_db().commit()
    flash("Combo deleted.", "success")
    return redirect(url_for("admin_combos"))


@app.route("/admin/settings", methods=["GET", "POST"])
@login_required
def admin_settings():
    keys = [
        "site_name", "hero_title", "tagline", "whatsapp_number", "instagram_url",
        "currency", "business_email", "business_address", "delivery_text", "homepage_notice",
        "footer_note", "google_analytics_id", "show_site_name_in_header"
    ]

    def save_setting(db, key, value):
        exists = db.execute("SELECT 1 FROM settings WHERE key = ?", (key,)).fetchone()
        if exists:
            db.execute("UPDATE settings SET value = ? WHERE key = ?", (value, key))
        else:
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))

    if request.method == "POST":
        db = get_db()
        for key in keys:
            if key == "show_site_name_in_header":
                value = "1" if request.form.get(key) == "1" else "0"
            else:
                value = request.form.get(key, "").strip()
            save_setting(db, key, value)

        try:
            if request.form.get("remove_logo") == "1":
                save_setting(db, "logo_url", "")
            else:
                logo_file = request.files.get("logo_file")
                if logo_file and logo_file.filename:
                    logo_url = upload_site_asset(logo_file, "logo")
                    if logo_url:
                        save_setting(db, "logo_url", logo_url)
        except ValueError as exc:
            db.rollback()
            flash(str(exc), "error")
            return redirect(url_for("admin_settings"))

        db.commit()
        flash("Settings saved.", "success")
        return redirect(url_for("admin_settings"))
    settings = {row["key"]: row["value"] for row in get_db().execute("SELECT * FROM settings").fetchall()}
    return render_template("admin/settings.html", settings=settings)


@app.route("/admin/password", methods=["GET", "POST"])
@login_required
def admin_password():
    admin = get_db().execute("SELECT * FROM admins WHERE id = ?", (session["admin_id"],)).fetchone()
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if not check_password_hash(admin["password_hash"], current):
            flash("Current password is incorrect.", "error")
        elif len(new_password) < 8:
            flash("New password must have at least 8 characters.", "error")
        elif new_password != confirm:
            flash("New passwords do not match.", "error")
        else:
            get_db().execute("UPDATE admins SET password_hash = ?, updated_at = ? WHERE id = ?", (generate_password_hash(new_password), now_iso(), admin["id"]))
            get_db().commit()
            flash("Password changed.", "success")
            return redirect(url_for("admin_dashboard"))
    return render_template("admin/password.html")


init_db()

if __name__ == "__main__":
    app.run(debug=True)
