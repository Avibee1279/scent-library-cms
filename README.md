# The Scent Library CMS - Business Upgrade

This is the upgraded CMS for The Scent Library.

## Stack

- Flask / Python backend
- PostgreSQL using `DATABASE_URL` on Render/Neon
- SQLite fallback for local testing
- Cloudinary product photo uploads
- Render deployment ready

## New in this version

- Stock quantity per product
- Low-stock alerts on admin dashboard
- Out-of-stock display on public website
- SKU/code per product
- Scent family filter on public catalogue
- Better admin dashboard with latest requests
- Request status filters: New, Contacted, Completed, Cancelled
- Product CSV export includes stock/SKU/family
- Sitemap and robots.txt routes for SEO

## Render environment variables

Keep these in Render, not GitHub:

```text
DATABASE_URL=your Neon PostgreSQL connection string
CLOUDINARY_CLOUD_NAME=your Cloudinary cloud name
CLOUDINARY_API_KEY=your Cloudinary API key
CLOUDINARY_API_SECRET=your Cloudinary API secret
SECRET_KEY=any long random string
```

## Deploy

Build command:

```bash
pip install -r requirements.txt
```

Start command:

```bash
gunicorn app:app
```

## Admin

Open:

```text
/admin
```

Default login if your database is new:

```text
admin / admin123
```

Change the password after login.
