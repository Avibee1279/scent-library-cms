# The Scent Library CMS - Catalogue Manager Upgrade

This version adds a hybrid catalogue workflow:

- `/catalogue.pdf` can still generate an automatic PDF from live CMS products.
- Admin can generate an AI prompt from live database products.
- Admin can upload a final luxury catalogue PDF/image.
- Customer downloads can use either:
  - automatic generated PDF, or
  - uploaded luxury catalogue file.

## New admin page

Go to:

```text
/admin/catalogue
```

You can:

1. Copy an AI catalogue prompt generated from current products.
2. Upload a final catalogue PDF/image.
3. Choose customer download mode: automatic PDF or uploaded catalogue.

## Deployment

Keep these Render environment variables:

```text
PYTHON_VERSION = 3.11.9
DATABASE_URL = your Neon connection string
CLOUDINARY_CLOUD_NAME = your Cloudinary cloud name
CLOUDINARY_API_KEY = your Cloudinary API key
CLOUDINARY_API_SECRET = your Cloudinary API secret
SECRET_KEY = your secret key
```

Deploy as usual:

```text
Build command: pip install -r requirements.txt
Start command: gunicorn app:app
```

## Customer download

The customer download link remains:

```text
/catalogue.pdf
```

If uploaded catalogue mode is selected, it redirects/downloads the uploaded catalogue.
If automatic mode is selected, it generates the PDF from current database products.

To force the automatic PDF preview even when uploaded mode is active:

```text
/catalogue.pdf?auto=1
```
