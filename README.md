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
