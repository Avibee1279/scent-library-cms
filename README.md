# Scent Library CMS - Full Image Default Fix

This version keeps the latest logo upload/luxury storefront features and fixes the product card image behavior.

## Change
- Product card photos show the full bottle/photo by default.
- Removed the confusing "Full view" hover label.
- Removed popup/overlay behavior.
- Hover no longer changes the crop.
- CSS cache version updated.

Deploy by replacing your GitHub files, committing, and redeploying on Render.

# The Scent Library CMS - Luxury Storefront Upgrade

This version keeps the existing backend setup:

- Flask app on Render
- Neon PostgreSQL database through `DATABASE_URL`
- Cloudinary product and banner images
- Admin panel for products, banners, stock, galleries, requests and settings

## New in this upgrade

- Luxury boutique-style public homepage
- Cream/white premium storefront design
- Collection tiles: Fresh, Oud, Sweet Amber and Gift Ideas
- Best sellers section
- Editorial banner layout using your existing admin banner photos
- Cleaner product cards
- Wishlist button using browser local storage
- Request basket / mini cart using browser local storage
- Request basket sends to the existing admin customer request system
- Product detail page redesign
- Better mobile layout
- Existing gallery arrows and thumbnail switching retained

## How to deploy

1. Extract this zip.
2. Replace the files in your GitHub repository with these files.
3. Commit the changes.
4. Go to Render.
5. Click **Manual Deploy** → **Deploy latest commit**.

Keep these Render environment variables:

```text
PYTHON_VERSION=3.11.9
DATABASE_URL=your Neon PostgreSQL connection string
CLOUDINARY_CLOUD_NAME=your Cloudinary cloud name
CLOUDINARY_API_KEY=your Cloudinary API key
CLOUDINARY_API_SECRET=your Cloudinary API secret
SECRET_KEY=your secret key
```

## Request basket note

The request basket is not an online payment cart. It lets customers collect perfumes, then submit a request to your admin panel or send the basket to WhatsApp.


## Logo upload added

This version is based on the luxury image-fit storefront and adds logo management.

Admin path:

`/admin/settings`

New options:

- Upload logo image
- Remove current logo
- Show/hide the site name next to the logo

Recommended logo format: transparent PNG, around 400 x 120 px. The logo is uploaded to Cloudinary when Cloudinary environment variables are configured.


## Update: Full photo hover preview
Product cards now keep the boutique crop in normal view, but on desktop hover a large overlay preview shows the full original photo.


## Soft hover preview update
- Product image hover preview is smaller, centered, semi-transparent, and fades in smoothly.
- Normal product card crop is unchanged.
- Cache-buster updated to `soft-preview-2`.


## No-popup hover update
Product image hover now stays inside the product card. No large overlay/popup is opened.

## Downloadable PDF catalogue added

Customers can now download a live PDF catalogue from:

`/catalogue.pdf`

The PDF is generated from the active products in the database, so it uses the latest:

- Product names
- Prices
- Descriptions
- Notes
- Occasion
- Stock status
- Main product photo
- Site logo, if uploaded in Admin Settings

Public links were added to the header, hero section, catalogue section and footer/newsletter area.

New dependency added:

```text
reportlab==4.2.2
Pillow==10.4.0
```

After replacing files in GitHub, redeploy on Render. The build command remains:

```text
pip install -r requirements.txt
```

Start command remains:

```text
gunicorn app:app
```
