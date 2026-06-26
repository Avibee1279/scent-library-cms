# The Scent Library CMS - Growth Upgrade

This version is the next business upgrade of the perfume CMS.

## Stack

- Flask / Python
- PostgreSQL through `DATABASE_URL` on Render/Neon
- SQLite fallback for local testing
- Cloudinary for permanent product and banner images
- Render for hosting

## New features in this upgrade

- Product image gallery: add several photos per perfume
- Homepage banner manager: promote new arrivals, offers and gift sets from admin
- CSV product import with downloadable template
- Full JSON backup export
- Product view tracking
- WhatsApp click tracking per product
- Dashboard now shows views and WhatsApp clicks
- Google Analytics ID field in settings
- Footer note setting
- Existing stock, low stock, requests, Cloudinary and PostgreSQL features remain

## Render settings

Build command:

```bash
pip install -r requirements.txt
```

Start command:

```bash
gunicorn app:app
```

Environment variables:

```text
PYTHON_VERSION=3.11.9
SECRET_KEY=your-long-secret-key
DATABASE_URL=your-neon-postgres-connection-string
CLOUDINARY_CLOUD_NAME=your-cloud-name
CLOUDINARY_API_KEY=your-api-key
CLOUDINARY_API_SECRET=your-api-secret
```

## After uploading to GitHub

1. Commit the new files.
2. In Render, run **Manual Deploy -> Clear build cache & deploy**.
3. Login to `/admin`.
4. Check the new admin menu items:
   - Banners
   - Import CSV
   - Backup JSON
5. Edit old products if needed to add stock, gallery images and SKU.

## Default local login

```text
Username: admin
Password: admin123
```

Change the password before using the site seriously.
