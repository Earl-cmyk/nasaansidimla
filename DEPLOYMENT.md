# Deployment

## Shared environment variable

Create `DATABASE_URL` on both hosts. Use the Supabase connection string in the format:

`postgresql://USER:PASSWORD@HOST:PORT/postgres`

Use the session pooler connection string if the direct database host is not reachable from the host. Do not commit `.env` or put the password in `render.yaml`, `vercel.json`, or source files.

## Render

1. Push this repository to GitHub.
2. In Render, choose **New > Blueprint** and select the repository, or create a **Web Service** from it.
3. If using the Blueprint, approve the `DATABASE_URL` secret prompt. If creating the service manually, add `DATABASE_URL` under **Environment**.
4. Use the root directory, `Python 3`, and the committed `render.yaml` settings.
5. Deploy. Render runs `pip install -r requirements.txt` and starts Gunicorn on Render's assigned port.

## Vercel

1. In Vercel, choose **Add New > Project** and import the same GitHub repository.
2. Leave the root directory as `.` and let Vercel detect the Python function in `api/index.py`.
3. Add `DATABASE_URL` under **Settings > Environment Variables** for Production (and Preview if needed).
4. Deploy, then redeploy after changing environment variables.

The two deployments are separate frontends/backends serving the same Flask application and database. Run the schema once against the selected Postgres database if it is empty; application startup also attempts schema initialization.
