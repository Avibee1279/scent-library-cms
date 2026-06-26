import csv
import io
import os
import re
from datetime import datetime
from functools import wraps
from urllib.parse import quote_plus

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
        }
        for key, value in defaults.items():
            exists = conn.execute(text("SELECT 1 FROM settings WHERE key = :key"), {"key": key}).fetchone()
            if not exists:
                conn.execute(text("INSERT INTO settings (key, value) VALUES (:key, :value)"), {"key": key, "value": value})

        product_count = conn.execute(text("SELECT COUNT(*) AS c FROM products")).mappings().fetchone()["c"]
        if product_count == 0:
            samples = [
                ("Amber Oud Carbon Edition", "Al Haramain", "Men", "60ml", 1500, "Fresh, aromatic and modern. Good for office and evening wear.", "bergamot, lavender, woods", "Office / Night", None, 1, 1, 1),
                ("Khamrah Waha", "Lattafa", "Unisex", "100ml", 1800, "Sweet, warm and addictive. Perfect for colder evenings and special occasions.", "dates, vanilla, amber", "Evening", None, 1, 1, 2),
                ("Pacific Chill Inspiration", "The Scent Library", "Unisex", "50ml", 900, "Fresh citrus scent with a clean summer feeling.", "citrus, mint, musk", "Summer / Day", None, 1, 1, 3),
                ("Ombre Nomade Inspiration", "The Scent Library", "Unisex", "50ml", 950, "Deep oud style fragrance for strong projection.", "oud, rose, incense", "Night / Special Occasion", None, 0, 1, 4),
                ("Invictus Parfum Inspiration", "The Scent Library", "Men", "50ml", 850, "Sporty, clean and powerful masculine scent.", "marine notes, woods, amber", "Daily", None, 0, 1, 5),
                ("Kayali Inspired Mini Set", "The Scent Library", "Women", "5 x 10ml", 1200, "A discovery set for layering and gifting.", "vanilla, musk, fruits", "Gift / Layering", None, 1, 1, 6),
            ]
            for p in samples:
                conn.execute(
                    text("""
                        INSERT INTO products
                        (name, brand, category, size, price, description, notes, occasion, image_filename,
                         is_featured, is_active, sort_order, created_at, updated_at)
                        VALUES (:name, :brand, :category, :size, :price, :description, :notes, :occasion, :image_filename,
                                :is_featured, :is_active, :sort_order, :created_at, :updated_at)
                    """),
                    {
                        "name": p[0], "brand": p[1], "category": p[2], "size": p[3], "price": p[4],
                        "description": p[5], "notes": p[6], "occasion": p[7], "image_filename": p[8],
                        "is_featured": p[9], "is_active": p[10], "sort_order": p[11],
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
    categories = ["All", "Men", "Women", "Unisex"]
    families = ["All"] + [row["scent_family"] for row in get_db().execute("SELECT DISTINCT scent_family FROM products WHERE is_active = 1 AND COALESCE(scent_family,'') <> '' ORDER BY scent_family").fetchall()]
    return render_template(
        "index.html",
        products=products,
        featured=featured,
        combos=combos,
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
    product = get_db().execute("SELECT * FROM products WHERE id = ? AND is_active = 1", (product_id,)).fetchone()
    if not product:
        abort(404)
    related = get_db().execute(
        "SELECT * FROM products WHERE is_active = 1 AND category = ? AND id <> ? ORDER BY is_featured DESC, sort_order ASC LIMIT 3",
        (product["category"], product_id)
    ).fetchall()
    return render_template("product_detail.html", product=product, related=related)


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
    }
    recent_orders = get_db().execute("SELECT * FROM order_requests ORDER BY created_at DESC LIMIT 5").fetchall()
    low_stock = get_db().execute("SELECT * FROM products WHERE is_active = 1 AND stock_qty <= low_stock_threshold ORDER BY stock_qty ASC, name ASC LIMIT 6").fetchall()
    return render_template("admin/dashboard.html", products=products, stats=stats, recent_orders=recent_orders, low_stock=low_stock)


@app.route("/admin/products/new", methods=["GET", "POST"])
@login_required
def admin_product_new():
    if request.method == "POST":
        return save_product()
    return render_template("admin/product_form.html", product=None, action="Add product")


@app.route("/admin/products/<int:product_id>/edit", methods=["GET", "POST"])
@login_required
def admin_product_edit(product_id):
    product = get_db().execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not product:
        flash("Product not found.", "error")
        return redirect(url_for("admin_dashboard"))
    if request.method == "POST":
        return save_product(product_id)
    return render_template("admin/product_form.html", product=product, action="Edit product")


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
        flash("Product added.", "success")
    db.commit()
    return redirect(url_for("admin_dashboard"))


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
        "currency", "business_email", "business_address", "delivery_text", "homepage_notice"
    ]
    if request.method == "POST":
        db = get_db()
        for key in keys:
            value = request.form.get(key, "").strip()
            exists = db.execute("SELECT 1 FROM settings WHERE key = ?", (key,)).fetchone()
            if exists:
                db.execute("UPDATE settings SET value = ? WHERE key = ?", (value, key))
            else:
                db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))
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
