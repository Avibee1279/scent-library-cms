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
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25MB upload limit for catalogue PDFs/images

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


def upload_catalogue_file(file_storage):
    """Upload final catalogue PDFs/images to Cloudinary, or local uploads in development."""
    if not file_storage or not file_storage.filename:
        return None

    ext = file_storage.filename.rsplit(".", 1)[-1].lower() if "." in file_storage.filename else ""
    if ext not in {"pdf", "png", "jpg", "jpeg", "webp"}:
        raise ValueError("Catalogue must be a PDF or image file: pdf, png, jpg, jpeg or webp.")

    safe_name = secure_filename(file_storage.filename)
    name_without_ext = safe_name.rsplit(".", 1)[0] if "." in safe_name else "catalogue"
    base_id = f"catalogue-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{slugify(name_without_ext)}"
    if USE_CLOUDINARY:
        # PDFs must be uploaded as RAW files on Cloudinary. Include the .pdf
        # extension in the public_id so the downloaded file keeps a proper name.
        resource_type = "raw" if ext == "pdf" else "image"
        public_id = f"{base_id}.pdf" if ext == "pdf" else base_id
        try:
            file_storage.stream.seek(0)
        except Exception:
            pass
        result = cloudinary.uploader.upload(
            file_storage,
            folder="scent-library/catalogues",
            public_id=public_id,
            overwrite=True,
            resource_type=resource_type,
        )
        return result.get("secure_url")

    filename = secure_filename(file_storage.filename)
    filename = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{filename}"
    file_storage.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
    return filename


def catalogue_download_url(value):
    """Return a URL for an uploaded catalogue file."""
    if not value:
        return ""
    value = str(value).strip()
    if value.startswith("http://") or value.startswith("https://"):
        return value
    return url_for("static", filename="uploads/" + value)


def save_setting_value(db, key, value):
    exists = db.execute("SELECT 1 FROM settings WHERE key = ?", (key,)).fetchone()
    if exists:
        db.execute("UPDATE settings SET value = ? WHERE key = ?", (value, key))
    else:
        db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))


def build_catalogue_ai_prompt(products, settings):
    """Create a rich prompt that the admin can copy into an AI design tool."""
    site = settings.get("site_name") or "The Scent Library"
    currency_value = settings.get("currency") or "Rs"
    instagram = settings.get("instagram_url") or "https://instagram.com/scentlibrary.mu"
    whatsapp = settings.get("whatsapp_number") or ""
    logo = settings.get("logo_url") or ""

    lines = [
        f'Create a polished luxury A4 portrait perfume catalogue for "{site}".',
        "",
        "Visual style:",
        "Elegant cream and warm white background, subtle gold accents, thin decorative gold border, luxury boutique perfume aesthetic, premium serif typography, soft shadows, clean editorial layout, lots of white space. It must look like a real downloadable perfume catalogue, not a website screenshot.",
        "",
        "Required pages:",
        "1. Luxury cover page with brand name, logo if supplied, and a premium hero composition.",
        "2. Best Sellers / Most Wanted page using featured products first.",
        "3. Product catalogue grid pages with clear product photos, prices, brand, scent family and stock status.",
        "4. Final order/enquiry page with WhatsApp and Instagram details.",
        "",
        "Brand details:",
        f"Brand: {site}",
        f"Logo URL: {logo or 'No logo supplied'}",
        f"Instagram: {instagram}",
        f"WhatsApp: {whatsapp or 'WhatsApp orders available'}",
        "Footer text: Catalogue generated from our live collection. Prices and availability are subject to change.",
        "",
        "Product data to include:",
    ]
    for i, p in enumerate(products, 1):
        try:
            price = float(p.get("price") or 0)
            price_text = f"{currency_value} {price:,.0f}".replace(",", " ")
        except Exception:
            price_text = f"{currency_value} {p.get('price') or ''}".strip()
        image = p.get("image_url") or p.get("image_filename") or ""
        stock = int(p.get("stock_qty") or 0)
        stock_text = "In stock" if stock > 0 else "Out of stock"
        featured = "Yes" if int(p.get("is_featured") or 0) else "No"
        lines.extend([
            f"",
            f"Product {i}:",
            f"Name: {p.get('name') or ''}",
            f"Brand: {p.get('brand') or ''}",
            f"Category: {p.get('category') or ''}",
            f"Size: {p.get('size') or ''}",
            f"SKU: {p.get('sku') or ''}",
            f"Scent family: {p.get('scent_family') or ''}",
            f"Price: {price_text}",
            f"Stock status: {stock_text}",
            f"Featured / Best seller: {featured}",
            f"Notes: {p.get('notes') or ''}",
            f"Occasion: {p.get('occasion') or ''}",
            f"Description: {p.get('description') or ''}",
            f"Image URL: {image or 'No image'}",
        ])

    lines.extend([
        "",
        "Design instructions:",
        "Use the product photos as real product references. Do not invent different bottles. Keep product names and prices readable. Use elegant product cards, gold dividers, refined iconography and a high-end boutique finish. If there are many products, create multiple pages rather than overcrowding a single page.",
    ])
    return "\n".join(lines)


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
        "catalogue_mode": setting("catalogue_mode", "auto"),
        "catalogue_uploaded_url": setting("catalogue_uploaded_url", ""),
        "catalogue_download_url": catalogue_download_url,
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
            "catalogue_mode": "auto",
            "catalogue_uploaded_url": "",
            "catalogue_uploaded_at": "",
            "catalogue_uploaded_name": "",
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
                         is_featured, is_active, sort_order, views, whatsapp_clicks, created_at, updated_at)
                        VALUES (:name, :brand, :category, :size, :sku, :scent_family, :stock_qty, :low_stock_threshold,
                                :price, :description, :notes, :occasion, :image_filename,
                                :is_featured, :is_active, :sort_order, 0, 0, :created_at, :updated_at)
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
    """Download uploaded luxury catalogue when enabled, otherwise generate one from live CMS data."""
    if request.args.get("auto") != "1":
        uploaded_catalogue = setting("catalogue_uploaded_url", "")
        if setting("catalogue_mode", "auto") == "uploaded" and uploaded_catalogue:
            return redirect(catalogue_download_url(uploaded_catalogue))

    from xml.sax.saxutils import escape
    from reportlab.pdfgen import canvas
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader

    W, H = A4
    IVORY = colors.HexColor("#FBF4E8")
    PAPER = colors.HexColor("#FFF9F0")
    GOLD = colors.HexColor("#B9822F")
    GOLD_LIGHT = colors.HexColor("#E8D1AC")
    ESPRESSO = colors.HexColor("#22160F")
    TAUPE = colors.HexColor("#746353")
    MUTED = colors.HexColor("#8E7B67")
    GREEN = colors.HexColor("#267A48")
    RED = colors.HexColor("#A34837")

    def clean(value, default=""):
        if value is None:
            return default
        return str(value).strip()

    def money(value):
        try:
            amount = float(value or 0)
        except Exception:
            amount = 0
        return f"{currency_value} {amount:,.0f}".replace(",", " ")

    def safe_text(value, max_len=None):
        value = clean(value)
        if max_len and len(value) > max_len:
            return value[: max_len - 1].rstrip() + "…"
        return value

    def fetch_image_source(image_value):
        """Return BytesIO or file path for ReportLab ImageReader."""
        if not image_value:
            return None
        image_value = str(image_value).strip()
        try:
            if image_value.startswith("http://") or image_value.startswith("https://"):
                # Cloudinary transformation for faster PDF generation and smaller downloads.
                url = image_value
                if "res.cloudinary.com" in url and "/upload/" in url and "f_auto" not in url:
                    url = url.replace("/upload/", "/upload/f_auto,q_auto,w_1200/")
                req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
                return io.BytesIO(urlopen(req, timeout=12).read())
            path = os.path.join(app.config["UPLOAD_FOLDER"], image_value)
            if os.path.exists(path):
                return path
        except Exception:
            return None
        return None

    def draw_bg(c, page_title=None, page_no=None):
        c.setFillColor(IVORY)
        c.rect(0, 0, W, H, fill=1, stroke=0)
        # subtle outer frame
        c.setStrokeColor(GOLD)
        c.setLineWidth(0.8)
        margin = 8 * mm
        c.roundRect(margin, margin, W - 2 * margin, H - 2 * margin, 5 * mm, fill=0, stroke=1)
        c.setStrokeColor(GOLD_LIGHT)
        c.setLineWidth(0.35)
        c.roundRect(margin + 2 * mm, margin + 2 * mm, W - 2 * (margin + 2 * mm), H - 2 * (margin + 2 * mm), 4 * mm, fill=0, stroke=1)
        # corner flourishes
        c.setStrokeColor(GOLD)
        c.setLineWidth(0.7)
        for sx, sy in [(1,1), (-1,1), (1,-1), (-1,-1)]:
            x = W - margin if sx < 0 else margin
            y = H - margin if sy > 0 else margin
            c.line(x, y - sy * 8 * mm, x + sx * 8 * mm, y)
        if page_title:
            c.setFont("Helvetica-Bold", 8)
            c.setFillColor(GOLD)
            c.drawCentredString(W / 2, H - 15 * mm, page_title.upper())
        if page_no:
            c.setFont("Helvetica", 7)
            c.setFillColor(MUTED)
            c.drawRightString(W - 13 * mm, 10 * mm, f"Page {page_no}")
            c.drawString(13 * mm, 10 * mm, site)

    def draw_logo_or_mark(c, x, y, max_w=32*mm, max_h=14*mm):
        logo_src = fetch_image_source(logo_value)
        if logo_src:
            draw_image_contain(c, logo_src, x - max_w/2, y - max_h/2, max_w, max_h)
        else:
            c.setStrokeColor(GOLD)
            c.setLineWidth(1)
            c.roundRect(x - 7*mm, y - 7*mm, 14*mm, 14*mm, 5*mm, fill=0, stroke=1)
            c.setFont("Helvetica-Bold", 13)
            c.setFillColor(GOLD)
            c.drawCentredString(x, y - 2.5*mm, "✦")

    def draw_rule(c, y, width=45*mm):
        c.setStrokeColor(GOLD)
        c.setLineWidth(0.6)
        c.line(W/2 - width/2, y, W/2 - 4*mm, y)
        c.line(W/2 + 4*mm, y, W/2 + width/2, y)
        c.setFillColor(GOLD)
        c.setFont("Helvetica", 10)
        c.drawCentredString(W/2, y - 2.4*mm, "◆")

    def get_img_size(src):
        reader = ImageReader(src)
        return reader, reader.getSize()

    def draw_image_contain(c, src, x, y, w, h):
        try:
            reader, (iw, ih) = get_img_size(src)
            scale = min(w / iw, h / ih)
            dw, dh = iw * scale, ih * scale
            c.drawImage(reader, x + (w - dw) / 2, y + (h - dh) / 2, dw, dh, mask="auto")
            return True
        except Exception:
            return False

    def draw_image_cover(c, src, x, y, w, h):
        try:
            reader, (iw, ih) = get_img_size(src)
            scale = max(w / iw, h / ih)
            dw, dh = iw * scale, ih * scale
            c.saveState()
            p = c.beginPath()
            p.rect(x, y, w, h)
            c.clipPath(p, stroke=0, fill=0)
            c.drawImage(reader, x + (w - dw) / 2, y + (h - dh) / 2, dw, dh, mask="auto")
            c.restoreState()
            return True
        except Exception:
            return False

    def placeholder(c, x, y, w, h):
        c.setFillColor(colors.HexColor("#F4E8D8"))
        c.roundRect(x, y, w, h, 4*mm, fill=1, stroke=0)
        c.setFillColor(GOLD)
        c.setFont("Helvetica-Bold", 26)
        c.drawCentredString(x + w/2, y + h/2 - 6, "✦")

    def text_lines(text, font_name, font_size, max_width):
        words = clean(text).split()
        lines, current = [], ""
        for word in words:
            trial = (current + " " + word).strip()
            if c.stringWidth(trial, font_name, font_size) <= max_width:
                current = trial
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines

    def draw_wrapped(c, text, x, y, max_width, font="Helvetica", size=9, leading=12, color=TAUPE, max_lines=None):
        c.setFillColor(color)
        c.setFont(font, size)
        lines = text_lines(text, font, size, max_width)
        if max_lines and len(lines) > max_lines:
            lines = lines[:max_lines]
            if len(lines[-1]) > 2:
                lines[-1] = lines[-1].rstrip(".,; ") + "…"
        for line in lines:
            c.drawString(x, y, line)
            y -= leading
        return y

    def draw_badge(c, text, x, y, bg=colors.white, fg=GOLD):
        c.setFont("Helvetica-Bold", 7)
        tw = c.stringWidth(text, "Helvetica-Bold", 7)
        bw = tw + 8*mm
        c.setFillColor(bg)
        c.setStrokeColor(GOLD_LIGHT)
        c.roundRect(x, y, bw, 6.5*mm, 3*mm, fill=1, stroke=1)
        c.setFillColor(fg)
        c.drawCentredString(x + bw/2, y + 2*mm, text)
        return bw

    def stock_status(product):
        qty = int(product["stock_qty"] or 0)
        threshold = int(product["low_stock_threshold"] or 2)
        if qty <= 0:
            return "Out of stock", RED
        if qty <= threshold:
            return "Low stock", GOLD
        return "In stock", GREEN

    def product_image_value(product):
        return product["image_filename"] or ""

    products = get_db().execute(
        """
        SELECT * FROM products
        WHERE is_active = 1
        ORDER BY is_featured DESC, sort_order ASC, name ASC
        """
    ).fetchall()

    site = setting("site_name", "The Scent Library")
    tagline_value = setting("tagline", "Curated signature scents for every mood, occasion and style.")
    currency_value = setting("currency", "Rs")
    whatsapp_value = setting("whatsapp_number", "23050000000")
    instagram_value = setting("instagram_url", "https://instagram.com/scentlibrary.mu")
    address_value = setting("business_address", "Mauritius")
    logo_value = setting("logo_url", "")
    footer_note_value = setting("footer_note", "Catalogue generated from our live collection")

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    c.setTitle(f"{site} Fragrance Catalogue")
    c.setAuthor(site)

    # ---------------- Cover page ----------------
    draw_bg(c)
    draw_logo_or_mark(c, W/2, H - 30*mm)
    c.setFillColor(ESPRESSO)
    c.setFont("Helvetica", 15)
    c.drawCentredString(W/2, H - 47*mm, site.upper())
    draw_rule(c, H - 53*mm, 56*mm)
    c.setFont("Times-Bold", 32)
    c.drawCentredString(W/2, H - 73*mm, "Fragrance Catalogue 2026")
    c.setFont("Times-Italic", 12)
    c.setFillColor(TAUPE)
    c.drawCentredString(W/2, H - 86*mm, safe_text(tagline_value, 95) or "Curated signature scents for every mood, occasion and style.")
    c.setFont("Helvetica-Bold", 12)
    c.setFillColor(GOLD)
    c.drawCentredString(W/2, H - 108*mm, "MOST WANTED NOW")
    c.setStrokeColor(GOLD_LIGHT)
    c.line(27*mm, H - 106*mm, 80*mm, H - 106*mm)
    c.line(W - 80*mm, H - 106*mm, W - 27*mm, H - 106*mm)

    featured = list(products[:4])
    card_w = 42 * mm
    gap = 5 * mm
    total = len(featured) * card_w + max(0, len(featured) - 1) * gap
    start_x = (W - total) / 2 if featured else 20 * mm
    top_y = H - 213 * mm
    if not featured:
        c.setFont("Helvetica", 11)
        c.setFillColor(TAUPE)
        c.drawCentredString(W/2, H/2, "Add active products in Admin to generate your catalogue.")
    for i, p in enumerate(featured):
        x = start_x + i * (card_w + gap)
        y = top_y
        c.setFillColor(PAPER)
        c.setStrokeColor(GOLD_LIGHT)
        c.roundRect(x, y, card_w, 86*mm, 4*mm, fill=1, stroke=1)
        img_box_y = y + 35*mm
        img_src = fetch_image_source(product_image_value(p))
        if img_src:
            # cover on cover page for polished alignment
            draw_image_cover(c, img_src, x+1.5*mm, img_box_y, card_w-3*mm, 48*mm)
        else:
            placeholder(c, x+1.5*mm, img_box_y, card_w-3*mm, 48*mm)
        c.setFillColor(GOLD)
        c.setFont("Helvetica", 8)
        c.drawCentredString(x + card_w/2, y + 30*mm, "◆")
        c.setFont("Times-Bold", 11)
        c.setFillColor(ESPRESSO)
        name_lines = text_lines(safe_text(p["name"], 38), "Times-Bold", 11, card_w - 8*mm)[:2]
        ty = y + 24*mm
        for line in name_lines:
            c.drawCentredString(x + card_w/2, ty, line)
            ty -= 5*mm
        c.setFont("Times-Italic", 8.5)
        c.setFillColor(GOLD)
        c.drawCentredString(x + card_w/2, y + 11*mm, safe_text(p["brand"] or "The Scent Library", 24))
        c.setFont("Helvetica", 7)
        c.setFillColor(TAUPE)
        descriptor = safe_text(p["scent_family"] or p["notes"] or p["category"], 32)
        c.drawCentredString(x + card_w/2, y + 5.5*mm, descriptor)

    # footer strip
    c.setFillColor(PAPER)
    c.setStrokeColor(GOLD_LIGHT)
    c.roundRect(18*mm, 21*mm, W-36*mm, 18*mm, 7*mm, fill=1, stroke=1)
    c.setFillColor(ESPRESSO)
    c.setFont("Helvetica", 8.5)
    c.drawString(28*mm, 29*mm, "WhatsApp orders available")
    c.drawCentredString(W/2, 29*mm, f"Instagram: {instagram_value.replace('https://instagram.com/', '@')}")
    c.drawRightString(W-28*mm, 29*mm, "Generated from live collection")
    c.showPage()

    # ---------------- Product pages ----------------
    page_no = 2
    per_page = 4
    if products:
        for offset in range(0, len(products), per_page):
            page_products = products[offset: offset + per_page]
            draw_bg(c, "The Scent Library Catalogue", page_no)
            c.setFont("Times-Bold", 27)
            c.setFillColor(ESPRESSO)
            title = "Most Wanted Now" if offset == 0 else "Fragrance Selection"
            c.drawCentredString(W/2, H - 31*mm, title)
            draw_rule(c, H - 39*mm, 54*mm)

            positions = [
                (18*mm, H - 142*mm),
                (108*mm, H - 142*mm),
                (18*mm, H - 258*mm),
                (108*mm, H - 258*mm),
            ]
            card_w = 84*mm
            card_h = 104*mm
            for p, (x, y) in zip(page_products, positions):
                c.setFillColor(PAPER)
                c.setStrokeColor(GOLD_LIGHT)
                c.roundRect(x, y, card_w, card_h, 4*mm, fill=1, stroke=1)
                img_h = 45*mm
                img_src = fetch_image_source(product_image_value(p))
                c.setFillColor(colors.HexColor("#F8EFE2"))
                c.roundRect(x+3*mm, y+card_h-img_h-3*mm, card_w-6*mm, img_h, 3*mm, fill=1, stroke=0)
                if img_src:
                    draw_image_contain(c, img_src, x+5*mm, y+card_h-img_h-1*mm, card_w-10*mm, img_h-5*mm)
                else:
                    placeholder(c, x+5*mm, y+card_h-img_h-1*mm, card_w-10*mm, img_h-5*mm)
                if int(p["is_featured"] or 0) == 1:
                    draw_badge(c, "Best Seller", x+5*mm, y+card_h-10*mm)
                stock_text, stock_color = stock_status(p)
                c.setFillColor(stock_color)
                c.setFont("Helvetica-Bold", 7)
                c.drawRightString(x+card_w-6*mm, y+card_h-7*mm, stock_text)

                tx = x + 6*mm
                ty = y + card_h - img_h - 9*mm
                c.setFont("Helvetica-Bold", 7.5)
                c.setFillColor(GOLD)
                c.drawString(tx, ty, safe_text(p["category"], 16).upper())
                c.drawRightString(x+card_w-6*mm, ty, safe_text(p["size"], 10).upper())
                ty -= 8*mm
                c.setFillColor(ESPRESSO)
                c.setFont("Times-Bold", 14)
                for line in text_lines(safe_text(p["name"], 50), "Times-Bold", 14, card_w-12*mm)[:2]:
                    c.drawString(tx, ty, line)
                    ty -= 6*mm
                c.setFillColor(ESPRESSO)
                c.setFont("Helvetica-Bold", 8.5)
                c.drawString(tx, ty, safe_text(p["brand"] or "The Scent Library", 36))
                ty -= 8*mm
                if p["scent_family"]:
                    wbadge = draw_badge(c, safe_text(p["scent_family"], 24), tx, ty-1*mm, bg=colors.HexColor("#F3E5CF"), fg=colors.HexColor("#8A5A24"))
                    ty -= 9*mm
                if p["notes"]:
                    ty = draw_wrapped(c, "Notes: " + safe_text(p["notes"], 80), tx, ty, card_w-12*mm, "Helvetica", 7.4, 9, TAUPE, 2)
                if p["description"]:
                    ty = draw_wrapped(c, safe_text(p["description"], 115), tx, ty-1*mm, card_w-12*mm, "Helvetica", 7.4, 9, TAUPE, 3)
                c.setFont("Helvetica-Bold", 13)
                c.setFillColor(ESPRESSO)
                c.drawString(tx, y + 9*mm, money(p["price"]))
                if p["sku"]:
                    c.setFont("Helvetica", 6.7)
                    c.setFillColor(MUTED)
                    c.drawRightString(x + card_w - 6*mm, y + 9.5*mm, "SKU: " + safe_text(p["sku"], 18))
            c.showPage()
            page_no += 1

    # ---------------- Closing page ----------------
    draw_bg(c, "Orders & Enquiries", page_no)
    draw_logo_or_mark(c, W/2, H - 29*mm)
    c.setFillColor(ESPRESSO)
    c.setFont("Helvetica", 13)
    c.drawCentredString(W/2, H - 45*mm, site.upper())
    c.setFont("Times-Bold", 34)
    c.drawCentredString(W/2, H - 66*mm, "Order & Enquiries")
    draw_rule(c, H - 77*mm, 55*mm)
    c.setFont("Helvetica", 11)
    c.setFillColor(TAUPE)
    c.drawCentredString(W/2, H - 91*mm, "Contact us for availability, recommendations and gifting ideas.")

    c.setFont("Times-Bold", 20)
    c.setFillColor(GOLD)
    c.drawString(22*mm, H - 125*mm, "How to order")
    c.setStrokeColor(GOLD_LIGHT)
    c.line(22*mm, H - 130*mm, 86*mm, H - 130*mm)
    c.setFont("Helvetica", 10)
    c.setFillColor(ESPRESSO)
    y = H - 143*mm
    for item in ["Browse your favourites", "Message us on WhatsApp", "Confirm availability and collection or delivery"]:
        c.setFillColor(GOLD)
        c.circle(25*mm, y+1.2*mm, 1.1*mm, fill=1, stroke=0)
        c.setFillColor(ESPRESSO)
        c.drawString(30*mm, y, item)
        y -= 9*mm

    c.setFont("Times-Bold", 20)
    c.setFillColor(GOLD)
    c.drawString(22*mm, H - 180*mm, "Why shop with us")
    c.line(22*mm, H - 185*mm, 93*mm, H - 185*mm)
    c.setFont("Helvetica", 10)
    y = H - 198*mm
    for item in ["Curated inspired fragrances", "Carefully selected best sellers", "Personal recommendations", "Gift-friendly picks"]:
        c.setFillColor(GOLD)
        c.circle(25*mm, y+1.2*mm, 1.1*mm, fill=1, stroke=0)
        c.setFillColor(ESPRESSO)
        c.drawString(30*mm, y, item)
        y -= 9*mm

    # product collage on closing page
    collage = list(products[:4])
    cx, cy = 106*mm, H - 205*mm
    sizes = [(24*mm, 52*mm), (24*mm, 52*mm), (31*mm, 45*mm), (28*mm, 45*mm)]
    xs = [cx, cx+25*mm, cx+50*mm, cx+83*mm]
    for p, x, (iw, ih) in zip(collage, xs, sizes):
        src = fetch_image_source(product_image_value(p))
        if src:
            draw_image_contain(c, src, x, cy, iw, ih)

    # contact box
    box_x, box_y, box_w, box_h = 42*mm, 38*mm, W-84*mm, 46*mm
    c.setFillColor(PAPER)
    c.setStrokeColor(GOLD)
    c.roundRect(box_x, box_y, box_w, box_h, 5*mm, fill=1, stroke=1)
    c.setFont("Times-Bold", 18)
    c.setFillColor(GOLD)
    c.drawCentredString(W/2, box_y + box_h - 13*mm, "Connect with us")
    c.setFont("Helvetica", 10)
    c.setFillColor(ESPRESSO)
    c.drawString(box_x + 18*mm, box_y + 24*mm, f"WhatsApp: {whatsapp_value}")
    c.drawString(box_x + 18*mm, box_y + 15*mm, f"Instagram: {instagram_value.replace('https://instagram.com/', '@')}")
    c.drawString(box_x + 18*mm, box_y + 6*mm, address_value)
    c.setFont("Times-Italic", 12)
    c.setFillColor(TAUPE)
    c.drawCentredString(W/2, 25*mm, f"{site} — curated scents for every mood, moment and occasion.")
    c.setFont("Helvetica", 7)
    c.drawCentredString(W/2, 16*mm, "Prices and availability are subject to change.")
    c.save()

    pdf_data = buffer.getvalue()
    buffer.close()
    filename = f"{slugify(site)}-luxury-catalogue.pdf"
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




@app.route("/admin/catalogue", methods=["GET", "POST"])
@login_required
def admin_catalogue():
    db = get_db()

    def all_settings():
        return {row["key"]: row["value"] for row in db.execute("SELECT * FROM settings").fetchall()}

    if request.method == "POST":
        action = request.form.get("action", "save")
        try:
            if action == "upload":
                mode = request.form.get("catalogue_mode", "uploaded")
                catalogue_file = request.files.get("catalogue_file")
                if catalogue_file and catalogue_file.filename:
                    uploaded_url = upload_catalogue_file(catalogue_file)
                    save_setting_value(db, "catalogue_uploaded_url", uploaded_url or "")
                    save_setting_value(db, "catalogue_uploaded_name", secure_filename(catalogue_file.filename))
                    save_setting_value(db, "catalogue_uploaded_at", now_iso())
                    flash("Luxury catalogue uploaded. Customer download can now use this file.", "success")
                save_setting_value(db, "catalogue_mode", "uploaded" if mode == "uploaded" else "auto")
            elif action == "remove":
                save_setting_value(db, "catalogue_uploaded_url", "")
                save_setting_value(db, "catalogue_uploaded_name", "")
                save_setting_value(db, "catalogue_uploaded_at", "")
                save_setting_value(db, "catalogue_mode", "auto")
                flash("Uploaded catalogue removed. Downloads will use the automatic PDF again.", "success")
            else:
                save_setting_value(db, "catalogue_mode", request.form.get("catalogue_mode", "auto"))
                flash("Catalogue settings saved.", "success")
            db.commit()
        except ValueError as exc:
            db.rollback()
            flash(str(exc), "error")
        except Exception as exc:
            db.rollback()
            app.logger.exception("Catalogue manager error")
            flash(f"Catalogue action failed: {exc}", "error")
        return redirect(url_for("admin_catalogue"))

    products = db.execute("SELECT * FROM products WHERE is_active = 1 ORDER BY is_featured DESC, sort_order ASC, name ASC").fetchall()
    rows = []
    for row in products:
        d = dict(row)
        img = d.get("image_filename") or ""
        if img:
            if img.startswith("http://") or img.startswith("https://"):
                d["image_url"] = img
            else:
                d["image_url"] = request.host_url.rstrip("/") + url_for("static", filename="uploads/" + img)
        else:
            d["image_url"] = ""
        rows.append(d)
    settings = all_settings()
    prompt = build_catalogue_ai_prompt(rows, settings)
    return render_template("admin/catalogue.html", settings=settings, products=rows, prompt=prompt)


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
