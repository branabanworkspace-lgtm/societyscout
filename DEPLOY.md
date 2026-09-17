# Putting SocietyScout on GitHub and Streamlit

Everything already built still works online. Two things change, and both are handled in the code: the hosted app can't keep your data, and searching by university name becomes unreliable from a shared server.

## Read this first

**A hosted copy forgets everything when it restarts.** Streamlit Community Cloud gives every app a temporary disk. It's wiped when the app sleeps (after 12 quiet hours), when you push a code change, and whenever Streamlit restarts it. Your master CSV goes with it.

The app now handles this: online it shows a banner, lets you upload a CSV you downloaded earlier to carry on, and reminds you to download before you finish. But treat the hosted version as a shared window onto the tool, not as where your data lives.

**If the master list matters, keep running the batch jobs on a laptop** and use the hosted app for looking things up and adding entries on the go.

**Make the app private.** A public app means anyone with the link can run scrapes in your company's name, using the contact email in your settings. The free plan allows one private app, which is all you need.

## Step 1: put the code on GitHub

Use GitHub Desktop. It applies `.gitignore` for you, so your collected contact details and your secrets stay off GitHub automatically. The website's drag-and-drop uploader does **not** do this.

1. Create an account at [github.com](https://github.com).
2. Install [GitHub Desktop](https://desktop.github.com) and sign in with that account.
3. In GitHub Desktop: **File > New repository**.
   - Name: `societyscout`
   - Local path: the folder that *contains* your SocietyScout folder
   - Leave "Initialize with a README" unticked, and Git ignore and Licence set to None
   - Click **Create repository**

   If your folder is called something else, rename it to `societyscout` first so the two match.
4. GitHub Desktop now lists the files it will commit. **Check this list before going further.** It should show `app.py`, `scraper.py`, `requirements.txt`, `uk_universities.csv`, `universities_example.txt`, `README.md`, `DEPLOY.md`, `.gitignore`, and from `.streamlit` only `config.toml` and `secrets.toml.example`.

   If you see `data/`, `exports/`, `.venv/` or `secrets.toml`, stop: `.gitignore` isn't in the folder. Copy it in from the download and the list will correct itself.
5. Bottom left, type a summary such as `SocietyScout first version`, then click **Commit to main**.
6. Top of the window, click **Publish repository**. **Tick "Keep this code private"**, then publish.

Done. From then on, every time you change a file, GitHub Desktop shows the change; write a summary, click **Commit to main**, then **Push origin**.

### If you prefer the command line

```
cd path/to/societyscout
git init
git add .
git status          # check no data/, exports/ or secrets.toml appears
git commit -m "SocietyScout first version"
```

Then create an empty private repo on github.com and follow the two `git remote add` / `git push` lines it shows you.

### What must never be committed

- `data/` and `exports/` — thousands of students' names and emails. On GitHub that's a copy you no longer control, and a personal data breach under UK GDPR if the repo is ever public or shared.
- `.streamlit/secrets.toml` — your contact settings and Brave API key, which is billable to your card. Only `secrets.toml.example` belongs in the repo.
- `.venv/` — hundreds of megabytes of installed libraries, rebuilt automatically from `requirements.txt`.

If something sensitive does get committed, deleting it in a later commit is not enough: it stays in the repository's history. Delete the repository, start a fresh one, and rotate the API key if that was what leaked.

## Step 2: deploy it

1. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with GitHub. Allow access to private repos when asked.
2. Click **Create app**, then **Deploy a public app from GitHub**.
3. Fill in:
   - Repository: `your-username/societyscout`
   - Branch: `main`
   - Main file path: `app.py`
4. Before deploying, open **Advanced settings** and paste into **Secrets**:

```toml
SOCIETYSCOUT_CONTACT = "outreach@your-brand.co.uk"
SOCIETYSCOUT_CLOUD = "true"
SOCIETYSCOUT_DATA_DIR = "/tmp/societyscout/data"
SOCIETYSCOUT_EXPORT_DIR = "/tmp/societyscout/exports"
# BRAVE_API_KEY = "your-key"   # see step 4
```

5. Click **Deploy**. The first build takes a few minutes.

## Step 3: make it private

In your app's settings on share.streamlit.io, open **Sharing**, switch it to private, and invite your team by email. The free plan allows one private app.

## Step 4: searching by university name, online

The free search this app uses blocks traffic from datacentres, so searching by name will often fail on a hosted server, even though it works on your laptop. Two ways round it:

- **Paste the societies page** into the Search tab instead of typing a name. This works everywhere, is faster, and the app remembers the page for next time.
- **Add a Brave Search API key.** Get one at [brave.com/search/api](https://brave.com/search/api/). As of mid-2026 it gives $5 of free credit a month, around 1,000 searches, and needs a card on file. Add it to your app's secrets as `BRAVE_API_KEY`, then name-searching works online too.

## Step 5: updating it later

Edit the files in GitHub and the app rebuilds itself within a minute. Remember that every rebuild clears the hosted data, so download your CSV first.

## What to expect online

| | On your laptop | Hosted on Streamlit |
|---|---|---|
| Data kept between sessions | Yes | No: download and re-upload |
| Search by university name | Works | Needs a Brave key |
| Paste a societies page | Works | Works |
| Add by hand, edit, download | Works | Works |
| All 166 UK universities | Yes, run it overnight | A few at a time only |

The whole-UK run is the one thing the free hosting can't really do. Hosted apps get about 1 GB of memory and are cut off if a single action runs too long, and a full run takes hours. The app caps the online batch at 25 and defaults to 3. Run the big sweep on a laptop, then upload the resulting CSV to the hosted app so the team can use it.

## If you outgrow the free tier

The change that matters is a real database instead of CSV files, so data survives restarts and several people can use it at once. A hosted Postgres (Supabase and Neon both have free tiers) plus paid hosting would do it. Worth doing once the master list is big enough that losing it would hurt; not before.
