# The Scent Library - Custom CMS

A complete starter CMS for a perfume catalogue website.

## What is included

- Public mobile-friendly perfume website
- Smooth scrolling / fade-in animations
- Product catalogue with search and category filters
- Product detail pages
- WhatsApp order buttons for each perfume
- Quick request form that saves customer enquiries
- Admin login
- Add / edit / delete products
- Upload perfume photos
- Mark products as featured or hidden
- Set product order
- Manage layering combos
- View and update customer requests
- Website settings page
- Change admin password
- Export products as CSV
- SQLite database
- Ready for Render/Railway-style Python hosting

## Default admin login

Admin URL:

```text
/admin
```

Default login:

```text
Username: admin
Password: admin123
```

Change this password before putting the website online.

## Run locally on Windows

Open Command Prompt in this folder and run:

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Then open:

```text
http://127.0.0.1:5000
```

Admin panel:

```text
http://127.0.0.1:5000/admin
```

## Run locally on Mac/Linux

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
```

## How the business owner uses it

1. Go to `/admin`
2. Login
3. Go to **Products**
4. Click **Add product**
5. Enter perfume name, price, size, notes and upload photo
6. Save
7. The product appears on the website automatically

## Important before going live

Do these before using it for a real client:

1. Change the admin password.
2. Set a strong `SECRET_KEY` environment variable on the hosting platform.
3. Use a persistent disk or managed database if hosting online.
4. Make regular backups of `scent_library.db` and `static/uploads`.
5. Replace the default WhatsApp number in **Admin → Settings**.

## Deployment idea

This is not a static HTML site. It needs Python/Flask hosting.

Good hosting choices:

- Render
- Railway
- PythonAnywhere
- A VPS

For Render/Railway-style hosting, use:

```text
Build command: pip install -r requirements.txt
Start command: gunicorn app:app
```

For local testing, use:

```bash
python app.py
```
