# HANALL AI deployment

The existing Vercel site remains the customer-facing address.

The PDF extractor runs as a Python service from the `backend/` directory. Connect this repository to Render using the root `render.yaml`. After the Render service is live, route the Vercel application to the Render service or link `/admin/catalogs` from the customer UI.

Render service name: `hanall-ai-catalog-bhj234926`

Health check: `/health`

Catalog admin: `/admin/catalogs`
