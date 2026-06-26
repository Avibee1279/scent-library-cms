import csv
import io
import os
import re
import sqlite3
from datetime import datetime
from functools import wraps
from urllib.parse import quote_plus

from flask import (
    Flask, Response, abort, flash, g, redirect, render_template,
    request, send_from_directory, session, url_for
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

# Cloudinary is used for permanent product photo storage on hosted servers.
# If Cloudinary environment variables are missing, the app falls back to local uploads.
try:
    import cloudinary
    import cloudinary.uploader
except ImportError:  # Local fallback if package is not installed yet
    cloudinary = None


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.environ.get("DATABASE_PATH", os.path.join(BASE_DIR, "scent_library.db"))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "CHANGE-ME-before-going-live")
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # 8MB image upload limit

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


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
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


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    cur = db.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            brand TEXT,
            category TEXT NOT NULL DEFAULT 'Unisex',
            size TEXT,
            price REAL NOT NULL DEFAULT 0,
            description TEXT,
            notes TEXT,
            occasion TEXT,
            image_filename TEXT,
            is_featured INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS combos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            product_1 TEXT,
            product_2 TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS order_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            location TEXT,
            product_id INTEGER,
            product_name TEXT,
            message TEXT,
            status TEXT NOT NULL DEFAULT 'New',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(product_id) REFERENCES products(id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    admin_count = cur.execute("SELECT COUNT(*) AS c FROM admins").fetchone()["c"]
    if admin_count == 0:
        cur.execute(
            "INSERT INTO admins (username, password_hash, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("admin", generate_password_hash("admin123"), now_iso(), now_iso())
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
        cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))

    product_count = cur.execute("SELECT COUNT(*) AS c FROM products").fetchone()["c"]
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
            cur.execute(
                """
                INSERT INTO products
                (name, brand, category, size, price, description, notes, occasion, image_filename, is_featured, is_active, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (*p, now_iso(), now_iso())
            )

    combo_count = cur.execute("SELECT COUNT(*) AS c FROM combos").fetchone()["c"]
    if combo_count == 0:
        combos = [
            ("Fresh Office", "Clean, professional and not too loud.", "Amber Oud Carbon", "Clean musk", 1),
            ("Sweet Night", "Warm, sweet and attractive for evening wear.", "Khamrah Waha", "Vanilla scent", 2),
            ("Oud Statement", "Strong projection for special occasions.", "Ombre style", "Rose / incense", 3),
        ]
        for title, desc, p1, p2, order in combos:
            cur.execute(
                """
                INSERT INTO combos (title, description, product_1, product_2, is_active, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (title, desc, p1, p2, order, now_iso(), now_iso())
            )

    db.commit()
    db.close()


def product_query(active_only=True):
    q = request.args.get("q", "").strip()
    category = request.args.get("category", "All")
    sql = "SELECT * FROM products WHERE 1=1"
    params = []
    if active_only:
        sql += " AND is_active = 1"
    if q:
        sql += " AND (name LIKE ? OR brand LIKE ? OR description LIKE ? OR notes LIKE ? OR occasion LIKE ?)"
        like = f"%{q}%"
        params.extend([like, like, like, like, like])
    if category and category != "All":
        sql += " AND category = ?"
        params.append(category)
    sql += " ORDER BY is_featured DESC, sort_order ASC, name ASC"
    return get_db().execute(sql, params).fetchall(), q, category


@app.route("/")
def index():
    products, q, selected_category = product_query(active_only=True)
    featured = get_db().execute(
        "SELECT * FROM products WHERE is_active = 1 AND is_featured = 1 ORDER BY sort_order ASC, name ASC LIMIT 6"
    ).fetchall()
    combos = get_db().execute(
        "SELECT * FROM combos WHERE is_active = 1 ORDER BY sort_order ASC, title ASC"
    ).fetchall()
    categories = ["All", "Men", "Women", "Unisex"]
    return render_template(
        "index.html",
        products=products,
        featured=featured,
        combos=combos,
        categories=categories,
        selected_category=selected_category,
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
    }
    return render_template("admin/dashboard.html", products=products, stats=stats)


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

    data = {
        "name": name,
        "brand": request.form.get("brand", "").strip(),
        "category": request.form.get("category", "Unisex"),
        "size": request.form.get("size", "").strip(),
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
            name=:name, brand=:brand, category=:category, size=:size, price=:price,
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
            (name, brand, category, size, price, description, notes, occasion, image_filename, is_featured, is_active, sort_order, created_at, updated_at)
            VALUES (:name, :brand, :category, :size, :price, :description, :notes, :occasion, :image_filename, :is_featured, :is_active, :sort_order, :created_at, :updated_at)
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
    writer.writerow(["Name", "Brand", "Category", "Size", "Price", "Notes", "Occasion", "Featured", "Active"])
    for p in products:
        writer.writerow([p["name"], p["brand"], p["category"], p["size"], p["price"], p["notes"], p["occasion"], p["is_featured"], p["is_active"]])
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
    orders = get_db().execute("SELECT * FROM order_requests ORDER BY created_at DESC").fetchall()
    return render_template("admin/orders.html", orders=orders)


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
            db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, request.form.get(key, "").strip()))
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
