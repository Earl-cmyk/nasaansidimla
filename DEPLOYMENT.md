# Deployment

## Render

Use the committed `render.yaml` Blueprint. It uses the root `Dockerfile`, which installs ffmpeg inside the image. Do not add an `apt-get` command to a native Python build command.

Required Render environment variables:

- `DATABASE_URL`: your Postgres/Supabase connection string
- `ALPHA_VANTAGE_API_KEY`: your Alpha Vantage API key

After adding or changing environment variables, trigger a redeploy. Render's free plan may time out or sleep during long YouTube conversions; the service limits conversions to 15 minutes and 250 MB.

## Vercel

Vercel uses `api/index.py` and `vercel.json`; it does not use the Dockerfile. Add these Production environment variables in the Vercel project settings:

- `DATABASE_URL`
- `ALPHA_VANTAGE_API_KEY`

Redeploy after saving them. The Flask APIs and market quotes work on Vercel, but ffmpeg/yt-dlp conversion should be used through Render because Vercel's serverless runtime does not guarantee ffmpeg or long-running temporary conversion jobs.

## Git safety

Never commit `.env`, API keys, database passwords, generated media, or virtual environments. The repository includes `.gitignore` and `.dockerignore` rules for these files.
